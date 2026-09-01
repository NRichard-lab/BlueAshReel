from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import AppConfig, get_config
from app.database import get_db
from app.dependencies import Principal, current_principal, require_csrf, require_owner, require_user_csrf
from app.models import ApplicationSetting, PlaybackSession, WatchProgress, utcnow
from app.services.catalog import authorized_file
from app.services.compatibility import TEXT_SUBTITLES, Decision, PlaybackChoice, decide
from app.services.playback import byte_range, file_chunks, load_playback, playback_budget, save_progress, setting
from app.services.subtitles import extract_subtitles

router = APIRouter(tags=["playback"])


class ProgressInput(BaseModel):
    position_seconds: float = Field(ge=0, allow_inf_nan=False)
    playing: bool = False
    reason: Literal["periodic", "playing", "pause", "seek", "exit", "ended", "restart"] = "periodic"
    sequence: int = Field(ge=1, le=2147483647)


class HistoryPolicy(BaseModel):
    watched_threshold: float = Field(default=90, ge=50, le=100, allow_inf_nan=False)
    minimum_watch_seconds: int = Field(default=30, ge=5, le=300)
    history_days: int = Field(default=365, ge=0, le=3650)


@router.post("/playback/decision")
def decision(
    payload: PlaybackChoice,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> Decision:
    playback_budget.check(f"decision:{principal.user.id}", 60)
    file, _source = authorized_file(db, principal.user.id, payload.file_id, config)
    return decide(file, payload, config)


@router.post("/playback/sessions", status_code=201)
def create_playback(
    payload: PlaybackChoice,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    playback_budget.check(f"create:{principal.user.id}", 12)
    # Admission is atomic across concurrent requests in the single local SQLite database.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    if not principal.user.is_active or principal.session.revoked_at is not None:
        raise HTTPException(401, "Authentication required")
    file, _source = authorized_file(db, principal.user.id, payload.file_id, config)
    choice = decide(file, payload, config)
    if choice.method == "unsupported":
        raise HTTPException(422, choice.reason)
    if choice.method != "direct":
        raise HTTPException(501, "This file needs the local remux/transcode layer; it cannot direct-play.")
    cutoff = utcnow() - timedelta(seconds=config.playback_session_timeout_seconds)
    db.execute(
        update(PlaybackSession)
        .where(PlaybackSession.state == "active", PlaybackSession.last_seen_at < cutoff)
        .values(state="expired", ended_at=utcnow())
    )
    total = db.scalar(select(func.count(PlaybackSession.id)).where(PlaybackSession.state == "active")) or 0
    own = (
        db.scalar(
            select(func.count(PlaybackSession.id)).where(
                PlaybackSession.state == "active", PlaybackSession.user_id == principal.user.id
            )
        )
        or 0
    )
    if total >= config.playback_max_streams or own >= config.playback_streams_per_user:
        raise HTTPException(429, "Active stream limit reached. Stop another stream first.")
    progress = db.scalar(
        select(WatchProgress).where(
            WatchProgress.user_id == principal.user.id, WatchProgress.media_item_id == file.media_item_id
        )
    )
    position = progress.position_seconds if progress and not progress.watched and not payload.restart else 0
    if payload.position_seconds is not None:
        position = min(payload.position_seconds, max(0, (file.duration_seconds or 0) - 0.1))
    playback = PlaybackSession(
        user_id=principal.user.id,
        auth_session_id=principal.session.id,
        media_item_id=file.media_item_id,
        media_file_id=file.id,
        fingerprint=file.fingerprint,
        method=choice.method,
        decision=choice.model_dump(),
        capabilities=payload.capabilities.model_dump(),
        audio_index=payload.audio_index
        if payload.audio_index is not None
        else min((a.stream_index for a in file.audio_streams), default=None),
        subtitle_index=payload.subtitle_index,
        quality=payload.quality,
        duration_seconds=file.duration_seconds or 0,
        position_seconds=position,
    )
    db.add(playback)
    db.commit()
    return {
        "id": playback.id,
        "decision": choice,
        "position_seconds": position,
        "duration_seconds": playback.duration_seconds,
        "url": f"/api/v1/playback/{playback.id}/file",
        "audio_index": playback.audio_index,
        "subtitle_index": playback.subtitle_index,
        "quality": playback.quality,
    }


@router.get("/playback/{session_id}/file")
@router.head("/playback/{session_id}/file", include_in_schema=False)
def direct_file(
    session_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> Response:
    playback, file, source = load_playback(db, principal, session_id, config)
    if playback.method != "direct":
        raise HTTPException(404, "This session uses segmented playback")
    etag = f'"{file.fingerprint}"'
    value = request.headers.get("range")
    if request.headers.get("if-range") not in (None, etag):
        value = None
    start, end, status = byte_range(value, file.size_bytes)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(0, end - start + 1)),
        "ETag": etag,
        "Content-Disposition": "inline",
        "Cache-Control": "no-store",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file.size_bytes}"
    mime = str(playback.decision.get("mime", "video/mp4"))
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=mime)
    chunks = file_chunks(db, playback, file, source, start, end, config)
    return StreamingResponse(chunks, status_code=status, headers=headers, media_type=mime)


@router.get("/playback/{session_id}")
def playback_state(
    session_id: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    playback, _file, _source = load_playback(db, principal, session_id, config)
    return {
        "id": playback.id,
        "state": playback.state,
        "method": playback.method,
        "startup_ms": playback.startup_ms,
        "position_seconds": playback.position_seconds,
    }


@router.post("/playback/{session_id}/progress")
def progress(
    session_id: str,
    payload: ProgressInput,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    playback_budget.check(f"progress:{principal.user.id}", 40)
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    if not principal.user.is_active or principal.session.revoked_at is not None:
        raise HTTPException(401, "Authentication required")
    playback, _file, _source = load_playback(db, principal, session_id, config)
    return save_progress(
        db, playback, payload.position_seconds, payload.playing, payload.reason, payload.sequence, config
    )


@router.post("/playback/{session_id}/stop", status_code=204)
def stop(session_id: str, principal: Principal = Depends(require_user_csrf), db: Session = Depends(get_db)) -> None:
    playback = db.get(PlaybackSession, session_id)
    if playback is None or playback.user_id != principal.user.id or playback.auth_session_id != principal.session.id:
        raise HTTPException(404, "Playback session not found")
    playback.state = "stopped"
    playback.ended_at = utcnow()
    db.commit()


@router.post("/playback/{session_id}/end", status_code=204)
def end_playback(
    session_id: str,
    payload: ProgressInput,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> None:
    progress(session_id, payload, principal, db, config)
    stop(session_id, principal, db)


@router.get("/playback/{session_id}/subtitles/{index}.vtt")
def subtitle(
    session_id: str,
    index: int,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> Response:
    playback_budget.check(f"subtitle:{principal.user.id}", 20)
    _playback, file, source = load_playback(db, principal, session_id, config)
    track = next((s for s in file.subtitle_streams if s.stream_index == index), None)
    if track is None:
        raise HTTPException(404, "Subtitle track not found")
    if track.codec not in TEXT_SUBTITLES:
        raise HTTPException(422, "Image subtitles require a locally converted video representation")
    db.rollback()
    return Response(extract_subtitles(source, index, config), media_type="text/vtt; charset=utf-8")


@router.get("/playback-policy")
def policy(
    _principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, float]:
    return {
        "watched_threshold": setting(db, "watched_threshold", config.playback_watched_threshold),
        "minimum_watch_seconds": setting(db, "minimum_watch_seconds", config.playback_minimum_watch_seconds),
        "history_days": setting(db, "history_days", config.playback_history_days),
    }


@router.patch("/playback-policy")
def update_policy(
    payload: HistoryPolicy, _principal: Principal = Depends(require_csrf), db: Session = Depends(get_db)
) -> HistoryPolicy:
    for key, value in payload.model_dump().items():
        db.merge(ApplicationSetting(key=f"playback.{key}", value=value))
    db.commit()
    return payload
