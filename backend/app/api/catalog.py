from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session, noload, selectinload

from app.config import AppConfig, get_config
from app.database import get_db
from app.dependencies import Principal, current_principal, require_user_csrf
from app.home_schemas import HomeResponse
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
    WatchProgress,
    utcnow,
)
from app.services.catalog import (
    cards,
    file_available,
    load_item,
    permitted_library,
    primary_video_height,
    progress_join,
    safe_text,
    strict_local_file,
)
from app.services.home import home_catalog

router = APIRouter(prefix="/browse", tags=["viewer catalog"])


@router.get("/libraries")
def libraries(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = (
        select(Library.id, Library.name, Library.library_type)
        .join(UserLibrary)
        .where(UserLibrary.user_id == principal.user.id, Library.enabled.is_(True))
        .order_by(Library.name, Library.id)
    )
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size))
    return {
        "items": [dict(row._mapping) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(select(func.count()).select_from(query.subquery())),
    }


@router.get("/media")
def catalog(
    page: int = Query(1, ge=1, le=1000000),
    page_size: int = Query(24, ge=1, le=100),
    kind: Literal["movie", "series", "episode", "other"] | None = None,
    q: str = Query("", max_length=200),
    library_id: str | None = None,
    sort: Literal["title", "year", "added", "duration", "watch"] = "title",
    watched: bool | None = None,
    available: bool | None = None,
    resolution: int | None = Query(None, ge=1, le=10000),
    history: Literal["continue", "recent"] | None = None,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user_id = principal.user.id
    query = select(MediaItem).outerjoin(WatchProgress, progress_join(user_id)).where(permitted_library(user_id))
    if kind:
        query = query.where(MediaItem.kind == kind)
    if library_id:
        query = query.where(MediaItem.library_id == library_id)
    if watched is not None:
        query = query.where(func.coalesce(WatchProgress.watched, False) == watched)
    if available is not None:
        query = query.where(file_available() == available)
    if resolution:
        query = query.where(
            MediaItem.id.in_(
                select(MediaFile.media_item_id)
                .join(LibraryPath)
                .where(
                    primary_video_height() >= resolution,
                    MediaFile.available.is_(True),
                    MediaFile.analysis_error.is_(None),
                    LibraryPath.enabled.is_(True),
                )
            )
        )
    if history:
        query = query.where(WatchProgress.id.is_not(None))
        if history == "continue":
            query = query.where(WatchProgress.watched.is_(False), WatchProgress.position_seconds >= 5, file_available())
        else:
            query = query.where(WatchProgress.watched.is_(True))
    if q.strip():
        identifier = re.search(r"\bs(\d{1,3})(?:e(\d{1,4}))?\b", q, re.I)
        title_query = (q[: identifier.start()] + q[identifier.end() :]) if identifier else q
        tokens = re.findall(r"\w+", title_query, re.UNICODE)[:16]
        if not tokens and not identifier:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        match = " AND ".join('"' + token.replace('"', "") + '"*' for token in tokens)
        matched_ids = text(
            "SELECT m.id FROM media_items m JOIN media_search s ON m.rowid=s.rowid WHERE media_search MATCH :terms"
        ).bindparams(terms=match)
        show_episodes = (
            select(Episode.media_item_id).join(Season).join(Series).where(Series.media_item_id.in_(matched_ids))
        )
        criteria = [MediaItem.id.in_(matched_ids), MediaItem.id.in_(show_episodes)]
        if identifier:
            episode_query = select(Episode.media_item_id).join(Season).where(Season.season_number == int(identifier[1]))
            if identifier[2]:
                episode_query = episode_query.where(Episode.episode_number == int(identifier[2]))
            query = query.where(MediaItem.id.in_(episode_query))
        if tokens:
            query = query.where(or_(*criteria))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    duration = (
        select(func.max(MediaFile.duration_seconds)).where(MediaFile.media_item_id == MediaItem.id).scalar_subquery()
    )
    order = {
        "title": MediaItem.sort_title,
        "year": MediaItem.year.desc(),
        "added": MediaItem.created_at.desc(),
        "duration": duration.desc(),
        "watch": func.coalesce(WatchProgress.watched, False),
    }[sort]
    if history:
        order = WatchProgress.last_played_at.desc()
    items = list(
        db.scalars(
            query.options(noload(MediaItem.files))
            .order_by(order, MediaItem.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {"items": cards(db, user_id, items, resolution), "total": total, "page": page, "page_size": page_size}


@router.get("/home", response_model=HomeResponse)
def home(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict[str, Any]:
    return home_catalog(db, principal.user.id)


@router.get("/media/{media_id}")
def detail(
    media_id: str,
    file_page: int = Query(1, ge=1),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = load_item(db, principal.user.id, media_id)
    result = cards(db, principal.user.id, [item])[0]
    files = db.scalars(
        select(MediaFile)
        .join(LibraryPath)
        .where(MediaFile.media_item_id == item.id, LibraryPath.enabled.is_(True))
        .options(
            selectinload(MediaFile.video_streams),
            selectinload(MediaFile.audio_streams),
            selectinload(MediaFile.subtitle_streams),
        )
        .order_by((MediaFile.id == result["file_id"]).desc(), MediaFile.available.desc(), MediaFile.id)
        .offset((file_page - 1) * 10)
        .limit(10)
    ).all()
    result["files"] = [
        {
            "id": f.id,
            "available": f.available and f.analysis_error is None,
            "container": f.container,
            "duration_seconds": f.duration_seconds,
            "bitrate": f.bitrate,
            "video": [
                {
                    "index": v.stream_index,
                    "codec": v.codec,
                    "width": v.width,
                    "height": v.height,
                    "profile": v.profile,
                    "level": v.level,
                    "pixel_format": v.pixel_format,
                    "bit_depth": v.bit_depth,
                }
                for v in f.video_streams
            ],
            "audio": [
                {
                    "index": a.stream_index,
                    "codec": a.codec,
                    "channels": a.channels,
                    "language": safe_text(a.language),
                    "title": safe_text(a.title),
                }
                for a in f.audio_streams
            ],
            "subtitles": [
                {
                    "index": s.stream_index,
                    "codec": s.codec,
                    "language": safe_text(s.language),
                    "title": safe_text(s.title),
                    "text_supported": s.codec in ("subrip", "srt", "webvtt", "ass", "ssa", "mov_text"),
                }
                for s in f.subtitle_streams
            ],
            **(
                {"size_bytes": f.size_bytes, "analyzed_at": f.analyzed_at, "analysis_error": f.analysis_error}
                if "Owner" in {r.name for r in principal.user.roles}
                else {}
            ),
        }
        for f in files
    ]
    result["file_page"] = file_page
    result["file_page_size"] = 10
    result["file_total"] = db.scalar(
        select(func.count(MediaFile.id))
        .join(LibraryPath)
        .where(MediaFile.media_item_id == item.id, LibraryPath.enabled.is_(True))
    )
    art = db.scalar(
        select(LocalArtwork)
        .join(LibraryPath)
        .where(
            LocalArtwork.media_item_id == item.id,
            LocalArtwork.artwork_type == "background",
            LibraryPath.enabled.is_(True),
        )
    )
    result["background_url"] = f"/api/v1/browse/artwork/{art.id}" if art else None
    return result


@router.get("/shows/{media_id}/seasons")
def seasons(
    media_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    load_item(db, principal.user.id, media_id)
    rows = db.execute(
        select(Season.id, Season.season_number, func.count(Episode.media_item_id).label("episode_count"))
        .join(Series)
        .outerjoin(Episode)
        .where(Series.media_item_id == media_id)
        .group_by(Season.id)
        .order_by(Season.season_number)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [dict(row._mapping) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(select(func.count(Season.id)).join(Series).where(Series.media_item_id == media_id)),
    }


@router.get("/seasons/{season_id}/episodes")
def episodes(
    season_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    show_id = db.scalar(select(Series.media_item_id).join(Season).where(Season.id == season_id))
    load_item(db, principal.user.id, show_id or "")
    query = select(MediaItem).join(Episode).where(Episode.season_id == season_id, permitted_library(principal.user.id))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(
        db.scalars(
            query.options(noload(MediaItem.files))
            .order_by(Episode.episode_number, Episode.part_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {"items": cards(db, principal.user.id, items), "total": total, "page": page, "page_size": page_size}


@router.get("/media/{media_id}/next")
def next_episode(
    media_id: str, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> dict[str, Any]:
    item = load_item(db, principal.user.id, media_id)
    episode = db.get(Episode, media_id)
    series = db.scalar(select(Series).where(Series.media_item_id == media_id)) if item.kind == "series" else None
    season = db.get(Season, episode.season_id) if episode else None
    series_id = series.id if series else season.series_id if season else None
    query = (
        select(MediaItem)
        .join(Episode)
        .join(Season)
        .outerjoin(WatchProgress, progress_join(principal.user.id))
        .where(Season.series_id == series_id, permitted_library(principal.user.id), file_available())
    )
    if season and episode:
        query = query.where(
            or_(
                Season.season_number > season.season_number,
                (Season.season_number == season.season_number) & (Episode.episode_number > episode.episode_number),
            )
        )
    else:
        query = query.where(func.coalesce(WatchProgress.watched, False).is_(False))
    found = (
        db.scalar(
            query.options(noload(MediaItem.files)).order_by(Season.season_number, Episode.episode_number).limit(1)
        )
        if series_id
        else None
    )
    return {"item": cards(db, principal.user.id, [found])[0] if found else None}


class WatchInput(BaseModel):
    watched: bool


@router.put("/media/{media_id}/watched")
def watched(
    media_id: str, payload: WatchInput, principal: Principal = Depends(require_user_csrf), db: Session = Depends(get_db)
) -> dict[str, bool]:
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    if not principal.user.is_active or principal.session.revoked_at is not None:
        raise HTTPException(401, "Authentication required")
    load_item(db, principal.user.id, media_id)
    progress = db.scalar(
        select(WatchProgress).where(WatchProgress.user_id == principal.user.id, WatchProgress.media_item_id == media_id)
    )
    if progress is None:
        progress = WatchProgress(user_id=principal.user.id, media_item_id=media_id)
        db.add(progress)
    progress.watched = payload.watched
    progress.completed_at = utcnow() if payload.watched else None
    progress.last_played_at = utcnow()
    if not payload.watched:
        progress.position_seconds = 0
        progress.watched_seconds = 0
    db.commit()
    return {"watched": payload.watched}


@router.get("/artwork/{artwork_id}")
def artwork(
    artwork_id: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> FileResponse:
    art = db.scalar(
        select(LocalArtwork)
        .join(MediaItem)
        .join(LibraryPath, LocalArtwork.library_path_id == LibraryPath.id)
        .where(
            LocalArtwork.id == artwork_id,
            permitted_library(principal.user.id),
            LibraryPath.enabled.is_(True),
            LocalArtwork.artwork_type.in_(["poster", "background"]),
        )
    )
    if art is None:
        raise HTTPException(404, "Local artwork unavailable")
    library_path = db.get(LibraryPath, art.library_path_id)
    assert library_path is not None
    source = strict_local_file(Path(library_path.canonical_path), art.source_path, config)
    if source.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp") or source.stat().st_size > 20 * 1024 * 1024:
        raise HTTPException(404, "Local artwork unavailable")
    return FileResponse(
        source,
        media_type="image/png"
        if source.suffix.lower() == ".png"
        else "image/webp"
        if source.suffix.lower() == ".webp"
        else "image/jpeg",
    )
