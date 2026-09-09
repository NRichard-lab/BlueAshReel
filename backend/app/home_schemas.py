"""The local Home contract; the encrypted adapter replaces IDs/artwork URLs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HomeCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    library_id: str
    kind: Literal["movie", "series", "episode", "other"]
    title: str
    year: int | None
    available: bool
    file_id: str | None
    duration_seconds: float | None
    height: int | None
    poster_url: str | None
    position_seconds: float
    watched: bool
    completion: float
    season_number: int | None
    episode_number: int | None
    show_id: str | None
    show_title: str | None
    added_at: datetime


class HomeLibrary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    library_type: Literal["movies", "tv", "other"]
    enabled: bool


class HomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    continue_watching: list[HomeCard] = Field(alias="continue")
    recent_movies: list[HomeCard]
    recent_episodes: list[HomeCard]
    libraries: list[HomeLibrary]
    # Keep the earlier local/relay clients' keys without adding more UI rails.
    movies: list[HomeCard]
    shows: list[HomeCard]
    recent_watched: list[HomeCard]
