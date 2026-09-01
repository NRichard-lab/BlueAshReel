from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_utc_datetimes(self, value: Any) -> Any:
        if not isinstance(value, datetime):
            return value
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.isoformat().replace("+00:00", "Z")


class ProductPublic(ApiModel):
    name: str
    subtitle: str
    version: str
    api_prefix: str


class SetupStatus(ApiModel):
    setup_required: bool
    product: ProductPublic


class InitialLibrary(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    library_type: Literal["movies", "tv", "other"]
    paths: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Library name cannot be blank")
        return value


class OwnerSetupRequest(ApiModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=1024)
    application_data_directory: str | None = None
    temporary_directory: str | None = None
    initial_library: InitialLibrary | None = None

    @field_validator("password")
    @classmethod
    def sufficiently_varied(cls, value: str) -> str:
        if value.isspace() or len(set(value)) < 4:
            raise ValueError("Password must contain a reasonable variety of characters")
        return value

    @field_validator("username")
    @classmethod
    def username_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Username cannot be blank")
        return value


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class UserPublic(ApiModel):
    id: str
    username: str
    roles: list[str]


class AuthResponse(ApiModel):
    user: UserPublic
    csrf_token: str


class SetupResponse(AuthResponse):
    health: dict[str, bool]


class LibraryPathCreate(ApiModel):
    path: str = Field(min_length=1, max_length=4096)


class LibraryPathPublic(ApiModel):
    id: str
    path: str
    enabled: bool


class LibraryCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    library_type: Literal["movies", "tv", "other"]
    enabled: bool = True
    paths: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Library name cannot be blank")
        return value


class LibraryUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Library name cannot be blank")
        return value


class LibraryPublic(ApiModel):
    id: str
    name: str
    library_type: str
    enabled: bool
    paths: list[LibraryPathPublic]
    last_successful_scan_at: datetime | None
    media_count: int = 0
    available_file_count: int = 0
    error_count: int = 0
    active_job_id: str | None = None


class LibraryList(ApiModel):
    items: list[LibraryPublic]
    total: int
    page: int
    page_size: int


class ScanRequest(ApiModel):
    mode: Literal["full", "changed"] = "changed"


class JobAccepted(ApiModel):
    job_id: str
    status: str


class JobPublic(ApiModel):
    id: str
    job_type: str
    status: str
    progress_current: int
    progress_total: int | None
    attempts: int
    max_attempts: int
    cancel_requested: bool
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    scan: dict[str, Any] | None = None


class JobList(ApiModel):
    items: list[JobPublic]
    total: int
    page: int
    page_size: int


class MediaFileSummary(ApiModel):
    id: str
    available: bool
    container: str | None
    duration_seconds: float | None
    analysis_error: str | None
    video_streams: int
    audio_streams: int
    subtitle_streams: int
    video: list[dict[str, Any]] = Field(default_factory=list)
    audio: list[dict[str, Any]] = Field(default_factory=list)
    subtitles: list[dict[str, Any]] = Field(default_factory=list)


class MediaPublic(ApiModel):
    id: str
    library_id: str
    kind: str
    title: str
    year: int | None
    match_confidence: float
    available: bool
    files: list[MediaFileSummary] = Field(default_factory=list)
    file_total: int = 0
    file_page: int = 1


class MediaList(ApiModel):
    items: list[MediaPublic]
    total: int
    page: int
    page_size: int


class SettingUpdate(ApiModel):
    scan_extensions: list[str] | None = Field(default=None, max_length=100)
    ignored_directories: list[str] | None = Field(default=None, max_length=100)


class SettingsPublic(ApiModel):
    application_data_directory: str
    temporary_directory: str
    artwork_directory: str
    scan_extensions: list[str]
    ignored_directories: list[str]


class PrivacyUpdate(ApiModel):
    integrations: dict[str, bool]


class PrivacyPublic(ApiModel):
    local_only: bool = True
    telemetry_enabled: bool = False
    runtime_outbound_allowed: bool
    integrations: dict[str, bool]


class DashboardPublic(ApiModel):
    server_status: str
    database_status: str
    ffprobe_available: bool
    ffmpeg_available: bool
    library_count: int
    media_count: int
    active_job_count: int
    last_scan_at: datetime | None
    storage: dict[str, bool]
    storage_details: dict[str, dict[str, Any]]


class AuditPublic(ApiModel):
    id: int
    event_type: str
    target_type: str | None
    target_id: str | None
    outcome: str
    details: dict[str, Any]
    created_at: datetime


class AuditList(ApiModel):
    items: list[AuditPublic]
    total: int
    page: int
    page_size: int
