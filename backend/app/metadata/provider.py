from __future__ import annotations

from typing import Protocol

from app.metadata.types import ArtworkSource, Candidate, Credit, MetadataDetails


class ProviderError(Exception):
    """Safe error categories: no request URLs, token, filename, or provider body."""

    def __init__(self, code: str, status_code: int | None = None, retry_after_seconds: float | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class MetadataProvider(Protocol):
    name: str

    def search_movie(self, title: str, year: int | None = None) -> tuple[Candidate, ...]: ...

    def get_movie(self, provider_id: str) -> MetadataDetails: ...

    def search_series(self, title: str, year: int | None = None) -> tuple[Candidate, ...]: ...

    def get_series(self, provider_id: str) -> MetadataDetails: ...

    def get_season(self, series_id: str, season_number: int) -> MetadataDetails: ...

    def get_episode(self, series_id: str, season_number: int, episode_number: int) -> MetadataDetails: ...

    def get_images(
        self, kind: str, provider_id: str, season_number: int | None = None, episode_number: int | None = None
    ) -> tuple[ArtworkSource, ...]: ...

    def get_image_candidates(self, kind: str, provider_id: str) -> tuple[ArtworkSource, ...]: ...

    def get_external_ids(
        self, kind: str, provider_id: str, season_number: int | None = None, episode_number: int | None = None
    ) -> dict[str, str]: ...

    def get_credits(
        self, kind: str, provider_id: str, season_number: int | None = None, episode_number: int | None = None
    ) -> tuple[Credit, ...]: ...

    def download_image(self, source: ArtworkSource) -> tuple[bytes, str]: ...

    def close(self) -> None: ...
