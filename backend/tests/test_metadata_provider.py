from __future__ import annotations

import http.client
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from app.metadata.provider import ProviderError
from app.metadata.tmdb import (
    API_HOST,
    IMAGE_HOST,
    MAX_IMAGE_BYTES,
    HTTPResponse,
    HTTPSConnectionTransport,
    TMDBMetadataProvider,
)
from app.metadata.types import ArtworkSource


class FixtureTransport:
    def __init__(self, *responses: HTTPResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], float, int]] = []
        self.closed = False

    def request(self, host: str, path: str, headers: Mapping[str, str], timeout: float, max_bytes: int) -> HTTPResponse:
        self.calls.append((host, path, dict(headers), timeout, max_bytes))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self) -> None:
        self.closed = True


def reply(body: Any, status: int = 200, **headers: str) -> HTTPResponse:
    return HTTPResponse(status, {"content-type": "application/json", **headers}, json.dumps(body).encode())


def provider(*responses: HTTPResponse | Exception, retries: int = 0) -> tuple[TMDBMetadataProvider, FixtureTransport]:
    transport = FixtureTransport(*responses)
    return TMDBMetadataProvider("fixture-token", transport=transport, retries=retries, sleep=lambda _: None), transport


def test_movie_and_series_search_normalize_queries() -> None:
    client, transport = provider(
        reply(
            {
                "results": [
                    {"id": 603, "title": "The Matrix", "original_title": "The Matrix", "release_date": "1999-03-30"}
                ]
            }
        ),
        reply(
            {"results": [{"id": 114461, "name": "Ahsoka", "original_name": "Ahsoka", "first_air_date": "2023-08-22"}]}
        ),
    )
    movie = client.search_movie("The Matrix", 1999)[0]
    show = client.search_series("Ahsoka", 2023)[0]
    assert (movie.provider, movie.provider_id, movie.kind, movie.year) == ("tmdb", "603", "movie", 1999)
    assert (show.title, show.original_title, show.kind, show.year) == ("Ahsoka", "Ahsoka", "series", 2023)
    movie_query = parse_qs(urlsplit(transport.calls[0][1]).query)
    series_query = parse_qs(urlsplit(transport.calls[1][1]).query)
    assert movie_query["query"] == ["The Matrix"] and movie_query["year"] == ["1999"]
    assert series_query["first_air_date_year"] == ["2023"]
    assert transport.calls[0][2]["Authorization"] == "Bearer fixture-token"
    assert "fixture-token" not in transport.calls[0][1]
    assert "BlueAshReel" in transport.calls[0][2]["User-Agent"]


def test_movie_details_normalize_credits_external_ids_and_certification() -> None:
    client, transport = provider(
        reply(
            {
                "id": 603,
                "title": "The Matrix",
                "original_title": "The Matrix",
                "release_date": "1999-03-30",
                "runtime": 136,
                "overview": "A computer programmer discovers a hidden world.",
                "tagline": "Welcome.",
                "genres": [{"id": 878, "name": "Science Fiction"}],
                "production_companies": [{"name": "Warner Bros."}],
                "production_countries": [{"name": "United States of America"}],
                "original_language": "en",
                "vote_average": 8.2,
                "vote_count": 25000,
                "imdb_id": "tt0133093",
                "credits": {
                    "cast": [{"id": 6384, "name": "Keanu Reeves", "character": "Neo", "profile_path": "/keanu.jpg"}],
                    "crew": [
                        {"id": 1, "name": "A Director", "job": "Director"},
                        {"id": 2, "name": "A Writer", "job": "Screenplay"},
                    ],
                },
                "external_ids": {"imdb_id": "tt0133093", "facebook_id": "never-persist"},
                "release_dates": {
                    "results": [
                        {"iso_3166_1": "GB", "release_dates": [{"certification": "15", "type": 3}]},
                        {"iso_3166_1": "US", "release_dates": [{"certification": "R", "type": 3}]},
                    ]
                },
                "recommendations": {"results": [{"id": 604}, {"id": 604}, {"id": 605}]},
                "arbitrary_debug_blob": {"do": "not persist"},
            }
        )
    )
    details = client.get_movie("603")
    assert (details.title, details.runtime_seconds, details.year, details.content_rating) == (
        "The Matrix",
        8160,
        1999,
        "R",
    )
    assert details.external_ids == {"tmdb": "603", "imdb": "tt0133093"}
    assert details.genres == ("Science Fiction",) and details.studios == ("Warner Bros.",)
    assert details.directors == ("A Director",) and details.writers == ("A Writer",)
    assert details.vote_average == 8.2 and details.vote_count == 25000
    assert details.credits[0].character == "Neo" and details.credits[0].profile is not None
    assert details.related_provider_ids == ("604", "605")
    assert not hasattr(details, "arbitrary_debug_blob")
    query = parse_qs(urlsplit(transport.calls[0][1]).query)
    assert query["append_to_response"] == ["credits,external_ids,images,release_dates,recommendations"]
    assert query["include_image_language"] == ["en,null"]
    assert len(transport.calls) == 1


def test_series_season_and_episode_use_hierarchy_details() -> None:
    client, transport = provider(
        reply(
            {
                "id": 114461,
                "name": "Ahsoka",
                "first_air_date": "2023-08-22",
                "last_air_date": "2023-10-03",
                "episode_run_time": [47],
                "created_by": [{"name": "Dave Filoni"}],
                "networks": [{"name": "Disney+"}],
                "number_of_seasons": 1,
                "number_of_episodes": 8,
                "status": "Returning Series",
                "external_ids": {"imdb_id": "tt13622776", "tvdb_id": 393189},
                "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-14"}]},
            }
        ),
        reply(
            {
                "id": 123,
                "name": "Season 1",
                "season_number": 1,
                "poster_path": "/season.jpg",
                "air_date": "2023-08-22",
                "episodes": [
                    {
                        "id": 456,
                        "name": "Part Five: Shadow Warrior",
                        "episode_number": 5,
                        "season_number": 1,
                        "runtime": 52,
                        "still_path": "/still.jpg",
                    }
                ],
            }
        ),
        reply(
            {
                "id": 456,
                "name": "Part Five: Shadow Warrior",
                "season_number": 1,
                "episode_number": 5,
                "air_date": "2023-09-12",
                "runtime": 52,
                "vote_average": 8.6,
                "still_path": "/still.jpg",
                "crew": [{"id": 10, "name": "Dave Filoni", "job": "Director"}],
                "guest_stars": [{"id": 11, "name": "Hayden Christensen", "character": "Anakin Skywalker"}],
            }
        ),
    )
    series = client.get_series("114461")
    season = client.get_season("114461", 1)
    episode = client.get_episode("114461", 1, 5)
    assert series.creators == ("Dave Filoni",) and series.networks == ("Disney+",)
    assert series.external_ids["tvdb"] == "393189" and series.content_rating == "TV-14"
    assert series.number_of_seasons == 1 and series.number_of_episodes == 8
    assert series.runtime_seconds == 2820 and series.last_air_date == "2023-10-03"
    assert season.series_provider_id == "114461" and season.season_number == 1
    assert season.episodes[0].episode_number == 5 and season.number_of_episodes == 1
    assert episode.title == "Part Five: Shadow Warrior" and episode.release_date == "2023-09-12"
    assert episode.directors == ("Dave Filoni",) and episode.credits[0].name == "Hayden Christensen"
    assert episode.artwork[0].kind == "still"
    paths = [urlsplit(call[1]).path for call in transport.calls]
    assert paths == ["/3/tv/114461", "/3/tv/114461/season/1", "/3/tv/114461/season/1/episode/5"]


def test_credits_bound_principal_cast_profiles_and_relevant_crew() -> None:
    client, _ = provider(
        reply(
            {
                "cast": [
                    {"id": value, "name": f"Person {value}", "order": value, "profile_path": f"/person{value}.jpg"}
                    for value in reversed(range(1, 51))
                ],
                "crew": [
                    {"id": 90, "name": "Director", "job": "Director"},
                    {"id": 91, "name": "Crew member", "job": "Grip"},
                ],
            }
        )
    )
    credits = client.get_credits("movie", "1")
    assert len(credits) == 11
    assert [credit.name for credit in credits[:10]] == [f"Person {value}" for value in range(1, 11)]
    assert sum(credit.profile is not None for credit in credits) == 10
    assert credits[-1].job == "Director"


def test_external_ids_do_not_include_social_or_wrong_shape_values() -> None:
    client, _ = provider(reply({"id": 1, "imdb_id": "tt123", "tvdb_id": 555, "instagram_id": "secret"}))
    assert client.get_external_ids("series", "1") == {"tmdb": "1", "imdb": "tt123", "tvdb": "555"}


def test_artwork_prefers_english_then_neutral_and_sensible_aspect() -> None:
    client, _ = provider(
        reply(
            {
                "posters": [
                    {"file_path": "/neutral.jpg", "width": 2000, "height": 3000, "iso_639_1": None},
                    {"file_path": "/english.jpg", "width": 500, "height": 750, "iso_639_1": "en"},
                    {"file_path": "/wrongaspect.jpg", "width": 5000, "height": 100, "iso_639_1": "en"},
                    {"file_path": "https://evil.invalid/image.jpg", "iso_639_1": "en"},
                ],
                "backdrops": [
                    {"file_path": "/foreign.jpg", "iso_639_1": "de"},
                    {"file_path": "/neutralbackdrop.jpg", "width": 1280, "height": 720},
                ],
            }
        )
    )
    artwork = client.get_images("movie", "1")
    assert [source.provider_path for source in artwork] == ["/english.jpg", "/neutralbackdrop.jpg"]


def test_artwork_download_uses_configuration_and_no_bearer_on_image_origin() -> None:
    configuration = {
        "images": {"secure_base_url": "https://image.tmdb.org/t/p/", "poster_sizes": ["w342", "w500", "original"]}
    }
    content = b"\xff\xd8\xfffixture-image"
    client, transport = provider(
        reply(configuration),
        HTTPResponse(200, {"Content-Type": "image/jpeg"}, content),
        HTTPResponse(200, {"content-type": "image/jpeg"}, content),
    )
    source = ArtworkSource("tmdb", "/poster.jpg", "poster")
    assert client.download_image(source) == (content, "image/jpeg")
    assert client.download_image(source) == (content, "image/jpeg")
    assert [call[0] for call in transport.calls] == [API_HOST, IMAGE_HOST, IMAGE_HOST]
    assert transport.calls[1][1] == "/t/p/w500/poster.jpg"
    assert "Authorization" not in transport.calls[1][2]
    assert transport.calls[1][4] == MAX_IMAGE_BYTES


@pytest.mark.parametrize(
    "path", ["//evil.invalid/x.jpg", "https://evil.invalid/x.jpg", "/../x.jpg", "/x.svg", "/x.jpg?token=x"]
)
def test_artwork_rejects_arbitrary_origins_and_paths(path: str) -> None:
    client, transport = provider()
    with pytest.raises(ProviderError, match="invalid_artwork"):
        client.download_image(ArtworkSource("tmdb", path, "poster"))
    assert transport.calls == []


def test_artwork_rejects_untrusted_configuration_and_mismatched_payload() -> None:
    client, transport = provider(
        reply({"images": {"secure_base_url": "https://evil.invalid/", "poster_sizes": ["w500"]}})
    )
    with pytest.raises(ProviderError, match="invalid_image_configuration"):
        client.download_image(ArtworkSource("tmdb", "/x.jpg", "poster"))
    assert len(transport.calls) == 1
    client, _ = provider(
        reply({"images": {"secure_base_url": "https://image.tmdb.org/t/p/", "poster_sizes": ["w500"]}}),
        HTTPResponse(200, {"content-type": "image/jpeg"}, b"<html>not an image</html>"),
    )
    with pytest.raises(ProviderError, match="invalid_image_content"):
        client.download_image(ArtworkSource("tmdb", "/x.jpg", "poster"))


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "authentication_failed"), (403, "authentication_failed"), (404, "not_found"), (302, "request_rejected")],
)
def test_permanent_failures_are_sanitized_and_never_retried(status: int, code: str) -> None:
    client, transport = provider(reply({"status_message": "fixture-token should never be exposed"}, status), retries=2)
    with pytest.raises(ProviderError) as caught:
        client.get_movie("1")
    assert caught.value.code == code and caught.value.status_code == status
    assert "fixture-token" not in str(caught.value) and len(transport.calls) == 1


@pytest.mark.parametrize(
    "failure",
    [TimeoutError("private path or token"), http.client.RemoteDisconnected("closed"), reply({}, 500), reply({}, 503)],
)
def test_transient_failures_retry_then_succeed(failure: HTTPResponse | Exception) -> None:
    client, transport = provider(failure, reply({"id": 1, "title": "Recovered"}), retries=1)
    assert client.get_movie("1").title == "Recovered" and len(transport.calls) == 2


def test_retries_are_bounded_and_timeout_error_sanitized() -> None:
    client, transport = provider(*(TimeoutError("fixture-token") for _ in range(3)), retries=2)
    with pytest.raises(ProviderError, match="temporarily_unavailable") as caught:
        client.get_movie("1")
    assert "fixture-token" not in str(caught.value) and len(transport.calls) == 3


@pytest.mark.parametrize("failure", [TimeoutError("private"), reply({}, 503)])
def test_transient_failure_exhaustion_starts_provider_cooldown(failure: HTTPResponse | Exception) -> None:
    client, transport = provider(failure)
    for _ in range(2):
        with pytest.raises(ProviderError, match="temporarily_unavailable"):
            client.get_movie("1")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("identifier", [True, False, 2, "1"])
def test_boolean_and_mismatched_response_identifiers_fail(identifier: object) -> None:
    client, _ = provider(reply({"id": identifier, "title": "Invalid identity"}))
    with pytest.raises(ProviderError, match="malformed_response"):
        client.get_movie("1")


def test_mismatched_episode_coordinate_fails() -> None:
    client, _ = provider(reply({"id": 25, "name": "Wrong episode", "season_number": 2, "episode_number": 5}))
    with pytest.raises(ProviderError, match="malformed_response"):
        client.get_episode("1", 1, 5)


def test_oversized_vote_number_remains_empty_instead_of_overflowing() -> None:
    client, _ = provider(reply({"id": 1, "title": "Minimal", "vote_average": 10**500}))
    assert client.get_movie("1").vote_average is None


def test_rate_limit_honors_short_retry_after_and_defers_long_cooldown() -> None:
    sleeps: list[float] = []
    transport = FixtureTransport(reply({}, 429, **{"Retry-After": "2"}), reply({"id": 1, "title": "Recovered"}))
    client = TMDBMetadataProvider("fixture-token", transport=transport, retries=1, sleep=sleeps.append)
    assert client.get_movie("1").title == "Recovered" and sleeps == [2.0]
    client, transport = provider(reply({}, 429, **{"retry-after": "120"}), retries=2)
    for _ in range(2):
        with pytest.raises(ProviderError, match="rate_limited") as caught:
            client.get_movie("1")
        assert caught.value.retry_after_seconds is not None and caught.value.retry_after_seconds > 100
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        HTTPResponse(200, {}, b"not-json"),
        reply([]),
        reply({"success": False}),
        reply({"id": "wrong", "title": "Invalid"}),
        reply({"id": 1}),
    ],
)
def test_malformed_details_fail_closed(response: HTTPResponse) -> None:
    client, _ = provider(response)
    with pytest.raises(ProviderError, match="malformed_response"):
        client.get_movie("1")


def test_missing_optional_fields_and_wrong_shapes_remain_empty() -> None:
    client, _ = provider(
        reply(
            {
                "id": 1,
                "title": "Minimal",
                "release_date": "invalid",
                "overview": {},
                "genres": "wrong",
                "credits": ["bad"],
                "external_ids": {"imdb_id": ["bad"]},
                "vote_average": "8.0",
                "vote_count": True,
                "runtime": -1,
                "images": None,
            }
        )
    )
    details = client.get_movie("1")
    assert details.title == "Minimal" and details.year is None and details.overview is None
    assert details.genres == () and details.artwork == () and details.credits == ()
    assert details.runtime_seconds is None and details.vote_average is None and details.vote_count is None
    assert details.external_ids == {"tmdb": "1"}


def test_malformed_search_results_and_empty_query() -> None:
    client, transport = provider(reply({"results": None}))
    assert client.search_movie(" ") == () and transport.calls == []
    with pytest.raises(ProviderError, match="malformed_response"):
        client.search_movie("Film")


@pytest.mark.parametrize("identifier", ["../1", "https://evil.invalid", "1?x=2", "0", "9999999999999"])
def test_identifier_validation_prevents_url_injection(identifier: str) -> None:
    client, transport = provider()
    with pytest.raises(ProviderError, match="invalid_identifier"):
        client.get_movie(identifier)
    assert transport.calls == []


def test_invalid_coordinates_and_unconfigured_credentials_are_safe() -> None:
    client, transport = provider()
    with pytest.raises(ProviderError, match="invalid_coordinate"):
        client.get_episode("1", -1, 1)
    with pytest.raises(ProviderError, match="unconfigured"):
        TMDBMetadataProvider("")
    with pytest.raises(ProviderError, match="unconfigured"):
        TMDBMetadataProvider("token\r\nAuthorization: injected")
    assert transport.calls == []


def test_transport_reuses_connections_closes_and_rejects_unknown_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[object] = []

    class FakeResponse:
        status = 200

        def getheaders(self) -> list[tuple[str, str]]:
            return [("Content-Type", "application/json")]

        def read(self, amount: int) -> bytes:
            assert amount > 0
            return b"{}"

    class FakeConnection:
        def __init__(self, host: str, timeout: float) -> None:
            self.closed = False
            created.append(self)

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET"

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    transport = HTTPSConnectionTransport()
    for _ in range(2):
        assert transport.request(API_HOST, "/3/movie/1", {}, 10, 1024).status == 200
    assert len(created) == 1
    with pytest.raises(ProviderError, match="invalid_origin"):
        transport.request("evil.invalid", "/", {}, 10, 1024)
    transport.close()
    assert isinstance(created[0], FakeConnection) and created[0].closed
