from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import anyio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.dependencies import Principal
from app.models import ApplicationSetting, MediaFile, PlaybackSession, UserSession, WatchProgress, utcnow
from app.security import _as_utc
from app.services.catalog import authorized_file
from app.services.transcoding_policy import session_policy


class RequestBudget:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()

    def check(self, key: str, limit: int, seconds: int = 60) -> None:
        now = time.monotonic()
        with self.lock:
            times = self.entries.setdefault(key, deque())
            while times and times[0] < now - seconds:
                times.popleft()
            if len(times) >= limit:
                raise HTTPException(
                    429, "Playback request limit reached. Try again shortly.", headers={"Retry-After": str(seconds)}
                )
            times.append(now)
            self.entries.move_to_end(key)
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()


playback_budget = RequestBudget()


def setting(db: Session, name: str, default: float) -> float:
    row = db.get(ApplicationSetting, f"playback.{name}")
    return float(row.value) if row and isinstance(row.value, (int, float)) else default


def load_playback(
    db: Session, principal: Principal, session_id: str, config: AppConfig
) -> tuple[PlaybackSession, MediaFile, Path]:
    playback = db.get(PlaybackSession, session_id)
    if playback is None or playback.user_id != principal.user.id or playback.auth_session_id != principal.session.id:
        raise HTTPException(404, "Playback session not found")
    if playback.state != "active" or _as_utc(playback.last_seen_at) < utcnow() - timedelta(
        seconds=session_policy(playback, config).inactive_session_seconds
    ):
        raise HTTPException(410, "This playback session has ended. Start playback again.")
    file, source = authorized_file(db, principal.user.id, playback.media_file_id, config)
    if playback.fingerprint != file.fingerprint:
        raise HTTPException(409, "Source changed. Start a new playback session after rescanning.")
    return playback, file, source


def byte_range(value: str | None, size: int) -> tuple[int, int, int]:
    """One byte range, inclusive endpoints. Multirange is deliberately rejected."""
    if value is None:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", value) if len(value) <= 64 else None
    if not match or (not match[1] and not match[2]) or size <= 0:
        raise HTTPException(416, "A single valid byte range is required", headers={"Content-Range": f"bytes */{size}"})
    if not match[1]:
        suffix = int(match[2])
        start, end = max(0, size - suffix), size - 1
        if suffix == 0:
            start = size
    else:
        start, end = int(match[1]), min(size - 1, int(match[2])) if match[2] else size - 1
    if start >= size or end < start:
        raise HTTPException(416, "Requested byte range is unavailable", headers={"Content-Range": f"bytes */{size}"})
    return start, end, 206


def file_chunks(
    db: Session, playback: PlaybackSession, file: MediaFile, source: Path, start: int, end: int, config: AppConfig
) -> AsyncIterator[bytes]:
    # Freeze identifiers and release the request transaction before serving a large movie.
    bind = db.get_bind()
    session_id, user_id, auth_id = playback.id, playback.user_id, playback.auth_session_id
    size, modified = file.size_bytes, file.modified_ns
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    db.rollback()

    def permitted() -> bool:
        with Session(bind=bind) as check:
            auth = check.get(UserSession, auth_id) if auth_id else None
            if (
                not auth
                or auth.revoked_at is not None
                or _as_utc(auth.expires_at) <= utcnow()
                or not auth.user.is_active
                or not user_id
            ):
                return False
            try:
                load_playback(check, Principal(auth.user, auth), session_id, config)
            except HTTPException:
                return False
            return True

    async def chunks() -> AsyncIterator[bytes]:
        remaining = max(0, end - start + 1)
        check_at = 0.0
        descriptor = os.open(source, flags)
        try:
            info = os.fstat(descriptor)
            if info.st_size != size or info.st_mtime_ns != modified:
                return
            stream = anyio.wrap_file(os.fdopen(descriptor, "rb", closefd=False))
            async with stream:
                await stream.seek(start)
                while remaining:
                    if time.monotonic() >= check_at:
                        if not await anyio.to_thread.run_sync(permitted):
                            break
                        check_at = time.monotonic() + 1
                    block = await stream.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block
        finally:
            os.close(descriptor)

    return chunks()


def save_progress(
    db: Session,
    playback: PlaybackSession,
    position: float,
    playing: bool,
    reason: str,
    sequence: int,
    config: AppConfig,
) -> dict[str, Any]:
    if not math.isfinite(position) or not 0 <= position <= playback.duration_seconds + 2:
        raise HTTPException(422, "Playback position is outside the media duration")
    if sequence <= playback.sequence:
        return {"saved": False, "position_seconds": playback.position_seconds}
    now = utcnow()
    elapsed = max(0, (now - _as_utc(playback.last_seen_at)).total_seconds())
    difference = max(0, position - playback.position_seconds)
    credit = (
        min(elapsed, difference, 30)
        if playback.was_playing and reason not in ("seek", "restart") and difference <= elapsed * 1.5 + 2
        else 0
    )
    playback.was_playing = playing
    if reason == "restart":
        playback.watched_seconds = 0
    playback.watched_seconds += credit
    playback.sequence = sequence
    playback.position_seconds = min(position, playback.duration_seconds)
    playback.last_seen_at = now
    if reason == "playing" and playback.startup_ms is None:
        playback.startup_ms = max(0, int((now - _as_utc(playback.started_at)).total_seconds() * 1000))
    latest = db.scalar(
        select(PlaybackSession.id)
        .where(
            PlaybackSession.user_id == playback.user_id,
            PlaybackSession.media_item_id == playback.media_item_id,
            PlaybackSession.state == "active",
        )
        .order_by(PlaybackSession.started_at.desc(), PlaybackSession.id.desc())
        .limit(1)
    )
    # Restart only replaces the previous resume point after real playback advances.
    if latest == playback.id and playback.watched_seconds >= 2:
        progress = db.scalar(
            select(WatchProgress).where(
                WatchProgress.user_id == playback.user_id, WatchProgress.media_item_id == playback.media_item_id
            )
        )
        if progress is None:
            progress = WatchProgress(user_id=playback.user_id, media_item_id=playback.media_item_id, watched_seconds=0)
            db.add(progress)
        progress.media_file_id = playback.media_file_id
        progress.position_seconds = playback.position_seconds
        progress.duration_seconds = playback.duration_seconds
        progress.watched_seconds += credit
        progress.last_played_at = now
        threshold = setting(db, "watched_threshold", config.playback_watched_threshold)
        minimum = min(
            setting(db, "minimum_watch_seconds", config.playback_minimum_watch_seconds), playback.duration_seconds * 0.5
        )
        if position / playback.duration_seconds * 100 >= threshold and progress.watched_seconds >= minimum:
            progress.watched = True
            progress.completed_at = now
    db.commit()
    return {"saved": True, "position_seconds": position}
