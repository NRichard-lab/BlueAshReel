from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ApplicationSetting(Base):
    __tablename__ = "application_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    users: Mapped[list[User]] = relationship(secondary="user_roles", back_populates="roles")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    roles: Mapped[list[Role]] = relationship(secondary="user_roles", back_populates="users", lazy="selectin")
    sessions: Mapped[list[UserSession]] = relationship(back_populates="user", cascade="all,delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="sessions")


class UserLibrary(Base):
    __tablename__ = "user_libraries"
    __table_args__ = (Index("ix_user_libraries_library_user", "library_id", "user_id"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_next: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_countdown: Mapped[int] = mapped_column(Integer, default=10, nullable=False)


class WatchProgress(Base):
    __tablename__ = "watch_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "media_item_id", name="uq_progress_user_media"),
        Index("ix_progress_user_watched_last", "user_id", "watched", "last_played_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    media_item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    media_file_id: Mapped[str | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"))
    position_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    watched_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    watched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Library(Base, TimestampMixin):
    __tablename__ = "libraries"
    __table_args__ = (
        CheckConstraint("library_type IN ('movies','tv','other')", name="ck_library_type"),
        Index("ix_libraries_enabled", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    library_type: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_successful_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paths: Mapped[list[LibraryPath]] = relationship(
        back_populates="library", cascade="all,delete-orphan", lazy="selectin"
    )
    media_items: Mapped[list[MediaItem]] = relationship(back_populates="library")
    scan_jobs: Mapped[list[ScanJob]] = relationship(back_populates="library")


class LibraryPath(Base, TimestampMixin):
    __tablename__ = "library_paths"
    __table_args__ = (UniqueConstraint("library_id", "canonical_path", name="uq_library_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    canonical_path: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    library: Mapped[Library] = relationship(back_populates="paths")
    files: Mapped[list[MediaFile]] = relationship(back_populates="library_path")


class MediaItem(Base, TimestampMixin):
    __tablename__ = "media_items"
    __table_args__ = (
        CheckConstraint("kind IN ('movie','series','episode','other')", name="ck_media_kind"),
        Index("ix_media_library_available_title", "library_id", "available", "sort_title"),
        Index("ix_media_library_kind_added", "library_id", "kind", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_title: Mapped[str] = mapped_column(String(500), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    match_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    library: Mapped[Library] = relationship(back_populates="media_items")
    files: Mapped[list[MediaFile]] = relationship(
        back_populates="media_item", cascade="all,delete-orphan", lazy="selectin"
    )


class Movie(Base):
    __tablename__ = "movies"

    media_item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True)


class Series(Base):
    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    seasons: Mapped[list[Season]] = relationship(back_populates="series", cascade="all,delete-orphan")


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("series_id", "season_number", name="uq_series_season"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    season_number: Mapped[int] = mapped_column(Integer, nullable=False)
    series: Mapped[Series] = relationship(back_populates="seasons")
    episodes: Mapped[list[Episode]] = relationship(back_populates="season", cascade="all,delete-orphan")


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("season_id", "episode_number", "part_number", name="uq_season_episode"),)

    media_item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False)
    part_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    season: Mapped[Season] = relationship(back_populates="episodes")


class MediaFile(Base, TimestampMixin):
    __tablename__ = "media_files"
    __table_args__ = (
        UniqueConstraint("library_path_id", "relative_path", name="uq_media_file_path"),
        Index("ix_media_file_available_seen", "available", "last_seen_scan_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    library_path_id: Mapped[str] = mapped_column(ForeignKey("library_paths.id", ondelete="CASCADE"), index=True)
    relative_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    modified_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_scan_id: Mapped[str | None] = mapped_column(String(36), index=True)
    container: Mapped[str | None] = mapped_column(String(120))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    bitrate: Mapped[int | None] = mapped_column(Integer)
    embedded_title: Mapped[str | None] = mapped_column(String(500))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_duration_ms: Mapped[int | None] = mapped_column(Integer)
    analysis_error: Mapped[str | None] = mapped_column(String(500))
    media_item: Mapped[MediaItem] = relationship(back_populates="files")
    library_path: Mapped[LibraryPath] = relationship(back_populates="files")
    video_streams: Mapped[list[VideoStream]] = relationship(cascade="all,delete-orphan")
    audio_streams: Mapped[list[AudioStream]] = relationship(cascade="all,delete-orphan")
    subtitle_streams: Mapped[list[SubtitleStream]] = relationship(cascade="all,delete-orphan")


class VideoStream(Base):
    __tablename__ = "video_streams"
    __table_args__ = (UniqueConstraint("media_file_id", "stream_index", name="uq_video_stream"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_file_id: Mapped[str] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"), index=True)
    stream_index: Mapped[int] = mapped_column(Integer, nullable=False)
    codec: Mapped[str | None] = mapped_column(String(80))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bitrate: Mapped[int | None] = mapped_column(Integer)
    frame_rate: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(32))

    profile: Mapped[str | None] = mapped_column(String(80))
    level: Mapped[int | None] = mapped_column(Integer)
    pixel_format: Mapped[str | None] = mapped_column(String(40))
    bit_depth: Mapped[int | None] = mapped_column(Integer)


class AudioStream(Base):
    __tablename__ = "audio_streams"
    __table_args__ = (UniqueConstraint("media_file_id", "stream_index", name="uq_audio_stream"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_file_id: Mapped[str] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"), index=True)
    stream_index: Mapped[int] = mapped_column(Integer, nullable=False)
    codec: Mapped[str | None] = mapped_column(String(80))
    channels: Mapped[int | None] = mapped_column(Integer)
    channel_layout: Mapped[str | None] = mapped_column(String(80))
    bitrate: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(200))


class SubtitleStream(Base):
    __tablename__ = "subtitle_streams"
    __table_args__ = (UniqueConstraint("media_file_id", "stream_index", name="uq_subtitle_stream"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_file_id: Mapped[str] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"), index=True)
    stream_index: Mapped[int] = mapped_column(Integer, nullable=False)
    codec: Mapped[str | None] = mapped_column(String(80))
    language: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(200))
    forced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hearing_impaired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LocalArtwork(Base, TimestampMixin):
    __tablename__ = "local_artwork"
    __table_args__ = (
        UniqueConstraint(
            "media_item_id",
            "library_path_id",
            "artwork_type",
            "source_path",
            name="uq_artwork",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    library_path_id: Mapped[str] = mapped_column(ForeignKey("library_paths.id", ondelete="CASCADE"), index=True)
    artwork_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    cached_path: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','failed','cancelled')",
            name="ck_job_status",
        ),
        Index("ix_jobs_queue", "status", "priority", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    progress_current: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_total: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    events: Mapped[list[BackgroundJobEvent]] = relationship(back_populates="job", cascade="all,delete-orphan")
    scan: Mapped[ScanJob | None] = relationship(back_populates="job", uselist=False)


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("background_jobs.id", ondelete="CASCADE"), unique=True)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    scan_mode: Mapped[str] = mapped_column(String(16), default="changed", nullable=False)
    discovered_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unchanged_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    missing_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    job: Mapped[BackgroundJob] = relationship(back_populates="scan")
    library: Mapped[Library] = relationship(back_populates="scan_jobs")


class ScanLock(Base):
    """Cross-process exclusion for scans; one row exists per actively scanning library."""

    __tablename__ = "scan_locks"

    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BackgroundJobEvent(Base):
    __tablename__ = "background_job_events"
    __table_args__ = (Index("ix_job_event_job_created", "job_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("background_jobs.id", ondelete="CASCADE"))
    level: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    job: Mapped[BackgroundJob] = relationship(back_populates="events")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ExternalMetadataIdentifier(Base):
    __tablename__ = "external_metadata_identifiers"
    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_provider_external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    media_item_id: Mapped[str] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
