from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProductConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "Home Media"
    subtitle: str = "A private media application."
    version: str = "0.1.0"
    api_prefix: str = "/api/v1"

    @field_validator("api_prefix")
    @classmethod
    def valid_prefix(cls, value: str) -> str:
        value = value.rstrip("/")
        if value != "/api/v1":
            raise ValueError("api_prefix must remain /api/v1 for this API version")
        return value


def _find_product_config() -> Path | None:
    explicit = os.getenv("PRODUCT_CONFIG_FILE")
    if explicit:
        explicit_path = Path(explicit)
        return explicit_path if explicit_path.is_file() else None
    candidates = [
        Path.cwd() / "config" / "product.json",
        Path(__file__).resolve().parents[2] / "config" / "product.json",
        Path("/app/config/product.json"),
    ]
    return next((item for item in candidates if item and item.is_file()), None)


@lru_cache
def get_product_config() -> ProductConfig:
    explicit = os.getenv("PRODUCT_CONFIG_FILE")
    path = _find_product_config()
    if path is None:
        if explicit:
            raise RuntimeError("The explicitly configured product configuration file is missing")
        return ProductConfig()
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return ProductConfig.model_validate(raw)
    except (OSError, ValueError, TypeError) as exc:
        if explicit:
            raise RuntimeError("The explicitly configured product configuration file is invalid") from exc
        return ProductConfig()


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/app.db"
    app_secret_key: str = Field(min_length=32)
    app_data_dir: Path = Path("./data")
    temp_dir: Path = Path("./data/tmp")
    artwork_dir: Path = Path("./data/artwork")
    media_roots: str = ""
    outbound_integrations_enabled: bool = False
    log_level: str = "INFO"
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"
    session_cookie_name: str = "media_session"
    session_ttl_hours: int = Field(default=24, ge=1, le=24 * 30)
    session_cookie_secure: bool = False
    scan_batch_size: int = Field(default=50, ge=1, le=1000)
    ffprobe_timeout_seconds: int = Field(default=45, ge=1, le=600)
    worker_poll_interval: float = Field(default=1.0, ge=0.1, le=60)
    job_stale_minutes: int = Field(default=15, ge=1, le=1440)
    scan_extensions: str = ".mkv,.mp4,.m4v,.avi,.mov,.wmv,.webm,.ts,.m2ts,.mpg,.mpeg"
    ignored_directories: str = "$RECYCLE.BIN,System Volume Information,.Trash,.Trashes,@eaDir,sample,samples"
    playback_session_timeout_seconds: int = Field(default=90, ge=30, le=600)
    playback_max_streams: int = Field(default=8, ge=1, le=32)
    playback_streams_per_user: int = Field(default=2, ge=1, le=8)
    playback_watched_threshold: float = Field(default=90, ge=50, le=100)
    playback_minimum_watch_seconds: int = Field(default=30, ge=5, le=300)
    playback_history_days: int = Field(default=365, ge=0, le=3650)
    transcode_max_processes: int = Field(default=2, ge=1, le=8)
    transcode_threads: int = Field(default=2, ge=1, le=16)
    transcode_max_height: int = Field(default=2160, ge=240, le=4320)
    transcode_max_bitrate_kbps: int = Field(default=12000, ge=500, le=50000)
    transcode_max_storage_mb: int = Field(default=4096, ge=64, le=1048576)
    transcode_startup_timeout_seconds: int = Field(default=45, ge=5, le=180)
    transcode_hardware: str = "software"

    @field_validator("app_secret_key")
    @classmethod
    def reject_placeholder_secret(cls, value: str) -> str:
        lowered = value.casefold()
        placeholders = (
            "generate_with_bootstrap_do_not_use",
            "development-only",
            "change-me",
            "changeme",
        )
        if any(marker in lowered for marker in placeholders) or len(set(value)) < 12:
            raise ValueError("APP_SECRET_KEY must be a securely generated, non-placeholder secret")
        return value

    @property
    def allowed_media_roots(self) -> tuple[Path, ...]:
        if not self.media_roots.strip():
            return ()
        return tuple(Path(value).expanduser().resolve() for value in self.media_roots.split(os.pathsep) if value)

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset(
            value.strip().lower() if value.strip().startswith(".") else f".{value.strip().lower()}"
            for value in self.scan_extensions.split(",")
            if value.strip()
        )

    @property
    def ignored_directory_names(self) -> frozenset[str]:
        return frozenset(value.strip().casefold() for value in self.ignored_directories.split(",") if value.strip())


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
