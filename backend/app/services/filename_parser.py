from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TV_PATTERNS = (
    re.compile(
        r"^(?P<series>.*?)[ ._\-]+[Ss](?P<season>\d{1,3})[ ._\-]*[Ee](?P<episode>\d{1,4})(?:[ ._\-]+(?P<title>.*?))?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?P<series>.*?)[ ._\-]+(?P<season>\d{1,3})x(?P<episode>\d{1,4})(?:[ ._\-]+(?P<title>.*?))?$",
        re.IGNORECASE,
    ),
)
_YEAR = re.compile(r"(?:^|[ ._\-(])(?P<year>(?:19|20)\d{2})(?:$|[ ._\-)])")
_NOISE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|bluray|blu-ray|webrip|web-dl|hdtv|dvdrip|x26[45]|h\.?26[45]|hevc|av1|remux)\b.*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedFilename:
    kind: str
    title: str
    year: int | None = None
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_title: str | None = None
    confidence: float = 0.25


def _clean(value: str) -> str:
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"\s*[-]+\s*", " - ", value)
    return re.sub(r"\s+", " ", value).strip(" -._")


def parse_filename(path: str | Path, library_type: str = "other") -> ParsedFilename:
    stem = Path(path).stem
    for pattern in _TV_PATTERNS:
        match = pattern.match(stem)
        if match:
            series_title = _clean(match.group("series")) or "Unknown Series"
            raw_episode_title = match.groupdict().get("title") or ""
            episode_title = _clean(_NOISE.sub("", raw_episode_title)) or None
            return ParsedFilename(
                kind="episode",
                title=episode_title or f"Episode {int(match.group('episode'))}",
                series_title=series_title,
                season_number=int(match.group("season")),
                episode_number=int(match.group("episode")),
                episode_title=episode_title,
                confidence=0.92,
            )

    year_match = _YEAR.search(stem)
    year = int(year_match.group("year")) if year_match else None
    title_source = stem[: year_match.start()] if year_match else stem
    cleaned = _clean(_NOISE.sub("", title_source)) or _clean(stem) or "Untitled"
    if library_type == "movies":
        return ParsedFilename(kind="movie", title=cleaned, year=year, confidence=0.78 if year else 0.55)
    return ParsedFilename(kind="other", title=cleaned, year=year, confidence=0.35)
