"""Short database-only transitions; the process manager reaps terminal work."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import MediaItem, PlaybackSession, WatchProgress, utcnow
from app.services.playback import setting


def end_sessions(
    db: Session, *, user_id: str | None = None, auth_id: str | None = None, removed_libraries: list[str] | None = None
) -> None:
    query = update(PlaybackSession).where(PlaybackSession.state == "active")
    if user_id is not None:
        query = query.where(PlaybackSession.user_id == user_id)
    if auth_id is not None:
        query = query.where(PlaybackSession.auth_session_id == auth_id)
    if removed_libraries is not None:
        query = query.where(
            PlaybackSession.media_item_id.in_(select(MediaItem.id).where(MediaItem.library_id.in_(removed_libraries)))
        )
    db.execute(query.values(state="stopped", ended_at=utcnow(), was_playing=False))


def erase_history(db: Session, user_id: str) -> None:
    # Deleting the live record invalidates all old URLs/checkpoints as well.
    db.execute(delete(PlaybackSession).where(PlaybackSession.user_id == user_id))
    db.execute(delete(WatchProgress).where(WatchProgress.user_id == user_id))


def retain_history(db: Session, config: AppConfig) -> None:
    days = setting(db, "history_days", config.playback_history_days)
    # Zero means keep indefinitely; explicit history deletion remains available.
    if days <= 0:
        return
    cutoff = utcnow() - timedelta(days=days)
    db.execute(delete(WatchProgress).where(WatchProgress.last_played_at < cutoff))
    db.execute(delete(PlaybackSession).where(PlaybackSession.state != "active", PlaybackSession.last_seen_at < cutoff))
