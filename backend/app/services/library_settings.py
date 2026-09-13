"""Versioned scan preferences in the existing, Agent-owned settings table."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from app.models import ApplicationSetting

KEY = "libraries.settings"


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frequency: Literal["off", "daily", "weekly"] = "off"
    time: str = Field(default="03:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    weekday: int = Field(default=0, ge=0, le=6, strict=True)


class ScanPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automatic_scanning: StrictBool = False
    scan_on_change: StrictBool = False
    # There is no permanent-trash subsystem. Never invent deletion semantics.
    empty_trash_after_scan: Literal[False] = False
    schedule: Schedule = Field(default_factory=Schedule)


class ScanAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_preview_thumbnails: Literal[False] = False
    chapter_thumbnails: Literal[False] = False
    analyze_audio_tracks: StrictBool = True
    analyze_subtitle_tracks: StrictBool = True


class LibrarySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    scan_policy: ScanPolicy = Field(default_factory=ScanPolicy)
    scan_analysis: ScanAnalysis = Field(default_factory=ScanAnalysis)


def read_library_settings(db: Session) -> LibrarySettings:
    row = db.get(ApplicationSetting, KEY)
    return LibrarySettings.model_validate(row.value if row else {})


def update_library_settings(db: Session, patch: dict[str, Any]) -> LibrarySettings:
    if set(patch) - {"scan_policy", "scan_analysis"}:
        raise ValueError("Unknown Libraries settings group")
    proposed = read_library_settings(db).model_dump()
    for group, fields in patch.items():
        if not isinstance(fields, dict):
            raise ValueError("Settings groups must be objects")
        fields = dict(fields)
        if group == "scan_policy" and "schedule" in fields:
            if not isinstance(fields["schedule"], dict):
                raise ValueError("Schedule must be an object")
            fields["schedule"] = {**proposed[group]["schedule"], **fields["schedule"]}
        proposed[group] = {**proposed[group], **fields}
    validated = LibrarySettings.model_validate(proposed)
    db.merge(ApplicationSetting(key=KEY, value=validated.model_dump()))
    db.commit()
    return read_library_settings(db)
