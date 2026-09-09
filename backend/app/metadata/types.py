from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArtworkSource:
    """An Agent-only provider reference, never a browser-facing image URL."""

    provider: str
    provider_path: str
    kind: str
    person_provider_id: str | None = None
    width: int | None = None
    height: int | None = None
    language: str | None = None


@dataclass(frozen=True)
class Candidate:
    provider: str
    provider_id: str
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    runtime_seconds: int | None = None
    alternate_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Credit:
    provider_person_id: str
    name: str
    role: str
    character: str | None = None
    job: str | None = None
    order: int = 0
    profile: ArtworkSource | None = None


@dataclass(frozen=True)
class MetadataDetails:
    """Normalized descriptive metadata, independent from local file analysis."""

    provider: str
    provider_id: str
    kind: str
    title: str
    original_title: str | None = None
    year: int | None = None
    release_date: str | None = None
    runtime_seconds: int | None = None
    overview: str | None = None
    tagline: str | None = None
    original_language: str | None = None
    content_rating: str | None = None
    genres: tuple[str, ...] = ()
    studios: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    creators: tuple[str, ...] = ()
    directors: tuple[str, ...] = ()
    writers: tuple[str, ...] = ()
    vote_average: float | None = None
    vote_count: int | None = None
    status: str | None = None
    last_air_date: str | None = None
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    series_provider_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    external_ids: dict[str, str] = field(default_factory=dict)
    credits: tuple[Credit, ...] = ()
    artwork: tuple[ArtworkSource, ...] = ()
    related_provider_ids: tuple[str, ...] = ()
    episodes: tuple[MetadataDetails, ...] = ()
