from __future__ import annotations

import http.client
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlencode

from app.metadata.provider import ProviderError
from app.metadata.types import ArtworkSource, Candidate, Credit, MetadataDetails

API_HOST = "api.themoviedb.org"
IMAGE_HOST = "image.tmdb.org"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ATTRIBUTION_NOTICE = "This product uses the TMDB API but is not endorsed or certified by TMDB."
_IMAGE_PATH = re.compile(r"/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)", re.IGNORECASE)


@dataclass(frozen=True)
class HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self, host: str, path: str, headers: Mapping[str, str], timeout: float, max_bytes: int
    ) -> HTTPResponse: ...

    def close(self) -> None: ...


class HTTPSConnectionTransport:
    """Two bounded reusable TLS connections. Redirects are never followed."""

    def __init__(self) -> None:
        self._connections: dict[str, http.client.HTTPSConnection] = {}
        self._lock = threading.Lock()

    def request(self, host: str, path: str, headers: Mapping[str, str], timeout: float, max_bytes: int) -> HTTPResponse:
        if host not in (API_HOST, IMAGE_HOST) or not path.startswith("/") or path.startswith("//"):
            raise ProviderError("invalid_origin")
        with self._lock:
            connection = self._connections.get(host)
            if connection is None:
                connection = http.client.HTTPSConnection(host, timeout=timeout)
                self._connections[host] = connection
            try:
                connection.request("GET", path, headers=dict(headers))
                response = connection.getresponse()
                response_headers = {key.lower(): value for key, value in response.getheaders()}
                size = response_headers.get("content-length", "")
                if size.isdigit() and int(size) > max_bytes:
                    raise ProviderError("response_too_large")
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ProviderError("response_too_large")
                return HTTPResponse(response.status, response_headers, body)
            except (OSError, http.client.HTTPException, ProviderError):
                connection.close()
                self._connections.pop(host, None)
                raise

    def close(self) -> None:
        with self._lock:
            for connection in self._connections.values():
                connection.close()
            self._connections.clear()


def _text(value: object, limit: int = 500) -> str | None:
    return value.strip()[:limit] or None if isinstance(value, str) else None


def _integer(value: object, *, minimum: int = 0) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= 2**31 - 1 else None


def _number(value: object, maximum: float) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= maximum
        and math.isfinite(value)
    ):
        return float(value)
    return None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: object, limit: int = 100) -> list[dict[str, Any]]:
    return [row for row in value[:limit] if isinstance(row, dict)] if isinstance(value, list) else []


def _date(value: object) -> str | None:
    text = _text(value, 32)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _names(value: object, limit: int = 20) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for row in _rows(value, limit) if (name := _text(row.get("name")))))


def _id(value: object) -> str | None:
    number = _integer(value, minimum=1)
    return str(number) if number is not None else None


def _valid_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", value) or int(value) > 2**31 - 1:
        raise ProviderError("invalid_identifier")
    return value


def _coordinate(value: int | None, minimum: int) -> int:
    number = _integer(value, minimum=minimum)
    if number is None or number > 10000:
        raise ProviderError("invalid_coordinate")
    return number


class TMDBMetadataProvider:
    name = "tmdb"

    def __init__(
        self,
        token: str,
        *,
        timeout_seconds: float = 10,
        retries: int = 2,
        language: str = "en-US",
        region: str = "US",
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not token
            or len(token) > 8192
            or not token.isascii()
            or any(char.isspace() or ord(char) < 32 for char in token)
        ):
            raise ProviderError("unconfigured")
        if not 1 <= timeout_seconds <= 60 or _integer(retries) is None or not 0 <= retries <= 3:
            raise ValueError("Invalid metadata request limits")
        if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", language) or not re.fullmatch(r"[A-Z]{2}", region):
            raise ValueError("Invalid metadata locale")
        self._token = token
        self._timeout = timeout_seconds
        self._retries = retries
        self._language = language
        self._region = region
        self._transport = transport or HTTPSConnectionTransport()
        self._sleep = sleep
        self._monotonic = monotonic
        self._retry_until = 0.0
        self._unavailable_until = 0.0
        self._configuration: dict[str, Any] | None = None

    def close(self) -> None:
        self._transport.close()

    def _request(self, host: str, path: str, *, image: bool = False) -> HTTPResponse:
        remaining = self._retry_until - self._monotonic()
        if remaining > 0:
            raise ProviderError("rate_limited", 429, remaining)
        if self._unavailable_until > self._monotonic():
            raise ProviderError("temporarily_unavailable")
        headers = {"User-Agent": "BlueAshReel-Agent/0.1 (local metadata cache)", "Accept": "application/json"}
        if host == API_HOST and not image:
            headers["Authorization"] = f"Bearer {self._token}"
        elif host == IMAGE_HOST and image:
            headers["Accept"] = "image/jpeg,image/png,image/webp"
        else:
            raise ProviderError("invalid_origin")
        limit = MAX_IMAGE_BYTES if image else MAX_JSON_BYTES
        for attempt in range(self._retries + 1):
            try:
                response = self._transport.request(host, path, headers, self._timeout, limit)
            except (OSError, http.client.HTTPException):
                if attempt == self._retries:
                    self._unavailable_until = self._monotonic() + 30
                    raise ProviderError("temporarily_unavailable") from None
                self._sleep(0.5 * 2**attempt)
                continue
            if len(response.body) > limit:
                raise ProviderError("response_too_large")
            if response.status == 200:
                return response
            if response.status in (401, 403):
                raise ProviderError("authentication_failed", response.status)
            if response.status == 404:
                raise ProviderError("not_found", 404)
            if response.status == 429:
                delay = self._retry_after(response.headers)
                if attempt == self._retries or delay > 10:
                    self._retry_until = self._monotonic() + delay
                    raise ProviderError("rate_limited", 429, delay)
                self._sleep(delay)
                continue
            if 500 <= response.status <= 599:
                if attempt < self._retries:
                    self._sleep(0.5 * 2**attempt)
                    continue
                self._unavailable_until = self._monotonic() + 30
                raise ProviderError("temporarily_unavailable", response.status)
            raise ProviderError("request_rejected", response.status)
        raise ProviderError("temporarily_unavailable")

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float:
        value = next((value for key, value in headers.items() if key.lower() == "retry-after"), "")
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds >= 0:
                return max(1.0, min(seconds, 86400.0))
        except ValueError:
            pass
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(1.0, min((parsed - datetime.now(UTC)).total_seconds(), 86400.0))
        except (TypeError, ValueError, OverflowError):
            return 2.0

    def _json(self, path: str, **query: object) -> dict[str, Any]:
        params: dict[str, object] = {"language": self._language, **query}
        response = self._request(API_HOST, "/3" + path + "?" + urlencode(params))
        try:
            result = json.loads(response.body)
        except (ValueError, UnicodeError, RecursionError):
            raise ProviderError("malformed_response") from None
        if not isinstance(result, dict) or result.get("success") is False:
            raise ProviderError("malformed_response")
        return result

    def search_movie(self, title: str, year: int | None = None) -> tuple[Candidate, ...]:
        return self._search("movie", title, year)

    def search_series(self, title: str, year: int | None = None) -> tuple[Candidate, ...]:
        return self._search("series", title, year)

    def _search(self, kind: str, title: str, year: int | None) -> tuple[Candidate, ...]:
        if not title.strip() or len(title) > 500:
            return ()
        query: dict[str, object] = {"query": title, "include_adult": "false", "page": 1}
        if year is not None and 1800 <= year <= 2200:
            query["year" if kind == "movie" else "first_air_date_year"] = year
        data = self._json("/search/movie" if kind == "movie" else "/search/tv", **query)
        if not isinstance(data.get("results"), list):
            raise ProviderError("malformed_response")
        candidates: list[Candidate] = []
        for row in _rows(data["results"], 40):
            title_key = "title" if kind == "movie" else "name"
            release_date = _date(row.get("release_date" if kind == "movie" else "first_air_date"))
            provider_id, name = _id(row.get("id")), _text(row.get(title_key))
            if provider_id and name and row.get("adult") is not True:
                candidates.append(
                    Candidate(
                        "tmdb",
                        provider_id,
                        kind,
                        name,
                        _text(row.get("original_" + title_key)),
                        int(release_date[:4]) if release_date else None,
                    )
                )
        return tuple(candidates)

    def get_movie(self, provider_id: str) -> MetadataDetails:
        data = self._json(
            f"/movie/{_valid_id(provider_id)}",
            append_to_response="credits,external_ids,images,release_dates,recommendations",
            include_image_language="en,null",
        )
        if _id(data.get("id")) != provider_id:
            raise ProviderError("malformed_response")
        return self._details(data, "movie")

    def get_series(self, provider_id: str) -> MetadataDetails:
        data = self._json(
            f"/tv/{_valid_id(provider_id)}",
            append_to_response="credits,external_ids,images,content_ratings,recommendations",
            include_image_language="en,null",
        )
        if _id(data.get("id")) != provider_id:
            raise ProviderError("malformed_response")
        return self._details(data, "series")

    def get_season(self, series_id: str, season_number: int) -> MetadataDetails:
        path = self._resource("season", series_id, season_number, None)
        data = self._json(path, append_to_response="credits,external_ids,images", include_image_language="en,null")
        if _integer(data.get("season_number", season_number)) != season_number:
            raise ProviderError("malformed_response")
        return self._details(data, "season", series_id=series_id, season_number=season_number)

    def get_episode(self, series_id: str, season_number: int, episode_number: int) -> MetadataDetails:
        path = self._resource("episode", series_id, season_number, episode_number)
        data = self._json(path, append_to_response="credits,external_ids,images", include_image_language="en,null")
        if (
            _integer(data.get("season_number", season_number)) != season_number
            or _integer(data.get("episode_number", episode_number), minimum=1) != episode_number
        ):
            raise ProviderError("malformed_response")
        return self._details(
            data, "episode", series_id=series_id, season_number=season_number, episode_number=episode_number
        )

    @staticmethod
    def _resource(kind: str, provider_id: str, season_number: int | None, episode_number: int | None) -> str:
        identifier = _valid_id(provider_id)
        if kind == "movie":
            return f"/movie/{identifier}"
        if kind == "series":
            return f"/tv/{identifier}"
        if kind not in ("season", "episode"):
            raise ProviderError("invalid_kind")
        path = f"/tv/{identifier}/season/{_coordinate(season_number, 0)}"
        return path if kind == "season" else f"{path}/episode/{_coordinate(episode_number, 1)}"

    def get_images(
        self, kind: str, provider_id: str, season_number: int | None = None, episode_number: int | None = None
    ) -> tuple[ArtworkSource, ...]:
        path = self._resource(kind, provider_id, season_number, episode_number)
        return self._images(self._json(path + "/images", include_image_language="en,null"), {}, kind)

    def get_external_ids(
        self, kind: str, provider_id: str, season_number: int | None = None, episode_number: int | None = None
    ) -> dict[str, str]:
        path = self._resource(kind, provider_id, season_number, episode_number)
        data = self._json(path + "/external_ids")
        return self._external_ids(data, _id(data.get("id")) or (provider_id if kind in ("movie", "series") else None))

    def get_credits(
        self, kind: str, provider_id: str, season_number: int | None = None, episode_number: int | None = None
    ) -> tuple[Credit, ...]:
        path = self._resource(kind, provider_id, season_number, episode_number)
        return self._credits(self._json(path + "/credits"))

    @staticmethod
    def _external_ids(data: dict[str, Any], provider_id: str | None) -> dict[str, str]:
        result = {"tmdb": provider_id} if provider_id else {}
        imdb = _text(data.get("imdb_id"), 32)
        if imdb and re.fullmatch(r"tt[0-9]{1,12}", imdb):
            result["imdb"] = imdb
        tvdb = _id(data.get("tvdb_id"))
        if tvdb:
            result["tvdb"] = tvdb
        return result

    def _certification(self, data: dict[str, Any], kind: str) -> str | None:
        if kind == "movie":
            rows = _rows(_mapping(data.get("release_dates")).get("results"))
            for region in rows:
                if region.get("iso_3166_1") != self._region:
                    continue
                releases = _rows(region.get("release_dates"))
                releases.sort(key=lambda row: (row.get("type") not in (3, 2), _text(row.get("release_date")) or ""))
                for release in releases:
                    if rating := _text(release.get("certification"), 32):
                        return rating
        if kind == "series":
            for row in _rows(_mapping(data.get("content_ratings")).get("results")):
                if row.get("iso_3166_1") == self._region and (rating := _text(row.get("rating"), 32)):
                    return rating
        return None

    def _credits(self, data: dict[str, Any]) -> tuple[Credit, ...]:
        result: list[Credit] = []
        seen: set[tuple[str, str, str | None]] = set()
        cast = _rows(data.get("cast")) + _rows(data.get("guest_stars"), 20)
        cast.sort(key=lambda row: (_integer(row.get("order")) or 0, _id(row.get("id")) or ""))
        for row in cast:
            identifier, name = _id(row.get("id")), _text(row.get("name"))
            if not identifier or not name or any(credit.provider_person_id == identifier for credit in result):
                continue
            profile = self._source(row.get("profile_path"), "profile", person_id=identifier)
            result.append(
                Credit(identifier, name, "cast", _text(row.get("character")), order=len(result), profile=profile)
            )
            if len(result) == 10:
                break
        for row in _rows(data.get("crew"), 200):
            identifier, name, job = _id(row.get("id")), _text(row.get("name")), _text(row.get("job"), 100)
            if not identifier or not name or job not in ("Director", "Writer", "Screenplay", "Story", "Creator"):
                continue
            key = (identifier, "crew", job)
            if key not in seen:
                seen.add(key)
                result.append(Credit(identifier, name, "crew", job=job, order=len(result)))
            if len(result) >= 30:
                break
        return tuple(result)

    @staticmethod
    def _source(
        path: object, kind: str, *, person_id: str | None = None, row: dict[str, Any] | None = None
    ) -> ArtworkSource | None:
        if not isinstance(path, str) or not _IMAGE_PATH.fullmatch(path):
            return None
        details = row or {}
        return ArtworkSource(
            "tmdb",
            path,
            kind,
            person_id,
            _integer(details.get("width"), minimum=1),
            _integer(details.get("height"), minimum=1),
            _text(details.get("iso_639_1"), 8),
        )

    def _images(self, images: dict[str, Any], data: dict[str, Any], kind: str) -> tuple[ArtworkSource, ...]:
        kinds = ("still",) if kind == "episode" else (("poster",) if kind == "season" else ("poster", "backdrop"))
        result: list[ArtworkSource] = []
        for image_kind in kinds:
            candidates: list[tuple[tuple[float, ...], str, ArtworkSource]] = []
            for row in _rows(images.get(image_kind + "s"), 250):
                source = self._source(row.get("file_path"), image_kind, row=row)
                if source is None or source.language not in (None, "en"):
                    continue
                aspect = source.width / source.height if source.width and source.height else None
                if aspect and not ((0.45 <= aspect <= 0.85) if image_kind == "poster" else (1.3 <= aspect <= 2.5)):
                    continue
                rank = (
                    0.0 if source.language == "en" else 1.0,
                    -float(min(source.width or 0, 2000)),
                    -(_number(row.get("vote_average"), 10) or 0),
                    -float(_integer(row.get("vote_count")) or 0),
                )
                candidates.append((rank, source.provider_path, source))
            candidates.sort(key=lambda row: (row[0], row[1]))
            source = candidates[0][2] if candidates else self._source(data.get(image_kind + "_path"), image_kind)
            if source:
                result.append(source)
        return tuple(result)

    def _details(
        self,
        data: dict[str, Any],
        kind: str,
        *,
        series_id: str | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> MetadataDetails:
        provider_id = _id(data.get("id"))
        title_key = "title" if kind == "movie" else "name"
        title = _text(data.get(title_key))
        if not provider_id or not title:
            raise ProviderError("malformed_response")
        release_date = _date(
            data.get("release_date" if kind == "movie" else "first_air_date" if kind == "series" else "air_date")
        )
        credits_data = _mapping(data.get("credits"))
        if kind == "episode":
            credits_data = {
                **credits_data,
                "crew": credits_data.get("crew", data.get("crew", [])),
                "guest_stars": data.get("guest_stars", credits_data.get("guest_stars", [])),
            }
        credits = self._credits(credits_data)
        runtime = _integer(data.get("runtime"), minimum=1)
        runtimes = data.get("episode_run_time")
        if runtime is None and kind == "series" and isinstance(runtimes, list):
            runtime = next((number for value in runtimes[:10] if (number := _integer(value, minimum=1))), None)
        external = {
            **_mapping(data.get("external_ids")),
            "imdb_id": data.get("imdb_id") or _mapping(data.get("external_ids")).get("imdb_id"),
        }
        recommendations = tuple(
            dict.fromkeys(
                identifier
                for row in _rows(_mapping(data.get("recommendations")).get("results"), 40)
                if (identifier := _id(row.get("id")))
            )
        )
        artwork = self._images(_mapping(data.get("images")), data, kind)
        artwork += tuple(credit.profile for credit in credits if credit.profile is not None)
        episodes: list[MetadataDetails] = []
        if kind == "season":
            for row in _rows(data.get("episodes"), 1000):
                if _id(row.get("id")) and _text(row.get("name")) and _integer(row.get("episode_number"), minimum=1):
                    episodes.append(
                        self._details(
                            row,
                            "episode",
                            series_id=series_id,
                            season_number=season_number,
                            episode_number=_integer(row.get("episode_number"), minimum=1),
                        )
                    )
        return MetadataDetails(
            provider="tmdb",
            provider_id=provider_id,
            kind=kind,
            title=title,
            original_title=_text(data.get("original_" + title_key)),
            year=int(release_date[:4]) if release_date else None,
            release_date=release_date,
            runtime_seconds=runtime * 60 if runtime is not None and runtime <= 1440 else None,
            overview=_text(data.get("overview"), 20000),
            tagline=_text(data.get("tagline"), 2000),
            original_language=_text(data.get("original_language"), 16),
            content_rating=self._certification(data, kind),
            genres=_names(data.get("genres")),
            studios=_names(data.get("production_companies")),
            countries=_names(data.get("production_countries")),
            networks=_names(data.get("networks")),
            creators=_names(data.get("created_by")),
            directors=tuple(dict.fromkeys(credit.name for credit in credits if credit.job == "Director")),
            writers=tuple(
                dict.fromkeys(credit.name for credit in credits if credit.job in ("Writer", "Screenplay", "Story"))
            ),
            vote_average=_number(data.get("vote_average"), 10),
            vote_count=_integer(data.get("vote_count")),
            status=_text(data.get("status"), 100),
            last_air_date=_date(data.get("last_air_date")),
            number_of_seasons=_integer(data.get("number_of_seasons")),
            number_of_episodes=len(episodes) if kind == "season" else _integer(data.get("number_of_episodes")),
            series_provider_id=series_id,
            season_number=season_number,
            episode_number=episode_number,
            external_ids=self._external_ids(external, provider_id),
            credits=credits,
            artwork=artwork,
            related_provider_ids=recommendations,
            episodes=tuple(episodes),
        )

    def download_image(self, source: ArtworkSource) -> tuple[bytes, str]:
        if source.provider != "tmdb" or not _IMAGE_PATH.fullmatch(source.provider_path):
            raise ProviderError("invalid_artwork")
        if source.kind not in ("poster", "backdrop", "still", "profile"):
            raise ProviderError("invalid_artwork")
        if self._configuration is None:
            self._configuration = _mapping(self._json("/configuration").get("images"))
        if self._configuration.get("secure_base_url") != "https://image.tmdb.org/t/p/":
            raise ProviderError("invalid_image_configuration")
        desired = {"poster": "w500", "backdrop": "w1280", "still": "w780", "profile": "w185"}[source.kind]
        sizes = self._configuration.get(source.kind + "_sizes")
        if not isinstance(sizes, list):
            raise ProviderError("invalid_image_configuration")
        available = [size for size in sizes if isinstance(size, str) and re.fullmatch(r"w[1-9][0-9]{1,3}", size)]
        if not available:
            raise ProviderError("invalid_image_configuration")
        size = (
            desired
            if desired in available
            else min(available, key=lambda value: (abs(int(value[1:]) - int(desired[1:])), value))
        )
        response = self._request(IMAGE_HOST, f"/t/p/{size}{source.provider_path}", image=True)
        content_type = (
            next((value for key, value in response.headers.items() if key.lower() == "content-type"), "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        body = response.body
        valid = (
            content_type == "image/jpeg"
            and body.startswith(b"\xff\xd8\xff")
            or content_type == "image/png"
            and body.startswith(b"\x89PNG\r\n\x1a\n")
            or content_type == "image/webp"
            and body.startswith(b"RIFF")
            and body[8:12] == b"WEBP"
        )
        if not valid:
            raise ProviderError("invalid_image_content")
        return body, content_type
