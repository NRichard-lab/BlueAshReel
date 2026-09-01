from __future__ import annotations

import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session, noload, selectinload
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from app.config import AppConfig
from app.models import (
    Episode,
    Library,
    LibraryPath,
    LocalArtwork,
    MediaFile,
    MediaItem,
    Season,
    Series,
    UserLibrary,
    VideoStream,
    WatchProgress,
)


def permitted_library(user_id: str) -> ColumnElement[bool]:
    return MediaItem.library_id.in_(
        select(UserLibrary.library_id).join(Library).where(UserLibrary.user_id == user_id, Library.enabled.is_(True))
    )


def file_available() -> ColumnElement[bool]:
    direct = exists(
        select(MediaFile.id)
        .join(LibraryPath)
        .where(
            MediaFile.media_item_id == MediaItem.id,
            MediaFile.available.is_(True),
            MediaFile.analysis_error.is_(None),
            LibraryPath.enabled.is_(True),
        )
    )
    descendants = exists(
        select(Series.id)
        .join(Season, Season.series_id == Series.id)
        .join(Episode, Episode.season_id == Season.id)
        .join(MediaFile, MediaFile.media_item_id == Episode.media_item_id)
        .join(LibraryPath, LibraryPath.id == MediaFile.library_path_id)
        .where(
            Series.media_item_id == MediaItem.id,
            MediaFile.available.is_(True),
            MediaFile.analysis_error.is_(None),
            LibraryPath.enabled.is_(True),
        )
    )
    return direct | descendants


def load_item(db: Session, user_id: str, media_id: str) -> MediaItem:
    item = db.scalar(
        select(MediaItem).options(noload(MediaItem.files)).where(MediaItem.id == media_id, permitted_library(user_id))
    )
    if item is None:
        raise HTTPException(404, "Media not found in your available libraries")
    return item


def strict_local_file(root: Path, relative: str, config: AppConfig) -> Path:
    """Reject every link/reparse component and nonregular file; never take browser paths."""
    parts = PurePosixPath(relative.replace("\\", "/"))
    if (
        not relative
        or parts.is_absolute()
        or any(p in ("..", ".") or ":" in p for p in parts.parts)
        or "\x00" in relative
    ):
        raise HTTPException(404, "Local source is unavailable")
    try:
        root = root.absolute()
        for component in [*reversed(root.parents), root]:
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("linked root")
        canonical = root.resolve(strict=True)
        if not any(canonical.is_relative_to(allowed) for allowed in config.allowed_media_roots):
            raise OSError("outside media roots")
        candidate = canonical
        for part in parts.parts:
            candidate = candidate / part
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("linked source")
        if not stat.S_ISREG(candidate.stat().st_mode) or not candidate.resolve().is_relative_to(canonical):
            raise OSError("not regular")
        return candidate
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(404, "Local source is unavailable; ask an administrator to rescan") from exc


def authorized_file(db: Session, user_id: str, file_id: str, config: AppConfig) -> tuple[MediaFile, Path]:
    file = db.scalar(
        select(MediaFile)
        .join(MediaItem)
        .join(LibraryPath)
        .where(
            MediaFile.id == file_id,
            permitted_library(user_id),
            MediaFile.available.is_(True),
            LibraryPath.enabled.is_(True),
            MediaFile.analysis_error.is_(None),
        )
        .options(
            selectinload(MediaFile.video_streams),
            selectinload(MediaFile.audio_streams),
            selectinload(MediaFile.subtitle_streams),
            selectinload(MediaFile.library_path),
        )
    )
    if file is None:
        raise HTTPException(404, "Media file is unavailable")
    source = strict_local_file(Path(file.library_path.canonical_path), file.relative_path, config)
    info = source.stat()
    if info.st_size != file.size_bytes or info.st_mtime_ns != file.modified_ns:
        raise HTTPException(409, "Local source changed; ask an administrator to rescan")
    return file, source


def safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    # Embedded track labels can be untrusted metadata, including absolute paths.
    if re.search(r"(?:[A-Za-z]:[\\/]|/[^\s/]+/|\\\\)", value):
        return None
    return "".join(c for c in value if c.isprintable())[:200]


def primary_video_height() -> ScalarSelect[int | None]:
    return (
        select(VideoStream.height)
        .where(VideoStream.media_file_id == MediaFile.id)
        .order_by(VideoStream.stream_index)
        .limit(1)
        .correlate(MediaFile)
        .scalar_subquery()
    )


def cards(db: Session, user_id: str, items: list[MediaItem], resolution: int | None = None) -> list[dict[str, Any]]:
    ids = [item.id for item in items]
    height = primary_video_height()
    ranking = (
        select(
            MediaFile.id,
            func.row_number()
            .over(
                partition_by=MediaFile.media_item_id,
                order_by=(MediaFile.available.desc(), height.desc(), MediaFile.id),
            )
            .label("rank"),
        )
        .join(LibraryPath)
        .where(MediaFile.media_item_id.in_(ids), LibraryPath.enabled.is_(True), MediaFile.analysis_error.is_(None))
    )
    if resolution:
        ranking = ranking.where(
            MediaFile.available.is_(True),
            height >= resolution,
        )
    ranked = ranking.subquery()
    files = db.scalars(
        select(MediaFile)
        .where(MediaFile.id.in_(select(ranked.c.id).where(ranked.c.rank == 1)))
        .options(
            selectinload(MediaFile.video_streams), noload(MediaFile.audio_streams), noload(MediaFile.subtitle_streams)
        )
        .order_by(MediaFile.available.desc(), MediaFile.id)
    ).all()
    best: dict[str, MediaFile] = {}
    for candidate_file in files:
        if candidate_file.analysis_error is None:
            best.setdefault(candidate_file.media_item_id, candidate_file)
    progress = {
        p.media_item_id: p
        for p in db.scalars(
            select(WatchProgress).where(WatchProgress.user_id == user_id, WatchProgress.media_item_id.in_(ids))
        )
    }
    artwork = {
        media_id: artwork_id
        for media_id, artwork_id in db.execute(
            select(LocalArtwork.media_item_id, func.min(LocalArtwork.id))
            .join(LibraryPath)
            .where(
                LocalArtwork.media_item_id.in_(ids),
                LocalArtwork.artwork_type == "poster",
                LibraryPath.enabled.is_(True),
            )
            .group_by(LocalArtwork.media_item_id)
        )
    }
    episodes = {
        row[0]: row[1:]
        for row in db.execute(
            select(Episode.media_item_id, Season.season_number, Episode.episode_number, Series.media_item_id)
            .select_from(Episode)
            .join(Season, Episode.season_id == Season.id)
            .join(Series, Season.series_id == Series.id)
            .where(Episode.media_item_id.in_(ids))
        )
    }
    result = []
    available_shows = set(
        db.scalars(select(MediaItem.id).where(MediaItem.id.in_(ids), MediaItem.kind == "series", file_available()))
    )
    for item in items:
        file = best.get(item.id)
        video = min(file.video_streams, key=lambda stream: stream.stream_index) if file and file.video_streams else None
        state = progress.get(item.id)
        episode = episodes.get(item.id)
        result.append(
            {
                "id": item.id,
                "library_id": item.library_id,
                "kind": item.kind,
                "title": item.title,
                "year": item.year,
                "available": bool(file and file.available) or item.id in available_shows,
                "file_id": file.id if file and file.available else None,
                "duration_seconds": file.duration_seconds if file else None,
                "height": video.height if video else None,
                "poster_url": f"/api/v1/browse/artwork/{artwork[item.id]}" if item.id in artwork else None,
                "position_seconds": state.position_seconds if state else 0,
                "watched": state.watched if state else False,
                "completion": min(100, state.position_seconds / state.duration_seconds * 100)
                if state and state.duration_seconds
                else 0,
                "season_number": episode[0] if episode else None,
                "episode_number": episode[1] if episode else None,
                "show_id": episode[2] if episode else None,
            }
        )
    return result


def progress_join(user_id: str) -> ColumnElement[bool]:
    return and_(WatchProgress.media_item_id == MediaItem.id, WatchProgress.user_id == user_id)
