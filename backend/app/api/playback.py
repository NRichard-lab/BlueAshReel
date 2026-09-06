from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.config import AppConfig, get_config
from app.database import get_db
from app.dependencies import Principal, current_principal, require_csrf, require_owner, require_user_csrf
from app.models import ApplicationSetting, MediaFile, PlaybackSession, User, WatchProgress, utcnow
from app.security import _as_utc
from app.services.catalog import authorized_file
from app.services.compatibility import TEXT_SUBTITLES, Capabilities, Decision, PlaybackChoice, decide
from app.services.playback import byte_range, file_chunks, load_playback, playback_budget, save_progress, setting
from app.services.process_supervisor import owned_size
from app.services.subtitles import extract_subtitles, retime_vtt
from app.services.transcoding import SEGMENT_NAME, PlaybackManager, no_links
from app.services.transcoding_policy import (
    TranscodingPolicy,
    public_policy,
    read_policy,
    session_policy,
    validate_temp_directory,
)

router = APIRouter(tags=["playback"])


def get_playback_manager(request: Request) -> PlaybackManager:
    return request.app.state.playback_manager  # type: ignore[no-any-return]


class ClientPlaybackHealth(BaseModel):
    buffer_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)
    buffering: bool = False
    dropped_frames: int = Field(default=0, ge=0, le=2147483647)
    total_frames: int = Field(default=0, ge=0, le=2147483647)
    waits: int = Field(default=0, ge=0, le=2147483647)
    fullscreen: bool = False
    playable_ms: int | None = Field(default=None, ge=0, le=300000)


class ProgressInput(BaseModel):
    position_seconds: float = Field(ge=0, allow_inf_nan=False)
    playing: bool = False
    reason: Literal["periodic", "playing", "pause", "seek", "exit", "ended", "restart"] = "periodic"
    sequence: int = Field(ge=1, le=2147483647)
    telemetry: ClientPlaybackHealth | None = None


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
    return decide(file, payload, config, read_policy(db, config))


@router.post("/playback/sessions", status_code=201)
def create_playback(
    payload: PlaybackChoice,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> dict[str, Any]:
    playback_budget.check(f"create:{principal.user.id}", 12)
    # Admission is atomic across concurrent requests in the single local SQLite database.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    if not principal.user.is_active or principal.session.revoked_at is not None:
        raise HTTPException(401, "Authentication required")
    recovery: PlaybackSession | None = None
    if payload.recovery_from:
        recovery = db.get(PlaybackSession, payload.recovery_from)
        if (
            recovery is None
            or recovery.user_id != principal.user.id
            or recovery.auth_session_id != principal.session.id
            or recovery.media_file_id != payload.file_id
        ):
            raise HTTPException(404, "Playback session not found")
        if not manager.can_recover(recovery):
            raise HTTPException(409, "This stream is not eligible for another hardware-to-software recovery")
        payload = payload.model_copy(
            update={
                "audio_index": recovery.audio_index,
                "subtitle_index": recovery.subtitle_index,
                "quality": recovery.quality,
                "capabilities": Capabilities.model_validate(recovery.capabilities),
            }
        )
    file, source = authorized_file(db, principal.user.id, payload.file_id, config)
    if recovery and recovery.fingerprint != file.fingerprint:
        raise HTTPException(409, "Source changed. Start a new playback session after rescanning.")
    policy = session_policy(recovery, config) if recovery else read_policy(db, config)
    if recovery:
        recovery.state, recovery.ended_at, recovery.was_playing = "failed", utcnow(), False
        recovery.error = "Hardware conversion failed during playback. A software recovery was requested."
        recovery.decision = {**recovery.decision, "recovery_used": True}
    choice = decide(file, payload, config, policy)
    if choice.method == "unsupported":
        raise HTTPException(422, choice.reason)
    for old in db.scalars(select(PlaybackSession).where(PlaybackSession.state == "active")):
        if _as_utc(old.last_seen_at) < utcnow() - timedelta(
            seconds=session_policy(old, config).inactive_session_seconds
        ):
            old.state, old.ended_at = "expired", utcnow()
    db.flush()
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
    offset = position if choice.method != "direct" else 0
    if offset:
        if payload.delivery == "remux":
            raise HTTPException(422, "Start from the beginning for forced Remux; precise resume requires transcoding.")
        if policy.mode == "direct_only":
            raise HTTPException(
                422, "Precise seeking in remuxed media requires conversion. Direct Play and Remux Only is enabled."
            )
        if any((video.height or 0) >= 2160 for video in file.video_streams) and not policy.allow_4k:
            raise HTTPException(
                422, "Precise seeking in this remuxed 4K source requires conversion; 4K transcoding is disabled."
            )
        choice = choice.model_copy(
            update={
                "method": "transcode",
                "video_copy": False,
                "audio_copy": False,
                "reason": "Precise local seek/resume requires video conversion at this position.",
            }
        )
    playback = PlaybackSession(
        user_id=principal.user.id,
        auth_session_id=principal.session.id,
        media_item_id=file.media_item_id,
        media_file_id=file.id,
        fingerprint=file.fingerprint,
        method=choice.method,
        decision={**choice.model_dump(), "settings": policy.model_dump()},
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
    url = f"/api/v1/playback/{playback.id}/file"
    if choice.method != "direct":
        # Do not hold SQLite's writer lock during process admission/readiness.
        try:
            if recovery:
                manager.invalidate_encoder(
                    str(recovery.decision.get("active_encoder")), "Hardware failed during playback. Retest hardware."
                )
                manager.stop(recovery.id)
            manager.create(
                file,
                source,
                playback,
                choice,
                offset,
                software_fallback="Hardware failed during playback; resumed with software." if recovery else None,
            )
            db.commit()  # Persist actual encoder/fallback only after bounded process admission.
            url = f"/api/v1/playback/{playback.id}/hls/index.m3u8"
            db.expire_all()
            load_playback(db, principal, playback.id, config)
        except HTTPException as exc:
            playback.state, playback.ended_at, playback.error = "failed", utcnow(), str(exc.detail)[:200]
            db.commit()
            manager.stop(playback.id)
            raise
    return {
        "id": playback.id,
        "decision": choice,
        "position_seconds": position,
        "duration_seconds": playback.duration_seconds,
        "url": url,
        "video_offset": offset,
        "audio_index": playback.audio_index,
        "subtitle_index": playback.subtitle_index,
        "quality": playback.quality,
        "method_label": stream_method(playback, playback.decision.get("active_encoder")),
        "fallback": bool(playback.decision.get("fallback")),
        "fallback_reason": playback.decision.get("fallback_reason"),
        "selected_mode": policy.mode,
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


@router.get("/playback/{session_id}/recovery")
def playback_recovery(
    session_id: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> dict[str, Any]:
    row = db.get(PlaybackSession, session_id)
    if row is None or row.user_id != principal.user.id or row.auth_session_id != principal.session.id:
        raise HTTPException(404, "Playback session not found")
    recoverable = manager.can_recover(row)
    return {
        "recoverable": recoverable,
        "reason": "Hardware failed. One software reconnect is available." if recoverable else row.error,
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
    if payload.telemetry is not None and payload.sequence > playback.sequence:
        playback.decision = {**playback.decision, "client_health": payload.telemetry.model_dump(),
                             "client_health_at": utcnow().isoformat()}
    return save_progress(
        db, playback, payload.position_seconds, payload.playing, payload.reason, payload.sequence, config
    )


@router.post("/playback/{session_id}/stop", status_code=204)
def stop(
    session_id: str,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> None:
    playback = db.get(PlaybackSession, session_id)
    if playback is None or playback.user_id != principal.user.id or playback.auth_session_id != principal.session.id:
        raise HTTPException(404, "Playback session not found")
    was_failed = playback.state == "failed"
    playback.state = "failed" if was_failed else "stopping"
    playback.ended_at = utcnow()
    db.commit()
    try:
        manager.stop(session_id)
    except HTTPException:
        playback.state, playback.error = "stop_failed", "Local process stop or cleanup needs attention"
        db.commit()
        raise
    playback.state, playback.was_playing = "failed" if was_failed else "stopped", False
    db.commit()


@router.post("/playback/{session_id}/end", status_code=204)
def end_playback(
    session_id: str,
    payload: ProgressInput,
    principal: Principal = Depends(require_user_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> None:
    try:
        progress(session_id, payload, principal, db, config)
    finally:
        # Exit must release the process even if the final progress checkpoint
        # is rate-limited or the source vanished. Stop rechecks ownership.
        db.rollback()
        stop(session_id, principal, db, manager)


@router.get("/playback/{session_id}/subtitles/{index}.vtt")
def subtitle(
    session_id: str,
    index: int,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> Response:
    playback_budget.check(f"subtitle:{principal.user.id}", 20)
    playback, file, source = load_playback(db, principal, session_id, config)
    track = next((s for s in file.subtitle_streams if s.stream_index == index), None)
    if track is None:
        raise HTTPException(404, "Subtitle track not found")
    if track.codec not in TEXT_SUBTITLES:
        raise HTTPException(422, "Image subtitles require a locally converted video representation")
    offset = manager.for_session(session_id).offset if playback.method != "direct" else 0
    db.rollback()
    return Response(
        retime_vtt(extract_subtitles(source, index, config, runner=manager.subtitle_output), offset),
        media_type="text/vtt; charset=utf-8",
    )


@router.get("/playback/{session_id}/hls/{name}")
def hls_file(
    session_id: str,
    name: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> Response:
    playback, _file, _source = load_playback(db, principal, session_id, config)
    if playback.method == "direct" or (name != "index.m3u8" and not SEGMENT_NAME.fullmatch(name)):
        raise HTTPException(404, "Streaming resource not found")
    job = manager.for_session(session_id)
    path = job.directory / name
    if not no_links(path) or not path.is_file():
        raise HTTPException(404, "Streaming resource is not available")
    if name == "index.m3u8":
        if path.stat().st_size > 2 * 1048576:
            raise HTTPException(422, "Local manifest exceeds safety limit")
        contents = path.read_text(encoding="utf-8")
        if any(
            line and not line.startswith("#") and not SEGMENT_NAME.fullmatch(line) for line in contents.splitlines()
        ):
            raise HTTPException(422, "Invalid local manifest")
        return Response(contents, media_type="application/vnd.apple.mpegurl")
    return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@router.get("/streams")
def streams(
    _principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> dict[str, Any]:
    rows = db.execute(
        select(PlaybackSession, User.username, MediaFile)
        .join(User, PlaybackSession.user_id == User.id)
        .join(MediaFile, PlaybackSession.media_file_id == MediaFile.id)
        .options(selectinload(MediaFile.video_streams))
        .where(PlaybackSession.state.in_(["active", "stopping", "stop_failed"]))
        .order_by(PlaybackSession.started_at)
        .limit(32)
    ).all()
    items = []
    for row, username, file in rows:
        try:
            job = manager.for_session(row.id)
        except HTTPException:
            job = None
        video = min(file.video_streams, key=lambda stream: stream.stream_index) if file.video_streams else None
        items.append(
            {
                "id": row.id,
                "username": username,
                "method": row.method,
                "state": row.state,
                "source_height": video.height if video else None,
                "source_width": video.width if video else None,
                "source_bitrate_kbps": file.bitrate / 1000 if file.bitrate else None,
                "output_width": (
                    video.width if row.decision.get("video_copy") else
                    round(video.width * row.decision.get("output_height", 0) / video.height / 2) * 2
                ) if video and video.width and video.height else None,
                "subtitle_mode": "WebVTT" if row.subtitle_index is not None else "Off",
                "started_at": _as_utc(row.started_at).isoformat(),
                "client_health": row.decision.get("client_health"),
                "client_health_at": row.decision.get("client_health_at"),
                "output_height": row.decision.get("output_height"),
                "bitrate_kbps": row.decision.get("bitrate_kbps"),
                "observed_bitrate_kbps": job.metrics.get("output_bitrate_kbps") if job else None,
                "speed": job.metrics.get("speed") if job else None,
                "encoder": job.encoder
                if job
                else row.decision.get("active_encoder", "none" if row.method == "direct" else "unavailable"),
                "method_label": stream_method(row, job.encoder if job else row.decision.get("active_encoder")),
                "audio_encoder": job.audio_encoder if job else row.decision.get("audio_encoder", "none"),
                "selected_mode": row.decision.get("settings", {}).get("mode", "automatic"),
                "fallback": job.fallback if job else bool(row.decision.get("fallback")),
                "fallback_reason": job.fallback_reason if job else row.decision.get("fallback_reason"),
                "elapsed_seconds": max(0, (utcnow() - _as_utc(row.started_at)).total_seconds()),
                "startup_ms": row.decision.get("client_health", {}).get("playable_ms"),
                "temp_bytes": owned_size(job.directory) if job else 0,
                "error": row.error,
            }
        )
    failures = db.execute(
        select(PlaybackSession, User.username)
        .join(User, PlaybackSession.user_id == User.id)
        .where(PlaybackSession.state == "failed")
        .order_by(PlaybackSession.ended_at.desc())
        .limit(8)
    ).all()
    return {
        "items": items,
        "total": len(items),
        "health": manager.health(),
        "failures": [
            {
                "id": row.id,
                "username": username,
                "error": row.error,
                "method_label": stream_method(row, row.decision.get("active_encoder")),
                "selected_mode": row.decision.get("settings", {}).get("mode", "automatic"),
                "ended_at": row.ended_at,
            }
            for row, username in failures
        ],
    }


def stream_method(row: PlaybackSession, encoder: str | None) -> str:
    if row.method == "direct":
        return "Direct Play"
    if row.method == "remux":
        return "Remux"
    if not encoder:
        return "Failed Transcode" if row.state == "failed" else "Starting Transcode"
    if encoder and encoder not in ("copy", "libx264"):
        return "Hardware Transcode"
    return "Software Transcode"


@router.post("/streams/{session_id}/stop", status_code=204)
def owner_stop(
    session_id: str,
    _principal: Principal = Depends(require_csrf),
    db: Session = Depends(get_db),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> None:
    row = db.get(PlaybackSession, session_id)
    if row is None:
        raise HTTPException(404, "Playback session not found")
    row.state, row.ended_at, row.was_playing = "stopping", utcnow(), False
    db.commit()
    try:
        manager.stop(session_id)
    except HTTPException:
        row.state, row.error = "stop_failed", "Local process stop or cleanup needs attention"
        db.commit()
        raise
    row.state, row.error = "stopped", None
    db.commit()


@router.get("/playback-health")
def playback_health(
    _principal: Principal = Depends(require_owner), manager: PlaybackManager = Depends(get_playback_manager)
) -> dict[str, Any]:
    return manager.health()


@router.post("/playback-health/detect")
def detect_hardware(
    _principal: Principal = Depends(require_csrf), manager: PlaybackManager = Depends(get_playback_manager)
) -> dict[str, str]:
    return manager.detect_hardware()


@router.get("/transcoding-policy")
def transcoding_policy(
    _principal: Principal = Depends(require_owner),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    return public_policy(read_policy(db, config))


@router.patch("/transcoding-policy")
def update_transcoding_policy(
    payload: TranscodingPolicy,
    _principal: Principal = Depends(require_csrf),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    manager: PlaybackManager = Depends(get_playback_manager),
) -> dict[str, Any]:
    canonical = validate_temp_directory(payload.temp_directory, config)
    payload = payload.model_copy(update={"temp_directory": str(canonical)})
    manager.storage_root(payload)
    db.merge(ApplicationSetting(key="transcoding.policy", value=payload.model_dump()))
    db.commit()
    return public_policy(payload)


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
