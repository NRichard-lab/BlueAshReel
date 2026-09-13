from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from test_jobs_and_scanner import make_library

from app.models import BackgroundJob, Library
from app.services.automatic_scanning import record_changes, scheduled_slot, tick
from app.services.library_settings import Schedule, read_library_settings, update_library_settings


def test_defaults_preserve_existing_installation(context):
    with context.session_factory() as db:
        value = read_library_settings(db)
        assert not value.scan_policy.automatic_scanning
        assert not value.scan_policy.scan_on_change
        assert value.scan_policy.schedule.frequency == "off"
        assert not value.scan_policy.empty_trash_after_scan
        assert value.scan_analysis.analyze_audio_tracks
        assert value.scan_analysis.analyze_subtitle_tracks
        assert not value.scan_analysis.video_preview_thumbnails
        assert not value.scan_analysis.chapter_thumbnails


def test_partial_update_and_session_reload(context):
    with context.session_factory() as db:
        update_library_settings(db, {"scan_policy": {"schedule": {"frequency": "weekly", "time": "14:25"}}})
        update_library_settings(
            db,
            {
                "scan_policy": {"automatic_scanning": False, "scan_on_change": True},
                "scan_analysis": {"analyze_audio_tracks": False, "analyze_subtitle_tracks": False},
            },
        )
    with context.session_factory() as db:
        value = read_library_settings(db)
        assert value.scan_policy.schedule.time == "14:25"
        assert value.scan_policy.schedule.frequency == "weekly"
        assert not value.scan_policy.automatic_scanning
        assert value.scan_policy.scan_on_change
        assert not value.scan_analysis.analyze_audio_tracks
        assert not value.scan_analysis.analyze_subtitle_tracks


@pytest.mark.parametrize(
    "patch",
    [
        {"scan_policy": {"schedule": {"time": "24:00"}}},
        {"scan_policy": {"schedule": {"weekday": 7}}},
        {"scan_policy": {"schedule": {"frequency": "cron"}}},
        {"scan_policy": {"automatic_scanning": "false"}},
        {"scan_policy": {"empty_trash_after_scan": True}},
        {"scan_analysis": {"video_preview_thumbnails": True}},
        {"scan_analysis": {"chapter_thumbnails": True}},
        {"scan_analysis": {"unknown": True}},
        {"schema_version": 2},
    ],
)
def test_invalid_or_unsupported_settings_never_persist(context, patch):
    with context.session_factory() as db:
        before = read_library_settings(db)
        with pytest.raises(ValueError):
            update_library_settings(db, patch)
        assert read_library_settings(db) == before


def test_scheduler_uses_local_time_and_deduplicates(context):
    library_id = make_library(context)
    with context.session_factory() as db:
        update_library_settings(
            db, {"scan_policy": {"automatic_scanning": True, "schedule": {"frequency": "daily", "time": "03:00"}}}
        )
    now = datetime(2026, 9, 12, 23, tzinfo=ZoneInfo("America/Denver"))
    tick(context.session_factory, context.config, {}, now)
    tick(context.session_factory, context.config, {}, now)
    with context.session_factory() as db:
        jobs = db.scalars(select(BackgroundJob).where(BackgroundJob.job_type == "library_scan")).all()
        assert len(jobs) == 1
        assert jobs[0].payload == {"library_id": library_id, "mode": "changed", "trigger": "schedule"}


def test_disabled_library_and_master_switch_do_not_enqueue(context):
    library_id = make_library(context)
    with context.session_factory() as db:
        db.get(Library, library_id).enabled = False
        update_library_settings(
            db, {"scan_policy": {"automatic_scanning": True, "schedule": {"frequency": "daily", "time": "00:00"}}}
        )
    tick(context.session_factory, context.config, {})
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0
        db.get(Library, library_id).enabled = True
        update_library_settings(db, {"scan_policy": {"automatic_scanning": False}})
    tick(context.session_factory, context.config, {library_id: 0})
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0


def test_watcher_debounce_and_existing_lock_preserve_one_pending_scan(context):
    library_id = make_library(context)
    pending = {}
    with context.session_factory() as db:
        update_library_settings(db, {"scan_policy": {"automatic_scanning": True, "scan_on_change": True}})
    path = str(context.media_root / "Fixture" / "new.mkv")
    for _ in range(5):
        record_changes(context.session_factory, {(1, path)}, pending)
    assert len(pending) == 1 and pending[library_id] > time.monotonic()
    tick(context.session_factory, context.config, pending)
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0
    pending[library_id] = 0
    tick(context.session_factory, context.config, pending)
    pending[library_id] = 0
    tick(context.session_factory, context.config, pending)
    assert library_id in pending
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1


def test_dst_repeated_hour_and_weekday():
    schedule = Schedule(frequency="weekly", time="01:30", weekday=6)
    first = datetime(2026, 11, 1, 1, 45, tzinfo=ZoneInfo("America/Denver"), fold=0)
    assert scheduled_slot(schedule, first) == scheduled_slot(schedule, first.replace(fold=1))
    assert scheduled_slot(schedule, first.replace(day=2)) is None


def test_remote_contract_is_owner_only(owner_context):
    import uuid

    from fastapi import HTTPException
    from test_remote_media import call, setup_remote

    media, auth, _ = setup_remote(owner_context)
    assert call(media, auth, "settings.libraries.get")["schema_version"] == 1
    assert not call(media, auth, "settings.libraries.update", scan_analysis={"analyze_audio_tracks": False})[
        "scan_analysis"
    ]["analyze_audio_tracks"]
    viewer = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
    card = call(media, auth, "catalog.list")["items"][0]
    call(media, auth, "grants.set", user_id=viewer["user_id"], role="viewer", library_ids=[card["library_id"]])
    for op in ("settings.libraries.get", "settings.libraries.update"):
        with pytest.raises(HTTPException) as error:
            call(media, viewer, op)
        assert error.value.status_code == 403


def test_removal_preserves_source_and_folder_failure_is_atomic(owner_context):
    from test_remote_media import call, setup_remote

    media, auth, (_, _, _, source) = setup_remote(owner_context)
    library = call(media, auth, "libraries.list")["items"][0]
    with pytest.raises(ValueError):
        call(
            media, auth, "libraries.update", library_id=library["id"], name="Must not save", remove_path_ids=["invalid"]
        )
    assert call(media, auth, "libraries.list")["items"][0]["name"] == library["name"]
    assert call(media, auth, "libraries.delete", library_id=library["id"])["removed"]
    assert source.is_file()


def test_analysis_switches_control_new_stream_rows(context):
    from app.models import AudioStream, MediaFile, MediaItem, SubtitleStream
    from app.services.ffprobe import ProbeResult, ProbeStream
    from app.services.library_settings import ScanAnalysis
    from app.services.scanner import _replace_streams

    library_id = make_library(context)
    with context.session_factory() as db:
        item = MediaItem(library_id=library_id, kind="movie", title="Fixture", sort_title="fixture")
        db.add(item)
        db.flush()
        file = MediaFile(
            media_item_id=item.id,
            library_path_id=db.get(Library, library_id).paths[0].id,
            relative_path="fixture.mkv",
            size_bytes=1,
            modified_ns=1,
            fingerprint="a" * 64,
        )
        db.add(file)
        db.flush()
        audio = ProbeStream(1, "aac", "en", None, {"channels": 2, "channel_layout": "stereo", "bitrate": 128000})
        sub = ProbeStream(2, "srt", "en", None, {"forced": False, "hearing_impaired": False})
        probe = ProbeResult("mkv", 1, 1, None, (), (audio,), (sub,))
        _replace_streams(db, file, probe, ScanAnalysis(analyze_audio_tracks=False, analyze_subtitle_tracks=False))
        db.flush()
        assert db.scalar(select(func.count()).select_from(AudioStream)) == 0
        assert db.scalar(select(func.count()).select_from(SubtitleStream)) == 0
        _replace_streams(db, file, probe)
        db.flush()
        assert db.scalar(select(func.count()).select_from(AudioStream)) == 1
        assert db.scalar(select(func.count()).select_from(SubtitleStream)) == 1
        _replace_streams(db, file, probe, ScanAnalysis(analyze_audio_tracks=False, analyze_subtitle_tracks=False))
        db.flush()
        assert db.scalar(select(func.count()).select_from(AudioStream)) == 1
