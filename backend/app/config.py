from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProductConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = "Home Media"
    package_name: str = "BlueAshReel"
    agent_name: str = "Blue Ash Reel Agent"
    server_name: str = "Blue Ash Reel Server"
    domain: str = "blueashreel.com"
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


class ApprovedMediaRoot(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=80)
    path: Path

    @field_validator("display_name")
    @classmethod
    def safe_display_name(cls, value: str) -> str:
        cleaned = "".join(character for character in value.strip() if character.isprintable())
        if not cleaned:
            raise ValueError("Media root display name cannot be blank")
        return cleaned


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
    deployment_mode: Literal["container", "native_windows"] = "container"
    windows_service_prefix: str = ""
    native_program_dir: Path | None = None
    native_data_dir: Path | None = None
    media_roots: str = ""
    media_root_definitions: str = ""
    outbound_integrations_enabled: bool = False
    # Provider credentials are private Agent configuration; never part of API models.
    tmdb_access_token: SecretStr | None = Field(default=None, exclude=True, repr=False)
    tmdb_token_file: Path | None = Field(default=None, exclude=True, repr=False)
    tmdb_timeout_seconds: float = Field(default=10, ge=1, le=30)
    tmdb_retries: int = Field(default=2, ge=0, le=3)
    metadata_language: str = Field(default="en-US", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    metadata_region: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    metadata_refresh_days: int = Field(default=30, ge=1, le=365)
    # Private connector/tray spool. Per-user native mode serves encrypted media
    # through its outbound canonical Portal connection; media data stays local.
    remote_control_dir: Path | None = None
    log_level: str = "INFO"
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"
    session_cookie_name: str = "media_session"
    csrf_cookie_name: str = "csrf_token"
    setup_cookie_name: str = "bluereel_setup"
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
    transcode_hardware: Literal["software", "qsv", "nvenc", "amf"] = "software"
    transcode_mode: Literal["automatic", "hardware_preferred", "software_only", "direct_only", "hardware_required"] = (
        "automatic"
    )
    transcode_cpu_preset: Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"] = "veryfast"
    transcode_allow_4k: bool = False
    transcode_device: str = Field(default="auto", pattern=r"^(auto|[0-9]{1,2})$")

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
    def tmdb_token(self) -> str:
        """Missing/unreadable/malformed credentials leave local indexing operational."""
        value = self.tmdb_access_token.get_secret_value() if self.tmdb_access_token else ""
        if not value and self.tmdb_token_file:
            try:
                from app.services.paths import assert_no_link_components

                assert_no_link_components(self.tmdb_token_file)
                if self.tmdb_token_file.stat().st_size <= 8192:
                    value = self.tmdb_token_file.read_text(encoding="utf-8-sig").strip()
            except (OSError, ValueError):
                return ""
        value = value.strip()
        if not 20 <= len(value) <= 8192 or any(not (c.isascii() and (c.isalnum() or c in "._-")) for c in value):
            return ""
        return value

    @property
    def approved_media_roots(self) -> tuple[ApprovedMediaRoot, ...]:
        if self.media_root_definitions.strip():
            try:
                raw = json.loads(self.media_root_definitions)
                roots = tuple(ApprovedMediaRoot.model_validate(item) for item in raw)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("MEDIA_ROOT_DEFINITIONS is not valid approved-root configuration") from exc
        else:
            paths = [value for value in self.media_roots.split(os.pathsep) if value]
            roots = tuple(
                ApprovedMediaRoot(
                    id="primary" if index == 0 else f"root_{index + 1}",
                    display_name="Media" if index == 0 else f"Media {index + 1}",
                    path=Path(value),
                )
                for index, value in enumerate(paths)
            )
        normalized = tuple(
            ApprovedMediaRoot(id=root.id, display_name=root.display_name, path=root.path.expanduser().absolute())
            for root in roots
        )
        # A native user confirms these roots on the workstation. The file is
        # private Agent state, read afresh so the scan worker sees approvals.
        if self.deployment_mode == "native_windows":
            from app.remote.storage import read_json
            approved = read_json(self.app_data_dir / "remote-roots.json").get("roots", [])
            known = {str(root.path.resolve(strict=False)) for root in normalized}
            normalized += tuple(ApprovedMediaRoot.model_validate(root) for root in approved
                                if str(Path(root["path"]).resolve(strict=False)) not in known)
        if len({root.id for root in normalized}) != len(normalized):
            raise ValueError("Approved media root identifiers must be unique")
        canonical_paths = tuple(root.path.resolve(strict=False) for root in normalized)
        if len({str(path) for path in canonical_paths}) != len(canonical_paths):
            raise ValueError("Approved media root paths must be unique")
        for index, _root in enumerate(normalized):
            canonical = canonical_paths[index]
            if canonical == Path(canonical.anchor):
                raise ValueError("Approved media roots cannot be filesystem roots")
            for other in canonical_paths[index + 1 :]:
                if canonical in other.parents or other in canonical.parents:
                    raise ValueError("Approved media roots cannot overlap")
        return normalized

    @property
    def allowed_media_roots(self) -> tuple[Path, ...]:
        return tuple(root.path.resolve(strict=False) for root in self.approved_media_roots)

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
