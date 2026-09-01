from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

import app.services.scanner as scanner_service
from app.models import (
    ApplicationSetting,
    BackgroundJob,
    BackgroundJobEvent,
    Library,
    LibraryPath,
    LocalArtwork,
    MediaFile,
    MediaItem,
    ScanLock,
    utcnow,
)
from app.services.ffprobe import ProbeResult, ProbeStream
from app.services.jobs import (
    ScanAlreadyRunning,
    claim_next_job,
    enqueue_scan,
    fail_job,
    recover_stale_jobs,
)
from app.services.scanner import ScanFailed, run_scan


def probe_result() -> ProbeResult:
    return ProbeResult(
        container="matroska",
        duration_seconds=60.0,
        bitrate=1_000_000,
        embedded_title=None,
        video=(
            ProbeStream(
                stream_index=0,
                codec="h264",
                language=None,
                title=None,
                details={
                    "width": 1280,
                    "height": 720,
                    "bitrate": 900_000,
                    "frame_rate": 24.0,
                    "channels": None,
                    "channel_layout": None,
                    "forced": False,
                    "hearing_impaired": False,
                },
            ),
        ),
        audio=(),
        subtitles=(),
    )


def make_library(context, name: str = "Fixture") -> str:
    library_dir = context.media_root / name
    library_dir.mkdir(exist_ok=True)
    with context.session_factory() as db:
        library = Library(name=name, library_type="movies")
        db.add(library)
        db.flush()
        db.add(LibraryPath(library_id=library.id, canonical_path=str(library_dir)))
        db.commit()
        return library.id


def run_queued_scan(context, library_id: str, monkeypatch, mode: str = "changed") -> BackgroundJob:
    monkeypatch.setattr("app.services.scanner.run_ffprobe", lambda *_args: (probe_result(), 3))
    with context.session_factory() as db:
        enqueue_scan(db, library_id, mode)
        db.commit()
        job = claim_next_job(db, context.config, "test-worker")
        assert job is not None
        run_scan(db, job, context.config)
        return job


def test_scan_concurrency_lock_and_cancellation(context) -> None:
    library_id = make_library(context)
    with context.session_factory() as db:
        first = enqueue_scan(db, library_id, "changed")
        db.commit()
        with pytest.raises(ScanAlreadyRunning) as duplicate:
            enqueue_scan(db, library_id, "full")
        assert duplicate.value.job_id == first.id
        assert db.scalar(select(func.count()).select_from(ScanLock)) == 1


def test_changed_scan_skips_ffprobe_and_full_refreshes_local_name(context, monkeypatch) -> None:
    library_id = make_library(context)
    source = context.media_root / "Fixture" / "Old.Name.2020.mkv"
    source.write_bytes(b"fixture, not copyrighted media")
    calls: list[Path] = []

    def fake_probe(_executable, path, _timeout):
        calls.append(path)
        return probe_result(), 2

    monkeypatch.setattr("app.services.scanner.run_ffprobe", fake_probe)
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        first = claim_next_job(db, context.config, "worker")
        assert first is not None
        run_scan(db, first, context.config)
        assert first.scan is not None and first.scan.missing_files == 0
        first_file = db.scalar(select(MediaFile))
        assert first_file is not None and first_file.available is True
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        assert item is not None and item.title == "Old Name"
    assert len(calls) == 1

    renamed = source.with_name("New.Name.2020.mkv")
    source.rename(renamed)
    with context.session_factory() as db:
        # A changed scan sees a renamed path as a new file, while a subsequent full scan
        # refreshes local naming without re-probing that unchanged new path.
        enqueue_scan(db, library_id, "changed")
        db.commit()
        second = claim_next_job(db, context.config, "worker")
        assert second is not None
        run_scan(db, second, context.config)
        before_full = len(calls)
        enqueue_scan(db, library_id, "full")
        db.commit()
        full = claim_next_job(db, context.config, "worker")
        assert full is not None
        run_scan(db, full, context.config)
        assert full.scan is not None and full.scan.unchanged_files == 1
        assert full.scan.missing_files == 0
        available_files = db.scalar(select(func.count()).select_from(MediaFile).where(MediaFile.available.is_(True)))
        assert available_files == 1
    assert len(calls) == before_full


def test_changed_file_reanalyzed_and_missing_history_retained(context, monkeypatch) -> None:
    library_id = make_library(context)
    source = context.media_root / "Fixture" / "Movie.2024.mkv"
    source.write_bytes(b"one")
    calls = 0

    def fake_probe(*_args):
        nonlocal calls
        calls += 1
        return probe_result(), 1

    monkeypatch.setattr("app.services.scanner.run_ffprobe", fake_probe)
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        job = claim_next_job(db, context.config, "worker")
        assert job is not None
        run_scan(db, job, context.config)
    source.write_bytes(b"two and changed size")
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        job = claim_next_job(db, context.config, "worker")
        assert job is not None
        run_scan(db, job, context.config)
    assert calls == 2
    source.unlink()
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        job = claim_next_job(db, context.config, "worker")
        assert job is not None
        run_scan(db, job, context.config)
        media_file = db.scalar(select(MediaFile))
        assert media_file is not None and media_file.available is False
        assert media_file.missing_since is not None
        assert db.scalar(select(func.count()).select_from(MediaFile)) == 1


def test_stale_scan_safely_restarts_counters_and_renews_lease(context, monkeypatch) -> None:
    library_id = make_library(context)
    directory = context.media_root / "Fixture"
    (directory / "One.2024.mkv").write_bytes(b"one")
    (directory / "Two.2024.mkv").write_bytes(b"two")
    observed_leases = []

    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        interrupted = claim_next_job(db, context.config, "old-worker")
        assert interrupted is not None and interrupted.scan is not None
        interrupted.scan.discovered_files = 99
        interrupted.scan.processed_files = 50
        interrupted.scan.checkpoint = {"processed": 99}
        interrupted.heartbeat_at = utcnow() - timedelta(minutes=5)
        interrupted.lease_expires_at = utcnow() + timedelta(minutes=2)
        db.commit()

        # A durable lease is authoritative even if the informational heartbeat is old.
        assert recover_stale_jobs(db, context.config) == (0, 0)
        interrupted.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        recovered, failed = recover_stale_jobs(db, context.config)
        assert (recovered, failed) == (1, 0)
        resumed = claim_next_job(db, context.config, "new-worker")
        assert resumed is not None and resumed.attempts == 2

        def fake_probe(*_args):
            observed_leases.append(resumed.lease_expires_at)
            return probe_result(), 1

        monkeypatch.setattr("app.services.scanner.run_ffprobe", fake_probe)
        run_scan(db, resumed, context.config)
        assert resumed.scan is not None
        assert resumed.scan.discovered_files == 2
        assert resumed.scan.processed_files == 2
        assert resumed.status == "succeeded"
        assert (
            db.scalar(
                select(func.count())
                .select_from(BackgroundJobEvent)
                .where(BackgroundJobEvent.event_type == "restart_from_beginning")
            )
            == 1
        )
    assert all(lease is not None for lease in observed_leases)


def test_restarted_scan_clears_committed_seen_markers_before_missing_reconciliation(context, monkeypatch) -> None:
    library_id = make_library(context)
    directory = context.media_root / "Fixture"
    for name in ("One.2024.mkv", "Two.2024.mkv", "Three.2024.mkv"):
        (directory / name).write_bytes(name.encode())

    monkeypatch.setattr("app.services.scanner.run_ffprobe", lambda *_args: (probe_result(), 1))
    original_discovery = scanner_service.discover_media_files
    committed_relatives: list[str] = []

    def interrupted_discovery(root, config):
        for index, discovered in enumerate(original_discovery(root, config)):
            committed_relatives.append(discovered[1])
            yield discovered
            if index == 1:
                raise ScanFailed("simulated interruption after a committed batch")

    monkeypatch.setattr("app.services.scanner.discover_media_files", interrupted_discovery)
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        first_attempt = claim_next_job(db, context.config, "first-worker")
        assert first_attempt is not None and first_attempt.scan is not None
        with pytest.raises(ScanFailed):
            run_scan(db, first_attempt, context.config)
        fail_job(db, first_attempt, "simulated interruption")
        assert len(committed_relatives) == 2
        assert (
            db.scalar(
                select(func.count()).select_from(MediaFile).where(MediaFile.last_seen_scan_id == first_attempt.scan.id)
            )
            == 2
        )
        first_attempt.available_at = utcnow() - timedelta(seconds=1)
        db.commit()

        removed_relative = committed_relatives[0]
        (directory / Path(removed_relative)).unlink()
        monkeypatch.setattr("app.services.scanner.discover_media_files", original_discovery)
        retry = claim_next_job(db, context.config, "replacement-worker")
        assert retry is not None and retry.id == first_attempt.id and retry.attempts == 2
        run_scan(db, retry, context.config)

        removed = db.scalar(select(MediaFile).where(MediaFile.relative_path == removed_relative))
        assert removed is not None and removed.available is False
        assert removed.missing_since is not None
        assert retry.scan is not None and retry.scan.missing_files == 1


def test_worker_poll_recovers_lease_that_expires_after_replacement_start(context) -> None:
    library_id = make_library(context)
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        interrupted = claim_next_job(db, context.config, "stopped-worker")
        assert interrupted is not None
        interrupted.heartbeat_at = utcnow()
        interrupted.lease_expires_at = utcnow() + timedelta(minutes=2)
        db.commit()

        # The replacement starts while the lease is still valid and correctly does not claim it.
        assert claim_next_job(db, context.config, "replacement-worker") is None
        assert db.get(BackgroundJob, interrupted.id).status == "running"

        # On a later poll, after expiry, claim_next_job performs recovery and claims the
        # same durable job without another worker restart.
        interrupted = db.get(BackgroundJob, interrupted.id)
        assert interrupted is not None
        interrupted.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
        resumed = claim_next_job(db, context.config, "replacement-worker")
        assert resumed is not None and resumed.id == interrupted.id
        assert resumed.status == "running" and resumed.attempts == 2


def test_traversal_error_does_not_mark_existing_file_missing(context, monkeypatch) -> None:
    library_id = make_library(context)
    source = context.media_root / "Fixture" / "Movie.2024.mkv"
    source.write_bytes(b"one")
    run_queued_scan(context, library_id, monkeypatch)

    def broken_walk(*_args, **kwargs):
        onerror = kwargs["onerror"]
        onerror(PermissionError("private path must not escape"))
        yield from ()

    monkeypatch.setattr("app.services.scanner.os.walk", broken_walk)
    with context.session_factory() as db:
        enqueue_scan(db, library_id, "changed")
        db.commit()
        job = claim_next_job(db, context.config, "worker")
        assert job is not None
        with pytest.raises(ScanFailed):
            run_scan(db, job, context.config)


def test_failed_changed_analysis_is_retried_on_next_scan(context, monkeypatch) -> None:
    library_id = make_library(context)
    source = context.media_root / "Fixture" / "Retry.2024.mkv"
    source.write_bytes(b"first")
    run_queued_scan(context, library_id, monkeypatch)
    source.write_bytes(b"changed and larger")
    attempts = 0

    def fail_once(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            from app.services.ffprobe import FFprobeError

            raise FFprobeError("fixture failure")
        return probe_result(), 1

    monkeypatch.setattr("app.services.scanner.run_ffprobe", fail_once)
    for attempt_number in range(2):
        with context.session_factory() as db:
            enqueue_scan(db, library_id, "changed")
            db.commit()
            job = claim_next_job(db, context.config, "worker")
            assert job is not None
            run_scan(db, job, context.config)
            if attempt_number == 0:
                media_file = db.scalar(select(MediaFile))
                assert media_file is not None
                assert media_file.analysis_error == "Local media analysis failed"
                assert media_file.analyzed_at is None
                assert media_file.container is None
                assert len(media_file.video_streams) == 0
    assert attempts == 2
    with context.session_factory() as db:
        media_file = db.scalar(select(MediaFile))
        assert media_file is not None and media_file.analysis_error is None

    with context.session_factory() as db:
        media_file = db.scalar(select(MediaFile))
        assert media_file is not None and media_file.available is True


def test_duplicate_episode_files_share_episode_identity(context, monkeypatch) -> None:
    library_id = make_library(context, "Shows")
    with context.session_factory() as db:
        library = db.get(Library, library_id)
        assert library is not None
        library.library_type = "tv"
        db.commit()
    directory = context.media_root / "Shows"
    (directory / "Show.S01E01.1080p.mkv").write_bytes(b"one")
    (directory / "Show.S01E01.720p.mp4").write_bytes(b"two")
    (directory / "poster.jpg").write_bytes(b"local artwork fixture")
    run_queued_scan(context, library_id, monkeypatch)
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MediaFile)) == 2
        assert db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.kind == "episode")) == 1
        assert db.scalar(select(func.count()).select_from(LocalArtwork)) == 1

    for child in directory.iterdir():
        child.unlink()
    run_queued_scan(context, library_id, monkeypatch)
    with context.session_factory() as db:
        series_item = db.scalar(select(MediaItem).where(MediaItem.kind == "series"))
        assert series_item is not None and series_item.available is False


def test_full_scan_reconciles_vanished_sidecars_for_shared_item_across_paths(context, monkeypatch) -> None:
    directories = [context.media_root / name for name in ("Shows-A", "Shows-B")]
    for directory in directories:
        directory.mkdir()
        (directory / "Show.S01E01.mkv").write_bytes(b"media source")
        (directory / "poster.jpg").write_bytes(b"poster source")
        (directory / "fanart.jpg").write_bytes(b"background source")
        (directory / "Show.S01E01.nfo").write_bytes(b"nfo source")

    with context.session_factory() as db:
        library = Library(name="Shared show", library_type="tv")
        db.add(library)
        db.flush()
        paths = [LibraryPath(library_id=library.id, canonical_path=str(directory)) for directory in directories]
        db.add_all(paths)
        db.commit()
        library_id = library.id
        removed_path_id, retained_path_id = (item.id for item in paths)

    run_queued_scan(context, library_id, monkeypatch)
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MediaFile)) == 2
        assert db.scalar(select(func.count()).select_from(MediaItem).where(MediaItem.kind == "episode")) == 1
        assert db.scalar(select(func.count()).select_from(LocalArtwork)) == 6

    for name in ("poster.jpg", "fanart.jpg", "Show.S01E01.nfo"):
        (directories[0] / name).unlink()
    run_queued_scan(context, library_id, monkeypatch, mode="full")

    with context.session_factory() as db:
        artwork = db.scalars(select(LocalArtwork).order_by(LocalArtwork.artwork_type)).all()
        assert len(artwork) == 3
        assert {item.artwork_type for item in artwork} == {"poster", "background", "nfo"}
        assert {item.library_path_id for item in artwork} == {retained_path_id}
        assert all(item.library_path_id != removed_path_id for item in artwork)
    assert all((directory / "Show.S01E01.mkv").read_bytes() == b"media source" for directory in directories)
    assert (directories[1] / "poster.jpg").read_bytes() == b"poster source"
    assert (directories[1] / "fanart.jpg").read_bytes() == b"background source"
    assert (directories[1] / "Show.S01E01.nfo").read_bytes() == b"nfo source"


def test_database_scanner_extension_setting_controls_runtime_discovery(context, monkeypatch) -> None:
    library_id = make_library(context)
    (context.media_root / "Fixture" / "Local.Video.custom").write_bytes(b"custom fixture")
    with context.session_factory() as db:
        db.add(ApplicationSetting(key="scanner.extensions", value=[".custom"]))
        db.commit()
    run_queued_scan(context, library_id, monkeypatch)
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MediaFile)) == 1


def test_disabling_library_path_marks_its_files_unavailable(owner_context, monkeypatch) -> None:
    context, csrf = owner_context
    directory = context.media_root / "Disable"
    directory.mkdir()
    (directory / "Movie.2024.mkv").write_bytes(b"fixture")
    response = context.client.post(
        "/api/v1/libraries",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Disable", "library_type": "movies", "paths": [str(directory)]},
    )
    assert response.status_code == 201
    library_id = response.json()["id"]
    path_id = response.json()["paths"][0]["id"]
    run_queued_scan(context, library_id, monkeypatch)
    media = context.client.get(f"/api/v1/media?library_id={library_id}")
    assert media.status_code == 200
    stream = media.json()["items"][0]["files"][0]["video"][0]
    assert (stream["codec"], stream["width"], stream["height"]) == ("h264", 1280, 720)
    latest_job = context.client.get(f"/api/v1/jobs?page=1&page_size=1&library_id={library_id}").json()
    assert latest_job["total"] == 1
    assert latest_job["items"][0]["scan"]["library_id"] == library_id
    removed = context.client.delete(f"/api/v1/libraries/{library_id}/paths/{path_id}", headers={"X-CSRF-Token": csrf})
    assert removed.status_code == 204
    with context.session_factory() as db:
        media_file = db.scalar(select(MediaFile))
        assert media_file is not None and media_file.available is False
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        assert item is not None and item.available is False


def test_disabling_library_bulk_marks_files_and_items_unavailable_with_autoflush_off(
    owner_context, monkeypatch
) -> None:
    context, csrf = owner_context
    directory = context.media_root / "Disable-library"
    directory.mkdir()
    (directory / "Movie.2024.mkv").write_bytes(b"fixture")
    created = context.client.post(
        "/api/v1/libraries",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Disable library", "library_type": "movies", "paths": [str(directory)]},
    )
    assert created.status_code == 201
    library_id = created.json()["id"]
    run_queued_scan(context, library_id, monkeypatch)

    disabled = context.client.patch(
        f"/api/v1/libraries/{library_id}",
        headers={"X-CSRF-Token": csrf},
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["available_file_count"] == 0
    with context.session_factory() as db:
        media_file = db.scalar(select(MediaFile))
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        assert media_file is not None and media_file.available is False
        assert media_file.missing_since is not None
        assert item is not None and item.available is False


def test_library_mutation_is_rejected_while_scan_lock_is_active(owner_context) -> None:
    context, csrf = owner_context
    directory = context.media_root / "Locked"
    directory.mkdir()
    created = context.client.post(
        "/api/v1/libraries",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Locked", "library_type": "other", "paths": [str(directory)]},
    ).json()
    with context.session_factory() as worker_db:
        job = enqueue_scan(worker_db, created["id"], "changed")
        worker_db.commit()
    removed = context.client.delete(
        f"/api/v1/libraries/{created['id']}/paths/{created['paths'][0]['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 409
    assert removed.json()["error"]["fields"]["existing_job_id"] == job.id
    settings = context.client.patch(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={"scan_extensions": [".mkv"]},
    )
    assert settings.status_code == 409
    with context.session_factory() as db:
        library_path = db.get(LibraryPath, created["paths"][0]["id"])
        assert library_path is not None and library_path.enabled is True
