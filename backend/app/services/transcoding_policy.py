"""One persisted policy for Docker and native playback; sessions retain snapshots."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import ApplicationSetting, PlaybackSession
from app.services.paths import UnsafeMediaPath, is_link_or_reparse, validate_windows_path_text

Mode = Literal["automatic", "hardware_preferred", "software_only", "direct_only", "hardware_required"]
Hardware = Literal["auto", "qsv", "nvenc", "amf"]
SUBTITLE_BEHAVIOR = (
    "Text subtitles are converted locally to WebVTT. Image subtitles and subtitle burn-in are not supported. "
    "Hardware acceleration applies to H.264 video encoding; decoding, audio and subtitles use the CPU."
)


class TranscodingPolicy(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    mode: Mode = "automatic"
    max_processes: int = Field(default=2, ge=1, le=8)
    max_height: int = Field(default=2160, ge=240, le=4320)
    max_bitrate_kbps: int = Field(default=12000, ge=500, le=50000)
    cpu_preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"] = "veryfast"
    preferred_hardware: Hardware = "auto"
    hardware_device: str = Field(default="auto", pattern=r"^(auto|[0-9]{1,2})$")
    allow_4k: bool = False
    temp_directory: str = Field(default="", max_length=1000)
    max_storage_mb: int = Field(default=4096, ge=64, le=1048576)
    inactive_session_seconds: int = Field(default=90, ge=30, le=600)

    @model_validator(mode="after")
    def required_encoder(self) -> TranscodingPolicy:
        if self.mode == "hardware_required" and self.preferred_hardware == "auto":
            raise ValueError("Hardware Required needs a selected hardware encoder")
        return self


def default_policy(config: AppConfig) -> TranscodingPolicy:
    return TranscodingPolicy(
        mode=config.transcode_mode,
        max_processes=config.transcode_max_processes,
        max_height=config.transcode_max_height,
        max_bitrate_kbps=config.transcode_max_bitrate_kbps,
        cpu_preset=config.transcode_cpu_preset,
        preferred_hardware=config.transcode_hardware if config.transcode_hardware != "software" else "auto",
        hardware_device=config.transcode_device,
        allow_4k=config.transcode_allow_4k,
        temp_directory=str(config.temp_dir.absolute()),
        max_storage_mb=config.transcode_max_storage_mb,
        inactive_session_seconds=config.playback_session_timeout_seconds,
    )


def read_policy(db: Session, config: AppConfig) -> TranscodingPolicy:
    row = db.get(ApplicationSetting, "transcoding.policy")
    return TranscodingPolicy.model_validate(row.value) if row else default_policy(config)


def session_policy(playback: PlaybackSession, config: AppConfig) -> TranscodingPolicy:
    value = playback.decision.get("settings")
    return TranscodingPolicy.model_validate(value) if isinstance(value, dict) else default_policy(config)


def public_policy(policy: TranscodingPolicy) -> dict[str, Any]:
    return {**policy.model_dump(), "subtitle_behavior": SUBTITLE_BEHAVIOR, "changes_apply_to": "new_streams"}


def safe_scratch_path(path: Path) -> bool:
    for component in (path, *path.parents):
        try:
            component.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if is_link_or_reparse(component):
            return False
    return True


def validate_temp_directory(value: str, config: AppConfig) -> Path:
    """Owner-configurable local scratch storage, never source media or program files."""
    path = Path(value)
    if os.name == "nt":
        try:
            validate_windows_path_text(value)
        except UnsafeMediaPath as exc:
            raise HTTPException(422, "Temporary storage must use an ordinary absolute Windows directory") from exc
    if not path.is_absolute() or path == Path(path.anchor) or value.startswith(("\\\\", "//")):
        raise HTTPException(422, "Temporary storage must be an absolute local directory, not a drive or network root")
    if not safe_scratch_path(path):
        raise HTTPException(422, "Temporary storage cannot contain symbolic links or junctions")
    canonical = path.resolve()
    forbidden = [*config.allowed_media_roots]
    forbidden += [Path(p).resolve() for k in ("ProgramFiles", "ProgramFiles(x86)", "WINDIR") if (p := os.getenv(k))]
    if config.native_program_dir is not None:
        forbidden.append(config.native_program_dir.resolve())
    if any(canonical == root or root in canonical.parents or canonical in root.parents for root in forbidden):
        raise HTTPException(
            422, "Temporary storage must be separate from media roots and protected program directories"
        )
    try:
        canonical.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=canonical):
            pass
    except OSError as exc:
        raise HTTPException(422, "The service cannot write to the selected temporary directory") from exc
    return canonical
