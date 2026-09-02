from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import (
    ApplicationSetting,
    AudioStream,
    BackgroundJob,
    BackgroundJobEvent,
    Episode,
    Library,
    LibraryPath,
    LocalArtwork,
    MediaFile,
    MediaItem,
    Movie,
    ScanJob,
    Season,
    Series,
    SubtitleStream,
    VideoStream,
    utcnow,
)
from app.services.ffprobe import FFprobeError, ProbeResult, run_ffprobe
from app.services.filename_parser import ParsedFilename, parse_filename
from app.services.jobs import release_scan_lock
from app.services.media_state import recompute_media_availability
from app.services.paths import UnsafeMediaPath, is_link_or_reparse, safe_discovered_file, validate_media_directory


class ScanCancelled(RuntimeError):
    pass


class ScanFailed(RuntimeError):
    pass


def fingerprint(relative_path: str, size_bytes: int, modified_ns: int) -> str:
    value = f"v1\0{relative_path.casefold()}\0{size_bytes}\0{modified_ns}".encode()
    return hashlib.sha256(value).hexdigest()


def discover_media_files(root: Path, config: AppConfig) -> Iterator[tuple[Path, str, os.stat_result]]:
    def traversal_error(error: OSError) -> None:
        raise ScanFailed("A media directory could not be read completely") from error

    for directory, names, files in os.walk(root, topdown=True, onerror=traversal_error, followlinks=False):
        names[:] = [
            name
            for name in names
            if name.casefold() not in config.ignored_directory_names
            and not is_link_or_reparse(Path(directory) / name)
        ]
        current = Path(directory)
        for filename in files:
            if Path(filename).suffix.casefold() not in config.supported_extensions:
                continue
            candidate = current / filename
            if is_link_or_reparse(candidate):
                continue
            try:
                safe_path = candidate.resolve(strict=True)
                safe_path.relative_to(root)
                if not safe_path.is_file():
                    continue
                info = safe_path.stat()
                relative = safe_path.relative_to(root).as_posix()
            except ValueError:
                # Symlinks or junctions which escape the configured root are ignored.
                continue
            except OSError as exc:
                # A disappearing or unreadable entry makes the traversal incomplete. Do not
                # proceed to missing-file marking in that case.
                raise ScanFailed("A media directory changed or became unreadable during scan") from exc
            yield safe_path, relative, info


def _create_media_item(db: Session, library: Library, parsed: ParsedFilename) -> MediaItem:
    if parsed.kind == "episode" and parsed.series_title:
        series_sort = parsed.series_title.casefold()
        series_item = db.scalar(
            select(MediaItem).where(
                MediaItem.library_id == library.id,
                MediaItem.kind == "series",
                MediaItem.sort_title == series_sort,
            )
        )
        if series_item is None:
            series_item = MediaItem(
                library_id=library.id,
                kind="series",
                title=parsed.series_title,
                sort_title=series_sort,
                match_confidence=parsed.confidence,
            )
            db.add(series_item)
            db.flush()
            series_record: Series | None = Series(media_item_id=series_item.id)
            db.add(series_record)
            db.flush()
        else:
            series_record = db.scalar(select(Series).where(Series.media_item_id == series_item.id))
            if series_record is None:
                series_record = Series(media_item_id=series_item.id)
                db.add(series_record)
                db.flush()
        assert series_record is not None
        season_number = parsed.season_number or 0
        season = db.scalar(
            select(Season).where(Season.series_id == series_record.id, Season.season_number == season_number)
        )
        if season is None:
            season = Season(series_id=series_record.id, season_number=season_number)
            db.add(season)
            db.flush()
        existing_episode = db.scalar(
            select(Episode).where(
                Episode.season_id == season.id,
                Episode.episode_number == (parsed.episode_number or 0),
                Episode.part_number == 1,
            )
        )
        if existing_episode is not None:
            existing_item = db.get(MediaItem, existing_episode.media_item_id)
            if existing_item is not None:
                existing_item.available = True
                return existing_item
        item = MediaItem(
            library_id=library.id,
            kind="episode",
            title=parsed.title,
            sort_title=parsed.title.casefold(),
            match_confidence=parsed.confidence,
        )
        db.add(item)
        db.flush()
        db.add(
            Episode(
                media_item_id=item.id,
                season_id=season.id,
                episode_number=parsed.episode_number or 0,
            )
        )
        return item

    kind = "movie" if parsed.kind == "movie" else "other"
    item = MediaItem(
        library_id=library.id,
        kind=kind,
        title=parsed.title,
        sort_title=parsed.title.casefold(),
        year=parsed.year,
        match_confidence=parsed.confidence,
    )
    db.add(item)
    db.flush()
    if kind == "movie":
        db.add(Movie(media_item_id=item.id))
    return item


def _replace_streams(db: Session, media_file: MediaFile, result: ProbeResult) -> None:
    db.execute(delete(VideoStream).where(VideoStream.media_file_id == media_file.id))
    db.execute(delete(AudioStream).where(AudioStream.media_file_id == media_file.id))
    db.execute(delete(SubtitleStream).where(SubtitleStream.media_file_id == media_file.id))
    for stream in result.video:
        db.add(
            VideoStream(
                media_file_id=media_file.id,
                stream_index=stream.stream_index,
                codec=stream.codec,
                width=stream.details["width"],
                height=stream.details["height"],
                bitrate=stream.details["bitrate"],
                frame_rate=stream.details["frame_rate"],
                language=stream.language,
                profile=stream.details.get("profile"),
                level=stream.details.get("level"),
                pixel_format=stream.details.get("pixel_format"),
                bit_depth=stream.details.get("bit_depth"),
            )
        )
    for stream in result.audio:
        db.add(
            AudioStream(
                media_file_id=media_file.id,
                stream_index=stream.stream_index,
                codec=stream.codec,
                channels=stream.details["channels"],
                channel_layout=stream.details["channel_layout"],
                bitrate=stream.details["bitrate"],
                language=stream.language,
                title=stream.title,
            )
        )
    for stream in result.subtitles:
        db.add(
            SubtitleStream(
                media_file_id=media_file.id,
                stream_index=stream.stream_index,
                codec=stream.codec,
                language=stream.language,
                title=stream.title,
                forced=stream.details["forced"],
                hearing_impaired=stream.details["hearing_impaired"],
            )
        )


ArtworkKey = tuple[str, str, str, str]


def _record_local_sidecars(
    db: Session,
    media_file: MediaFile,
    source: Path,
    root: Path,
) -> set[ArtworkKey]:
    observed: set[ArtworkKey] = set()
    candidates = {
        "poster": ("poster.jpg", "poster.png", f"{source.stem}-poster.jpg"),
        "background": ("fanart.jpg", "backdrop.jpg", "background.jpg"),
        "nfo": (f"{source.stem}.nfo",),
    }
    for artwork_type, names in candidates.items():
        for name in names:
            candidate = safe_discovered_file(source.parent / name, root)
            if candidate is None:
                continue
            stat = candidate.stat()
            relative = candidate.relative_to(root).as_posix()
            digest = fingerprint(relative, stat.st_size, stat.st_mtime_ns)
            key = (
                media_file.media_item_id,
                media_file.library_path_id,
                artwork_type,
                relative,
            )
            observed.add(key)
            existing = db.scalar(
                select(LocalArtwork).where(
                    LocalArtwork.media_item_id == media_file.media_item_id,
                    LocalArtwork.library_path_id == media_file.library_path_id,
                    LocalArtwork.artwork_type == artwork_type,
                    LocalArtwork.source_path == relative,
                )
            )
            if existing:
                existing.fingerprint = digest
            else:
                db.add(
                    LocalArtwork(
                        media_item_id=media_file.media_item_id,
                        library_path_id=media_file.library_path_id,
                        artwork_type=artwork_type,
                        source_path=relative,
                        fingerprint=digest,
                    )
                )
                # Autoflush is disabled in production; make the unique sidecar visible
                # before another file sharing this media item checks for it.
                db.flush()
            break
    return observed


def _reconcile_local_sidecars(
    db: Session,
    scanned_path_ids: list[str],
    observed: set[ArtworkKey],
) -> None:
    """Forget vanished sidecars after a complete full scan; never modify source files."""
    if not scanned_path_ids:
        return
    existing = db.scalars(select(LocalArtwork).where(LocalArtwork.library_path_id.in_(scanned_path_ids))).all()
    for artwork in existing:
        key = (
            artwork.media_item_id,
            artwork.library_path_id,
            artwork.artwork_type,
            artwork.source_path,
        )
        if key not in observed:
            db.delete(artwork)


def _check_cancelled(db: Session, job: BackgroundJob) -> None:
    db.refresh(job, attribute_names=["cancel_requested"])
    if job.cancel_requested:
        raise ScanCancelled("Scan cancellation requested")


def run_scan(db: Session, job: BackgroundJob, config: AppConfig) -> None:
    scan = db.scalar(select(ScanJob).where(ScanJob.job_id == job.id))
    if scan is None:
        raise ScanFailed("Scan job details are missing")
    library = db.get(Library, scan.library_id)
    if library is None or not library.enabled:
        raise ScanFailed("Library is missing or disabled")

    extension_setting = db.get(ApplicationSetting, "scanner.extensions")
    ignored_setting = db.get(ApplicationSetting, "scanner.ignored_directories")
    effective_config = config.model_copy(
        update={
            "scan_extensions": ",".join(extension_setting.value)
            if extension_setting and isinstance(extension_setting.value, list)
            else config.scan_extensions,
            "ignored_directories": ",".join(ignored_setting.value)
            if ignored_setting and isinstance(ignored_setting.value, list)
            else config.ignored_directories,
        }
    )

    # A persisted checkpoint means a worker stopped after committing a batch. Replaying
    # discovery from the beginning is deliberate: upserts are idempotent, unchanged files
    # skip FFprobe, and rebuilding counters avoids double-counting partial work.
    if scan.checkpoint or job.attempts > 1:
        library_path_ids = select(LibraryPath.id).where(LibraryPath.library_id == library.id)
        db.execute(
            update(MediaFile)
            .where(
                MediaFile.library_path_id.in_(library_path_ids),
                MediaFile.last_seen_scan_id == scan.id,
            )
            .values(last_seen_scan_id=None)
            .execution_options(synchronize_session=False)
        )
        scan.discovered_files = 0
        scan.processed_files = 0
        scan.unchanged_files = 0
        scan.missing_files = 0
        scan.error_count = 0
        scan.checkpoint = {}
        job.progress_current = 0
        db.add(
            BackgroundJobEvent(
                job_id=job.id,
                level="warning",
                event_type="restart_from_beginning",
                message="Interrupted scan safely restarted from discovery",
            )
        )
        db.commit()

    started = time.monotonic()
    completed_traversal = False
    scanned_path_ids: list[str] = []
    observed_artwork: set[ArtworkKey] = set()
    lease_seconds = max(config.job_stale_minutes * 60, config.ffprobe_timeout_seconds * 2 + 30)
    heartbeat_batch = max(
        1,
        min(
            config.scan_batch_size,
            max(1, lease_seconds // max(config.ffprobe_timeout_seconds * 2, 1)),
        ),
    )
    try:
        for library_path in list(library.paths):
            if not library_path.enabled:
                continue
            try:
                root = validate_media_directory(library_path.canonical_path, effective_config)
            except UnsafeMediaPath as exc:
                raise ScanFailed("A configured library directory is unavailable or unsafe") from exc
            scanned_path_ids.append(library_path.id)
            for source, relative, info in discover_media_files(root, effective_config):
                _check_cancelled(db, job)
                scan.discovered_files += 1
                digest = fingerprint(relative, info.st_size, info.st_mtime_ns)
                media_file = db.scalar(
                    select(MediaFile).where(
                        MediaFile.library_path_id == library_path.id,
                        MediaFile.relative_path == relative,
                    )
                )
                if (
                    media_file
                    and media_file.fingerprint == digest
                    and media_file.analyzed_at is not None
                    and media_file.analysis_error is None
                ):
                    media_file.available = True
                    media_file.missing_since = None
                    media_file.last_seen_scan_id = scan.id
                    media_file.media_item.available = True
                    scan.unchanged_files += 1
                    if scan.scan_mode == "full":
                        # Full scans refresh inexpensive local naming and sidecar state while
                        # still honoring the invariant that unchanged files do not run FFprobe.
                        parsed = parse_filename(relative, library.library_type)
                        media_file.media_item.title = parsed.title
                        media_file.media_item.sort_title = parsed.title.casefold()
                        media_file.media_item.year = parsed.year
                        media_file.media_item.match_confidence = parsed.confidence
                        observed_artwork.update(_record_local_sidecars(db, media_file, source, root))
                else:
                    parsed = parse_filename(relative, library.library_type)
                    if media_file is None:
                        item = _create_media_item(db, library, parsed)
                        db.flush()
                        media_file = MediaFile(
                            media_item_id=item.id,
                            library_path_id=library_path.id,
                            relative_path=relative,
                            size_bytes=info.st_size,
                            modified_ns=info.st_mtime_ns,
                            fingerprint=digest,
                        )
                        db.add(media_file)
                        db.flush()
                    media_file.size_bytes = info.st_size
                    media_file.modified_ns = info.st_mtime_ns
                    media_file.fingerprint = digest
                    media_file.available = True
                    media_file.missing_since = None
                    media_file.last_seen_scan_id = scan.id
                    media_file.media_item.available = True
                    try:
                        probe, duration_ms = run_ffprobe(
                            effective_config.ffprobe_path,
                            source,
                            effective_config.ffprobe_timeout_seconds,
                        )
                        media_file.container = probe.container
                        media_file.duration_seconds = probe.duration_seconds
                        media_file.bitrate = probe.bitrate
                        media_file.embedded_title = probe.embedded_title
                        media_file.analysis_duration_ms = duration_ms
                        media_file.analyzed_at = utcnow()
                        media_file.analysis_error = None
                        _replace_streams(db, media_file, probe)
                        observed_artwork.update(_record_local_sidecars(db, media_file, source, root))
                    except FFprobeError:
                        media_file.container = None
                        media_file.duration_seconds = None
                        media_file.bitrate = None
                        media_file.embedded_title = None
                        media_file.analyzed_at = None
                        media_file.analysis_duration_ms = None
                        media_file.analysis_error = "Local media analysis failed"
                        db.execute(delete(VideoStream).where(VideoStream.media_file_id == media_file.id))
                        db.execute(delete(AudioStream).where(AudioStream.media_file_id == media_file.id))
                        db.execute(delete(SubtitleStream).where(SubtitleStream.media_file_id == media_file.id))
                        scan.error_count += 1
                        if scan.scan_mode == "full":
                            observed_artwork.update(_record_local_sidecars(db, media_file, source, root))
                    scan.processed_files += 1

                job.progress_current = scan.discovered_files
                job.heartbeat_at = utcnow()
                job.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
                if scan.discovered_files % heartbeat_batch == 0:
                    scan.checkpoint = {
                        "library_path_id": library_path.id,
                        "processed": scan.discovered_files,
                    }
                    db.commit()
        completed_traversal = True
        _check_cancelled(db, job)

        # Production sessions disable autoflush. Persist every last_seen_scan_id before
        # querying for unseen rows or the just-discovered files select themselves as missing.
        db.flush()
        now = utcnow()
        missing_filter = (
            MediaFile.library_path_id.in_(scanned_path_ids),
            MediaFile.available.is_(True),
            or_(MediaFile.last_seen_scan_id != scan.id, MediaFile.last_seen_scan_id.is_(None)),
        )
        scan.missing_files = db.scalar(select(func.count()).select_from(MediaFile).where(*missing_filter)) or 0
        if scan.missing_files:
            db.execute(
                update(MediaFile)
                .where(*missing_filter)
                .values(available=False, missing_since=now)
                .execution_options(synchronize_session=False)
            )
        if scan.scan_mode == "full":
            _reconcile_local_sidecars(db, scanned_path_ids, observed_artwork)
        recompute_media_availability(db, library.id)
        library.last_successful_scan_at = now
        scan.duration_ms = int((time.monotonic() - started) * 1000)
        scan.checkpoint = {}
        job.status = "succeeded"
        job.progress_total = scan.discovered_files
        job.completed_at = now
        job.locked_by = None
        job.lease_expires_at = None
        release_scan_lock(db, job.id)
        db.add(
            BackgroundJobEvent(
                job_id=job.id,
                event_type="completed",
                message="Library scan completed",
                details={
                    "discovered": scan.discovered_files,
                    "processed": scan.processed_files,
                    "errors": scan.error_count,
                },
            )
        )
        db.commit()
    except ScanCancelled:
        job.status = "cancelled"
        job.completed_at = utcnow()
        job.locked_by = None
        job.lease_expires_at = None
        release_scan_lock(db, job.id)
        db.add(
            BackgroundJobEvent(job_id=job.id, level="warning", event_type="cancelled", message="Library scan cancelled")
        )
        db.commit()
    except Exception:
        db.rollback()
        if completed_traversal:
            # Missing-file state is only ever written after a complete traversal.
            pass
        raise
