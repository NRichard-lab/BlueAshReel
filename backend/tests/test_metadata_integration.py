from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from collections import Counter
from dataclasses import replace
from datetime import UTC, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import func, select
from test_household_catalog import create_viewer, library, login, movie
from test_jobs_and_scanner import run_queued_scan
from test_remote_media import call, setup_remote

from app import native_runtime
from app.config import AppConfig
from app.metadata.artwork import cache_image, cached_file
from app.metadata.provider import ProviderError
from app.metadata.service import (
    Enricher,
    assign_match,
    clear_match,
    configured_provider,
    record_for,
    run_metadata_job,
)
from app.metadata.types import ArtworkSource, Candidate, Credit, MetadataDetails
from app.models import (
    BackgroundJob,
    BackgroundJobEvent,
    Episode,
    LibraryPath,
    LocalArtwork,
    MediaFile,
    MediaItem,
    MetadataRecord,
    Season,
    Series,
    utcnow,
)
from app.remote.protocol import decode
from app.services.jobs import claim_next_job


class FixtureProvider:
    """Synthetic normalized provider data; all requests are recorded locally."""

    name = "tmdb"

    def __init__(self, title: str = "Local", year: int | None = 2001, runtime: int = 120):
        self.calls: list[tuple] = []
        self.fail: ProviderError | None = None
        self.movie = self.details("movie", "101", title.upper(), year, runtime)
        self.show = self.details("series", "202", "Fixture Show", 2023, None)

    def details(self, kind, provider_id, title, year, runtime):
        profile = ArtworkSource("tmdb", "/private-provider-profile.jpg", "profile", "301")
        return MetadataDetails(
            provider="tmdb", provider_id=provider_id, kind=kind, title=title,
            original_title=title, year=year, release_date=f"{year or 2023}-04-05",
            runtime_seconds=runtime, overview="A descriptive overview from the fixture provider.",
            tagline="A provider tagline.", original_language="en", content_rating="PG-13",
            genres=("Drama", "Adventure"), studios=("Fixture Studio",), countries=("US",),
            networks=("Fixture Network",) if kind == "series" else (),
            creators=("Fixture Creator",) if kind == "series" else (),
            directors=("Fixture Director",), writers=("Fixture Writer",),
            vote_average=8.2, vote_count=345, status="Ended" if kind == "series" else None,
            number_of_seasons=1 if kind == "series" else None,
            number_of_episodes=2 if kind == "series" else None,
            external_ids={"tmdb": provider_id, "imdb": "tt0000001"},
            credits=(
                Credit("301", "Fixture Actor", "Acting", "Leading Character", order=0, profile=profile),
                Credit("302", "Fixture Director", "Directing", job="Director"),
                Credit("303", "Fixture Writer", "Writing", job="Writer"),
            ),
            artwork=(ArtworkSource("tmdb", f"/private-provider-{kind}-poster.jpg", "poster"),
                     ArtworkSource("tmdb", f"/private-provider-{kind}-backdrop.jpg", "backdrop")),
        )

    def search_movie(self, title, year=None):
        self.calls.append(("search_movie", title, year))
        if self.fail:
            raise self.fail
        return (Candidate(self.name, self.movie.provider_id, "movie", self.movie.title, year=self.movie.year),)

    def get_movie(self, provider_id):
        self.calls.append(("get_movie", provider_id))
        if self.fail:
            raise self.fail
        return replace(self.movie, provider_id=provider_id)

    def search_series(self, title, year=None):
        self.calls.append(("search_series", title, year))
        return (Candidate(self.name, self.show.provider_id, "series", self.show.title, year=2023),)

    def get_series(self, provider_id):
        self.calls.append(("get_series", provider_id))
        return self.show

    def get_season(self, series_id, season_number):
        self.calls.append(("get_season", series_id, season_number))
        return MetadataDetails(
            provider=self.name, provider_id="203", kind="season", title="A Named First Season",
            overview="The real provider season overview.", release_date="2023-04-05", number_of_episodes=2,
            series_provider_id=series_id, season_number=season_number,
            artwork=(ArtworkSource(self.name, "/private-provider-season.jpg", "poster"),),
        )

    def get_episode(self, series_id, season_number, episode_number):
        self.calls.append(("get_episode", series_id, season_number, episode_number))
        return MetadataDetails(
            provider=self.name, provider_id=str(204 + episode_number), kind="episode",
            title=f"Actual Episode Title {episode_number}", overview=f"Episode {episode_number} provider overview.",
            year=2023, release_date=f"2023-04-{episode_number + 5:02d}", runtime_seconds=2700,
            vote_average=8.5, vote_count=100, series_provider_id=series_id,
            season_number=season_number, episode_number=episode_number,
            external_ids={"tmdb": str(204 + episode_number)},
            credits=(Credit("304", "Episode Director", "Directing", job="Director"),),
            artwork=(ArtworkSource(self.name, f"/private-provider-episode-{episode_number}.jpg", "still"),),
        )

    def download_image(self, source):
        self.calls.append(("download_image", source.provider_path))
        return b"\xff\xd8\xff" + hashlib.sha256(source.provider_path.encode()).digest(), "image/jpeg"

    def close(self):
        self.calls.append(("close",))


def enrich_movie(context, local_id, provider, *, force=False):
    with context.session_factory() as db:
        item = db.get(MediaItem, local_id)
        assert item is not None
        return Enricher(db, context.config, provider).enrich(item, force=force)


def make_hierarchy(context, csrf):
    lib = library(context, csrf, "TV", "tv")
    with context.session_factory() as db:
        show = MediaItem(library_id=lib["id"], kind="series", title="Fixture Show", sort_title="fixture show")
        db.add(show)
        db.flush()
        series = Series(media_item_id=show.id)
        db.add(series)
        db.flush()
        season = Season(series_id=series.id, season_number=1)
        db.add(season)
        db.flush()
        episode_ids = []
        for number in (1, 2):
            episode = MediaItem(library_id=lib["id"], kind="episode", title=f"Episode {number}",
                                sort_title=f"episode {number}")
            db.add(episode)
            db.flush()
            db.add(Episode(media_item_id=episode.id, season_id=season.id, episode_number=number))
            db.add(MediaFile(
                media_item_id=episode.id, library_path_id=lib["paths"][0]["id"],
                relative_path=f"Fixture.Show.S01E{number:02d}.mkv", size_bytes=30, modified_ns=1,
                fingerprint="a" * 64, available=True, container="matroska", duration_seconds=2701,
            ))
            episode_ids.append(episode.id)
        db.commit()
        return lib, show.id, season.id, episode_ids


def test_movie_metadata_persists_locally_and_detail_reads_never_call_provider(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    local_id = movie(context, lib, "Local")
    provider = FixtureProvider(year=2026)
    record = enrich_movie(context, local_id, provider)
    assert record.status == "complete" and record.provider_id == "101"
    assert record.media_item_id == local_id and record.id != "101"
    assert record.match_confidence >= 0.9 and record.match_method.startswith("automatic_")
    assert record.metadata_updated_at and record.provider_data_updated_at and record.matched_at
    with context.session_factory() as db:
        assert db.get(MediaItem, local_id).title == "Local"
        assert db.scalar(select(MetadataRecord).where(MetadataRecord.media_item_id == local_id)).overview
        artwork = list(db.scalars(select(LocalArtwork).where(LocalArtwork.media_item_id == local_id)))
        assert len(artwork) == 3
        for art in artwork:
            assert art.provider == "tmdb" and art.library_path_id == lib["paths"][0]["id"]
            assert cached_file(context.config, art.cached_path).is_relative_to(context.config.artwork_dir)
            cached = cached_file(context.config, art.cached_path)
            assert hashlib.sha256(cached.read_bytes()).hexdigest() == art.fingerprint

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Viewer reads must use only local metadata")

    monkeypatch.setattr("app.metadata.service.configured_provider", forbidden)
    before = list(provider.calls)
    for _ in range(2):
        response = context.client.get(f"/api/v1/browse/media/{local_id}")
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail["title"] == "LOCAL" and detail["overview"] == provider.movie.overview
        assert detail["genres"] == ["Drama", "Adventure"] and detail["studios"] == ["Fixture Studio"]
        assert detail["release_date"] == "2026-04-05" and detail["content_rating"] == "PG-13"
        assert detail["rating"] == {"provider": "tmdb", "value": 8.2, "vote_count": 345}
        assert detail["external_ids"] == {"tmdb": "101", "imdb": "tt0000001"}
        assert detail["files"][0]["duration_seconds"] == 120
        assert detail["files"][0]["video"][0]["codec"] == "h264"
        assert detail["poster_url"] and detail["background_url"]
        for forbidden_text in (str(context.media_root), str(context.config.artwork_dir), "private-provider", "SAMPLE"):
            assert forbidden_text not in response.text
    assert provider.calls == before
    assert context.client.get("/api/v1/browse/home").json()["recent_movies"][0]["title"] == "LOCAL"


def test_episode_hierarchy_reuses_series_and_season_and_reads_normalized_season(owner_context):
    context, csrf = owner_context
    _, show_id, season_id, episode_ids = make_hierarchy(context, csrf)
    provider = FixtureProvider()
    with context.session_factory() as db:
        enricher = Enricher(db, context.config, provider)
        for episode_id in episode_ids:
            assert enricher.enrich(db.get(MediaItem, episode_id)).status == "complete"
        assert db.scalar(select(func.count()).select_from(MetadataRecord)) == 4
    counts = Counter(call[0] for call in provider.calls)
    assert counts["search_series"] == counts["get_series"] == counts["get_season"] == 1
    assert counts["get_episode"] == 2 and counts["search_movie"] == 0
    assert ("get_episode", "202", 1, 2) in provider.calls
    detail = context.client.get(f"/api/v1/browse/media/{episode_ids[0]}").json()
    assert detail["title"] == "Actual Episode Title 1"
    assert detail["show_id"] == show_id and detail["show_title"] == "Fixture Show"
    assert (detail["season_number"], detail["episode_number"]) == (1, 1)
    assert detail["release_date"] == "2023-04-06" and detail["genres"] == ["Drama", "Adventure"]
    assert detail["files"][0]["duration_seconds"] == 2701 and detail["runtime_seconds"] == 2700
    assert {credit["name"] for credit in detail["credits"]} >= {"Episode Director", "Fixture Actor"}
    assert detail["poster_url"] and detail["background_url"]
    seasons = context.client.get(f"/api/v1/browse/shows/{show_id}/seasons").json()["items"]
    assert seasons[0]["id"] == season_id and seasons[0]["name"] == "A Named First Season"
    assert seasons[0]["overview"] == "The real provider season overview." and seasons[0]["poster_url"]
    before = list(provider.calls)
    with context.session_factory() as db:
        enricher = Enricher(db, context.config, provider)
        for episode_id in episode_ids:
            assert enricher.enrich(db.get(MediaItem, episode_id)).status == "complete"
    assert provider.calls == before


def test_scanning_enqueues_enrichment_and_provider_failure_keeps_files_indexed(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    source = context.media_root / "Movies" / "Archive.Story.2001.1080p.mkv"
    source.write_bytes(b"synthetic local media fixture")
    provider = FixtureProvider("Archive Story", runtime=60)
    provider.fail = ProviderError("authentication_failed", status_code=401)
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: provider)
    scanned = run_queued_scan(context, lib["id"], monkeypatch)
    assert scanned.status == "succeeded" and provider.calls == []
    with context.session_factory() as db:
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        job = db.scalar(select(BackgroundJob).where(BackgroundJob.job_type == "metadata_enrich"))
        assert job and item
        run_metadata_job(db, job, context.config)
        row = record_for(db, item)
        assert row.status == "error" and row.error_code == "authentication_failed"
        assert db.get(BackgroundJob, scanned.id).status == "succeeded"
        local_file = db.scalar(select(MediaFile).where(MediaFile.media_item_id == item.id))
        assert item.available and local_file.available and local_file.duration_seconds == 60
        assert db.get(LibraryPath, local_file.library_path_id).canonical_path == str(source.parent)
    assert provider.calls == [("search_movie", "Archive Story", 2001), ("close",)]
    assert str(source) not in repr(provider.calls) and ".mkv" not in repr(provider.calls)


def test_missing_token_uses_truthful_local_data_and_scanner_queue_failure_is_isolated(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    (context.media_root / "Movies" / "Local.2026.mkv").write_bytes(b"synthetic scan media")
    assert configured_provider(context.config) is None

    def enqueue_failure(*_args, **_kwargs):
        raise OSError("Synthetic queue failure")

    with monkeypatch.context() as isolated:
        isolated.setattr("app.metadata.service.enqueue_enrichment", enqueue_failure)
        assert run_queued_scan(context, lib["id"], isolated).status == "succeeded"
    with context.session_factory() as db:
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        row = Enricher(db, context.config, None).enrich(item)
        assert row.status == "unavailable" and row.error_code == "provider_not_configured"
        local_id = item.id
        assert db.scalar(select(func.count()).select_from(LocalArtwork)) == 0
    detail = context.client.get(f"/api/v1/browse/media/{local_id}").json()
    assert detail["title"] == "Local" and detail["metadata_status"] == "unavailable"
    assert detail["overview"] is None and detail["rating"] is None and detail["credits"] == []
    assert detail["related"] == [] and detail["files"][0]["available"] is True


@pytest.mark.parametrize(("cached", "force"), [(False, False), (True, False), (True, True)])
def test_unconfigured_provider_job_fails_only_when_enrichment_is_needed(owner_context, monkeypatch, cached, force):
    context, csrf = owner_context
    lib = library(context, csrf)
    local_id = movie(context, lib, "Local")
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: None)
    with context.session_factory() as db:
        item = db.get(MediaItem, local_id)
        row = record_for(db, item)
        if cached:
            row.status, row.title, row.metadata_updated_at = "complete", "Cached title", utcnow()
        job = BackgroundJob(job_type="metadata_enrich", status="running", attempts=1,
                            locked_by="test-worker", lease_expires_at=utcnow() + timedelta(minutes=15),
                            payload={"library_id": lib["id"], "force": force})
        db.add(job)
        db.commit()
        run_metadata_job(db, job, context.config)
        assert job.status == ("succeeded" if cached and not force else "failed")
        assert job.completed_at and job.locked_by is None and job.lease_expires_at is None
        assert job.progress_current == 1 and item.available
        if cached:
            assert row.status == "complete" and row.title == "Cached title"
        else:
            assert row.status == "unavailable" and row.error_code == "provider_not_configured"
        if not cached or force:
            assert "not configured" in job.error_summary
            assert db.scalar(select(BackgroundJobEvent.event_type).where(
                BackgroundJobEvent.job_id == job.id)) == "failed"
        assert claim_next_job(db, context.config, "another-worker") is None


@pytest.mark.parametrize("contents", [None, "", " \r\n", "too-short", "Bearer invalid-token-0123456789"])
def test_missing_or_malformed_token_file_disables_provider(context, tmp_path, contents):
    credential = tmp_path / "tmdb-token.txt"
    if contents is not None:
        credential.write_text(contents, encoding="utf-8")
    config = context.config.model_copy(update={"tmdb_access_token": None, "tmdb_token_file": credential})
    assert config.tmdb_token == ""
    assert configured_provider(config) is None


def test_token_file_with_bom_and_trailing_newline_is_loaded_privately(context, tmp_path):
    credential = tmp_path / "tmdb-token.txt"
    token = "synthetic-test-token-0123456789"  # noqa: S105 - deliberately fake credential
    credential.write_text(token + "\r\n", encoding="utf-8-sig")
    config = context.config.model_copy(update={"tmdb_access_token": None, "tmdb_token_file": credential})
    assert config.tmdb_token == token
    assert token not in repr(config) and token not in config.model_dump_json()
    provider = configured_provider(config)
    assert provider is not None
    provider.close()


def test_manual_match_and_clear_survive_routine_rescan(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    source = context.media_root / "Movies" / "Archive.Story.2001.mkv"
    source.write_bytes(b"synthetic scan media")
    run_queued_scan(context, lib["id"], monkeypatch)
    provider = FixtureProvider("Archive Story", runtime=60)
    with context.session_factory() as db:
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        local_id = item.id
        assign_match(db, item.id, "tmdb", "999")
        row = Enricher(db, context.config, provider).enrich(item)
        assert row.status == "complete" and row.provider_id == "999"
        assert row.manually_confirmed and row.match_method == "manual"
    assert not any(c[0] == "search_movie" for c in provider.calls)
    run_queued_scan(context, lib["id"], monkeypatch, mode="full")
    before = list(provider.calls)
    assert enrich_movie(context, local_id, provider).provider_id == "999"
    assert provider.calls == before
    with context.session_factory() as db:
        clear_match(db, local_id)
        assert db.scalar(select(func.count()).select_from(LocalArtwork)) == 0
        assert not record_for(db, db.get(MediaItem, local_id)).auto_match_enabled
    run_queued_scan(context, lib["id"], monkeypatch, mode="full")
    cleared = enrich_movie(context, local_id, provider)
    assert cleared.provider_id is None and cleared.status == "unmatched"
    assert provider.calls == before
    assert context.client.get(f"/api/v1/browse/media/{local_id}").json()["title"] == "Archive Story"


def test_related_titles_are_playable_local_matches_scoped_to_library_permissions(owner_context):
    context, csrf = owner_context
    lib = library(context, csrf)
    hidden_lib = library(context, csrf, "Private")
    source = movie(context, lib, "Local")
    related = movie(context, lib, "Owned Related")
    unavailable = movie(context, lib, "Unavailable Related", available=False)
    hidden = movie(context, hidden_lib, "Private Related")
    provider = FixtureProvider(year=2026)
    provider.movie = replace(provider.movie, related_provider_ids=("501", "502", "503", "504"))
    assert enrich_movie(context, source, provider).status == "complete"
    with context.session_factory() as db:
        for local_id, provider_id in ((related, "501"), (unavailable, "502"), (hidden, "503")):
            row = record_for(db, db.get(MediaItem, local_id))
            row.provider, row.provider_id, row.status = "tmdb", provider_id, "complete"
        db.commit()
    create_viewer(context, csrf, [lib["id"]])
    login(context, "viewer")
    response = context.client.get(f"/api/v1/browse/media/{source}")
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["related"]] == [related]
    assert "Private Related" not in response.text and "504" not in response.text


def test_provider_artwork_opaque_relay_profile_season_and_authorization(owner_context):
    context, csrf = owner_context
    media, auth, (_, local_id, _, _) = setup_remote(owner_context)
    provider = FixtureProvider(year=None)
    assert enrich_movie(context, local_id, provider).status == "complete"
    _, show_id, _, episode_ids = make_hierarchy(context, csrf)
    with context.session_factory() as db:
        enricher = Enricher(db, context.config, provider)
        for episode_id in episode_ids:
            assert enricher.enrich(db.get(MediaItem, episode_id)).status == "complete"
        alias = media.alias(db, "media", local_id)
        show_alias = media.alias(db, "media", show_id)
        db.commit()
    detail = call(media, auth, "catalog.detail", media_id=alias)
    assert detail["id"] == alias != local_id and detail["title"] == "LOCAL"
    profile_id = next(c["profile_artwork_id"] for c in detail["credits"] if c["name"] == "Fixture Actor")
    seasons = call(media, auth, "catalog.seasons", media_id=show_alias)["items"]
    assert seasons[0]["name"] == "A Named First Season" and seasons[0]["show_id"] == show_alias
    for artwork_id in (detail["artwork_id"], detail["background_id"], profile_id, seasons[0]["artwork_id"]):
        assert decode(call(media, auth, "artwork.bytes", artwork_id=artwork_id)["data"]).startswith(b"\xff\xd8\xff")
        for invalid in ({**auth, "authorized": False}, {**auth, "expires_at": 0},
                        {**auth, "agent_id": str(uuid.uuid4())}):
            with pytest.raises(HTTPException):
                call(media, invalid, "artwork.bytes", artwork_id=artwork_id)
    serialized = json.dumps({"detail": detail, "seasons": seasons}, default=str)
    for forbidden in (str(context.media_root), str(context.config.artwork_dir), "private-provider", local_id,
                      "poster_url", "profile_url", "api.themoviedb.org", "image.tmdb.org"):
        assert forbidden not in serialized
    viewer = {**auth, "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "role": "viewer"}
    call(media, auth, "grants.set", user_id=viewer["user_id"], library_ids=[])
    with pytest.raises(HTTPException):
        call(media, viewer, "artwork.bytes", artwork_id=profile_id)
    with media.factory() as db:
        art = db.get(LocalArtwork, media.resolve(db, "artwork", profile_id))
        art.cached_path = "../../private-token.txt"
        db.commit()
    with pytest.raises(HTTPException):
        call(media, auth, "artwork.bytes", artwork_id=profile_id)


def test_artwork_cache_deduplicates_repairs_corruption_and_rejects_invalid_images(owner_context):
    context, csrf = owner_context
    lib = library(context, csrf)
    first, second = movie(context, lib, "First"), movie(context, lib, "Second")
    provider = FixtureProvider()
    source = ArtworkSource("tmdb", "/shared-poster.jpg", "poster")
    with context.session_factory() as db:
        first_art = cache_image(db, context.config, provider, db.get(MediaItem, first), source)
        db.commit()
        second_art = cache_image(db, context.config, provider, db.get(MediaItem, second), source)
        db.commit()
        assert first_art.id != second_art.id and first_art.cached_path == second_art.cached_path
        assert len(provider.calls) == 1
        original_path = cached_file(context.config, first_art.cached_path)
        original = original_path.read_bytes()
        original_path.write_bytes(b"corrupted artwork bytes")
        repaired = cache_image(db, context.config, provider, db.get(MediaItem, first), source)
        db.commit()
        assert repaired.id == first_art.id and original_path.read_bytes() == original
        assert len(provider.calls) == 2
        provider.download_image = lambda _source: (b"<html>upstream error</html>", "image/jpeg")
        with pytest.raises(ProviderError, match="invalid_image"):
            cache_image(db, context.config, provider, db.get(MediaItem, first),
                        ArtworkSource("tmdb", "/invalid-image.jpg", "poster"))
        assert db.scalar(select(func.count()).select_from(LocalArtwork)) == 2
    for path in ("../secret.jpg", str(Path(context.config.artwork_dir) / "outside.jpg"), "https://image.tmdb.org/x.jpg"):
        with pytest.raises(HTTPException):
            cached_file(context.config, path)


@pytest.mark.parametrize("code,status", [
    ("rate_limited", 429), ("temporarily_unavailable", 500), ("temporarily_unavailable", 503),
    ("timeout", None), ("network_error", None),
])
def test_provider_outage_stops_library_requests_and_schedules_bounded_retry(
    owner_context, monkeypatch, code, status,
):
    context, csrf = owner_context
    lib = library(context, csrf)
    local_ids = [movie(context, lib, f"Local {number}") for number in range(4)]
    provider = FixtureProvider(year=2026)
    provider.fail = ProviderError(code, status_code=status, retry_after_seconds=30)
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: provider)
    now = utcnow()
    monkeypatch.setattr("app.metadata.service.utcnow", lambda: now)
    with context.session_factory() as db:
        job = BackgroundJob(job_type="metadata_enrich", status="running", attempts=1,
                            payload={"library_id": lib["id"], "force": False})
        db.add(job)
        db.commit()
        run_metadata_job(db, job, context.config)
        assert job.status == "retry_wait" and job.available_at.replace(tzinfo=UTC) == now + timedelta(minutes=15)
        assert job.locked_by is None and job.lease_expires_at is None
        assert job.completed_at is None
        assert claim_next_job(db, context.config, "not-due-worker") is None
        errors = list(db.scalars(select(MetadataRecord).where(MetadataRecord.status == "error")))
        assert len(errors) == 1 and errors[0].error_code == code
        assert db.scalar(select(func.count()).select_from(MediaFile).where(MediaFile.available.is_(True))) == 4
        assert set(db.scalars(select(MediaItem.id).where(MediaItem.available.is_(True)))) == set(local_ids)
    assert sum(call[0] == "search_movie" for call in provider.calls) == 1
    assert provider.calls[-1] == ("close",)
    assert not any(call[0] in {"get_movie", "download_image"} for call in provider.calls)


def test_provider_retry_after_survives_the_worker_job_boundary(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    movie(context, lib)
    provider = FixtureProvider(year=2026)
    provider.fail = ProviderError("rate_limited", status_code=429, retry_after_seconds=7200)
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: provider)
    now = utcnow()
    monkeypatch.setattr("app.metadata.service.utcnow", lambda: now)
    with context.session_factory() as db:
        job = BackgroundJob(job_type="metadata_enrich", status="running", attempts=1,
                            payload={"library_id": lib["id"]})
        db.add(job)
        db.commit()
        run_metadata_job(db, job, context.config)
        assert job.status == "retry_wait"
        assert job.available_at.replace(tzinfo=UTC) == now + timedelta(hours=2)


def test_scheduled_retry_retries_recent_error_without_refetching_successful_records(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    failed_id = movie(context, lib, "Local")
    complete_id = movie(context, lib, "Complete")
    provider = FixtureProvider(year=2026)
    provider.fail = ProviderError("temporarily_unavailable", status_code=503)
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: provider)
    before = utcnow()
    with context.session_factory() as db:
        complete = record_for(db, db.get(MediaItem, complete_id))
        complete.provider, complete.provider_id = "tmdb", "777"
        complete.status, complete.title, complete.overview = "complete", "Previously Complete", "Preserved overview."
        complete.metadata_updated_at = before
        job = BackgroundJob(job_type="metadata_enrich", status="running", attempts=1,
                            payload={"library_id": lib["id"], "force": False})
        db.add(job)
        db.commit()
        run_metadata_job(db, job, context.config)
        failed = record_for(db, db.get(MediaItem, failed_id))
        assert failed.status == "error" and job.status == "retry_wait"
        assert failed.attempted_at.replace(tzinfo=UTC) >= before
        provider.fail = None
        provider.calls.clear()
        job.status, job.attempts = "running", 2
        db.commit()
        run_metadata_job(db, job, context.config)
        assert job.status == "succeeded" and failed.status == "complete"
        assert complete.title == "Previously Complete" and complete.overview == "Preserved overview."
        assert complete.metadata_updated_at.replace(tzinfo=UTC) == before
    assert ("search_movie", "Local", 2026) in provider.calls
    assert not any(call[0] == "get_movie" and call[1] == "777" for call in provider.calls)


def test_automatic_retry_does_not_repeat_original_force_refresh_or_retry_forever(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    failed_id, complete_id = movie(context, lib, "Local"), movie(context, lib, "Complete")
    provider = FixtureProvider(year=2026)
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: provider)
    with context.session_factory() as db:
        complete = record_for(db, db.get(MediaItem, complete_id))
        complete.status, complete.provider, complete.provider_id = "complete", "tmdb", "777"
        complete.title, complete.metadata_updated_at = "Completed By First Attempt", utcnow()
        failed = record_for(db, db.get(MediaItem, failed_id))
        failed.status, failed.attempted_at = "error", utcnow()
        job = BackgroundJob(job_type="metadata_enrich", status="running", attempts=2, max_attempts=3,
                            payload={"library_id": lib["id"], "force": True})
        db.add(job)
        db.commit()
        run_metadata_job(db, job, context.config)
        assert job.status == "succeeded" and failed.status == "complete"
        assert complete.title == "Completed By First Attempt"
        assert not any(call[0] == "get_movie" and call[1] == "777" for call in provider.calls)
        provider.fail = ProviderError("rate_limited", status_code=429)
        failed.status, failed.attempted_at = "error", utcnow()
        job.status, job.attempts, job.completed_at = "running", 3, None
        provider.calls.clear()
        db.commit()
        run_metadata_job(db, job, context.config)
        assert job.status == "failed" and job.completed_at is not None
        assert failed.status == "error" and failed.error_code == "rate_limited"
        assert complete.status == "complete" and complete.title == "Completed By First Attempt"
    assert provider.calls == [("get_movie", "101"), ("close",)]


def test_retry_only_job_preserves_complete_reviewable_and_manually_cleared_records(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    failed_id = movie(context, lib, "Local")
    complete_id = movie(context, lib, "Complete")
    cleared_id = movie(context, lib, "Cleared")
    review_id = movie(context, lib, "Reviewable")
    provider = FixtureProvider(year=2026)
    monkeypatch.setattr("app.metadata.service.configured_provider", lambda _config: provider)
    old = utcnow() - timedelta(days=90)
    with context.session_factory() as db:
        complete = record_for(db, db.get(MediaItem, complete_id))
        complete.provider, complete.provider_id, complete.status = "tmdb", "777", "complete"
        complete.title, complete.metadata_updated_at = "Previously Complete", old
        clear_match(db, cleared_id)
        review = record_for(db, db.get(MediaItem, review_id))
        review.status, review.attempted_at = "needs_review", old
        failed = record_for(db, db.get(MediaItem, failed_id))
        failed.status, failed.attempted_at = "error", utcnow()
        failed.error_code = "temporarily_unavailable"
        job = BackgroundJob(job_type="metadata_enrich", status="running", attempts=1,
                            payload={"library_id": lib["id"], "force": False, "retry_only": True})
        db.add(job)
        db.commit()
        run_metadata_job(db, job, context.config)
        assert failed.status == "complete" and job.status == "succeeded"
        assert complete.status == "complete" and complete.title == "Previously Complete"
        assert complete.metadata_updated_at.replace(tzinfo=UTC) == old
        assert review.status == "needs_review" and review.attempted_at.replace(tzinfo=UTC) == old
        cleared = record_for(db, db.get(MediaItem, cleared_id))
        assert cleared.status == "unmatched" and not cleared.auto_match_enabled
    assert [call for call in provider.calls if call[0] == "search_movie"] == [("search_movie", "Local", 2026)]
    assert not any(call[0] == "get_movie" and call[1] == "777" for call in provider.calls)


@pytest.mark.parametrize("status", ["complete", "matched"])
def test_episode_never_uses_another_providers_series_identifier(owner_context, status):
    context, csrf = owner_context
    _, show_id, _, episode_ids = make_hierarchy(context, csrf)
    provider = FixtureProvider()
    with context.session_factory() as db:
        show = record_for(db, db.get(MediaItem, show_id))
        show.provider, show.provider_id, show.status = "future_provider", "foreign-series-202", status
        show.title, show.metadata_updated_at, show.manually_confirmed = "Previously Matched Show", utcnow(), True
        db.commit()
        result = Enricher(db, context.config, provider).enrich(db.get(MediaItem, episode_ids[0]))
        assert result.status in {"error", "unmatched"} and result.provider_id is None
        assert show.provider == "future_provider" and show.provider_id == "foreign-series-202"
    assert provider.calls == []


@pytest.mark.parametrize("credential_mode", ["direct", "file", "both", "none"])
def test_native_activation_exports_private_token_fields_without_serializing_them(
    context, tmp_path, monkeypatch, capsys, credential_mode,
):
    direct = "unit-test-direct-token-0123456789"
    from_file = "unit-test-file-token-9876543210"
    secret_file = tmp_path / "private-credential.txt"
    secret_file.write_text(from_file, encoding="utf-8")
    configuration = context.config.model_copy(update={
        "tmdb_access_token": SecretStr(direct) if credential_mode in {"direct", "both"} else None,
        "tmdb_token_file": secret_file if credential_mode in {"file", "both"} else None,
    })
    installation = native_runtime.Installation(
        program_dir=tmp_path / "program", data_dir=tmp_path,
        service_prefix="FixtureAgent", port=18080, api_port=18081, web_port=18082, bind_address="127.0.0.1",
    )
    environment = {"TMDB_ACCESS_TOKEN": "stale-inherited-value", "TMDB_TOKEN_FILE": "stale-inherited-file"}
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(os, "chdir", lambda _path: None)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    native_runtime.activate_configuration(installation, configuration)
    if credential_mode in {"direct", "both"}:
        assert environment["TMDB_ACCESS_TOKEN"] == direct
    else:
        assert "TMDB_ACCESS_TOKEN" not in environment
    if credential_mode in {"file", "both"}:
        assert environment["TMDB_TOKEN_FILE"] == str(secret_file)
    else:
        assert "TMDB_TOKEN_FILE" not in environment
    reloaded = AppConfig(_env_file=None)
    assert reloaded.tmdb_token == (direct if credential_mode in {"direct", "both"} else
                                   from_file if credential_mode == "file" else "")
    for dumped in (configuration.model_dump(), configuration.model_dump_json(), repr(configuration)):
        text = str(dumped)
        for private in ("tmdb_access_token", "tmdb_token_file", direct, from_file, str(secret_file)):
            assert private not in text
    captured = capsys.readouterr()
    assert direct not in captured.out + captured.err and from_file not in captured.out + captured.err
