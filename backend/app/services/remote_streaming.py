"""Agent-owned remote playback limits, layered on the existing transcoder."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import ApplicationSetting

KEY = "remote_streaming.settings"
DISABLED = "Remote streaming is disabled for this Agent."
LIMIT_REACHED = "Remote session limit reached. Stop another remote stream first."


class RemoteStreamingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    enabled: StrictBool = True
    max_quality: Literal["original", "4k", "1080p", "720p", "480p"] = "original"
    # None preserves existing playback policy; a configured cap is always positive.
    bitrate_limit_bps: int | None = Field(default=None, ge=1_000_000, le=50_000_000, strict=True)
    session_limit: int = Field(default=8, ge=1, le=100, strict=True)

    @property
    def max_height(self) -> int | None:
        return {"original": None, "4k": 2160, "1080p": 1080, "720p": 720, "480p": 480}[self.max_quality]


def read_remote_settings(db: Session, config: AppConfig) -> RemoteStreamingSettings:
    row = db.get(ApplicationSetting, KEY)
    return RemoteStreamingSettings.model_validate(
        row.value if row else {"session_limit": min(100, max(1, config.playback_max_streams))}
    )


def update_remote_settings(db: Session, config: AppConfig, patch: dict[str, Any]) -> RemoteStreamingSettings:
    if set(patch) - {"enabled", "max_quality", "bitrate_limit_bps", "session_limit"}:
        raise ValueError("Unknown Remote Streaming setting")
    # Serialize partial updates with playback admission and other settings writers.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    value = RemoteStreamingSettings.model_validate({**read_remote_settings(db, config).model_dump(), **patch})
    db.merge(ApplicationSetting(key=KEY, value=value.model_dump()))
    db.commit()
    return value


def require_remote_enabled(db: Session, config: AppConfig) -> RemoteStreamingSettings:
    value = read_remote_settings(db, config)
    if not value.enabled:
        raise HTTPException(403, DISABLED)
    return value
