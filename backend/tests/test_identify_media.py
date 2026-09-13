from dataclasses import replace
from datetime import UTC, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_household_catalog import library, movie
from test_jobs_and_scanner import run_queued_scan
from test_metadata_integration import FixtureProvider, make_hierarchy
from test_remote_media import call, setup_remote

from app.metadata.identify import identify, preview, search
from app.metadata.provider import ProviderError
from app.metadata.service import Enricher, record_for
from app.models import LocalArtwork, MediaFile, MediaItem, MetadataRecord, WatchProgress, utcnow


@pytest.mark.parametrize("year", [None, 2018])
def test_encrypted_search_is_bounded_owner_only_and_uses_optional_year(owner_context, monkeypatch, year):
    remote, auth, _ = setup_remote(owner_context)
    card = call(remote, auth, "catalog.list")["items"][0]
    provider = FixtureProvider("12 Strong", 2018)
    monkeypatch.setattr("app.metadata.identify.configured_provider", lambda _: provider)
    result = call(remote, auth, "metadata.search", media_id=card["id"], title="12 Strong", year=year)
    assert result["items"][0]["provider_id"] == "101"
    assert provider.calls == [("search_movie", "12 Strong", year), ("close",)]
    with pytest.raises(HTTPException):
        call(remote, {**auth, "role": "member"}, "metadata.search", media_id=card["id"], title="12 Strong")
    with pytest.raises(ValueError):
        call(remote, auth, "metadata.search", media_id=card["id"], title="12 Strong", url="https://invalid")


@pytest.mark.parametrize("failure", [None, "details", "artwork", "malformed"])
def test_identification_atomicity_artwork_and_history(owner_context, monkeypatch, failure):
    context, _ = owner_context
    remote, auth, (_, local_id, file_id, _) = setup_remote(owner_context)
    card = call(remote, auth, "catalog.list")["items"][0]
    provider = FixtureProvider("Correct title", 2018)
    monkeypatch.setattr("app.metadata.identify.configured_provider", lambda _: provider)
    with context.session_factory() as db:
        item = db.get(MediaItem, local_id)
        row = record_for(db, item)
        row.provider, row.provider_id, row.title, row.status = "tmdb", "99", "Old title", "complete"
        user_id = remote.principal(db, auth).user.id
        progress = WatchProgress(user_id=user_id, media_item_id=local_id, media_file_id=file_id,
                                 position_seconds=45, watched_seconds=90, watched=True)
        db.add(progress)
        db.commit()
        db.refresh(progress)
        original = (db.get(MediaFile, file_id).fingerprint, item.library_id, progress.id, progress.last_played_at)
    if failure == "details":
        provider.fail = ProviderError("authentication_failed", 401)
    elif failure == "artwork":
        def failed(_):
            raise ProviderError("invalid_image")
        provider.download_image = failed
    elif failure == "malformed":
        provider.get_movie = lambda _: replace(provider.movie, provider_id="999")
    if failure:
        with pytest.raises(HTTPException):
            call(remote, auth, "metadata.identify", media_id=card["id"], provider_id="101")
    else:
        result = call(remote, auth, "metadata.identify", media_id=card["id"], provider_id="101")
        assert result["title"] == "CORRECT TITLE" and result["artwork_id"]
        assert result["id"] == card["id"] and result["watched"]
    with context.session_factory() as db:
        row = db.scalar(select(MetadataRecord).where(MetadataRecord.media_item_id == local_id))
        assert row.provider_id == ("99" if failure else "101")
        assert row.title == ("Old title" if failure else "CORRECT TITLE")
        if not failure:
            assert row.manually_confirmed and row.match_method == "manual" and row.metadata_updated_at
        progress = db.scalar(select(WatchProgress).where(WatchProgress.media_item_id == local_id))
        assert progress.watched and progress.position_seconds == 45 and progress.watched_seconds == 90
        assert (db.get(MediaFile, file_id).fingerprint, db.get(MediaItem, local_id).library_id,
                progress.id, progress.last_played_at) == original


def test_manual_identity_survives_full_scan_refresh_and_artwork_replacement(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    (context.media_root / "Movies" / "Wrong.Name.2001.mkv").write_bytes(b"synthetic scan file")
    run_queued_scan(context, lib["id"], monkeypatch)
    provider = FixtureProvider("Chosen title", 2018)
    monkeypatch.setattr("app.metadata.identify.configured_provider", lambda _: provider)
    with context.session_factory() as db:
        item = db.scalar(select(MediaItem).where(MediaItem.kind == "movie"))
        local_id = item.id
        identify(db, item, context.config, "101")
        old_art = set(db.scalars(select(LocalArtwork.id)))
        provider.movie = replace(provider.movie, artwork=(
            replace(provider.movie.artwork[0], provider_path="/new.jpg"),))
        identify(db, item, context.config, "102")
        assert not old_art.issubset(set(db.scalars(select(LocalArtwork.id))))
        row = record_for(db, item)
        row.metadata_updated_at = utcnow() - timedelta(days=90)
        db.commit()
    run_queued_scan(context, lib["id"], monkeypatch, mode="full")
    provider.calls.clear()
    with context.session_factory() as db:
        row = Enricher(db, context.config, provider).enrich(db.get(MediaItem, local_id))
        assert row.provider_id == "102" and row.manually_confirmed and row.match_method == "manual"
        assert row.metadata_updated_at.replace(tzinfo=UTC) > utcnow() - timedelta(minutes=1)
    assert ("get_movie", "102") in provider.calls
    assert not any(value[0] == "search_movie" for value in provider.calls)


def test_preview_is_transient_and_series_search_is_supported(owner_context, monkeypatch):
    context, csrf = owner_context
    lib = library(context, csrf)
    local_id = movie(context, lib, "Local")
    provider = FixtureProvider()
    monkeypatch.setattr("app.metadata.identify.configured_provider", lambda _: provider)
    with context.session_factory() as db:
        item = db.get(MediaItem, local_id)
        result = preview(db, item, context.config, "101")
        assert result["mime"] == "image/jpeg" and result["data"]
        assert not list(db.scalars(select(LocalArtwork)))
        item.kind = "series"
        result = search(db, item, context.config, "Fixture Show", None)
        assert result["items"][0]["kind"] == "series"
        identify(db, item, context.config, "202")
        assert record_for(db, item).provider_id == "202"


def test_series_reidentification_retires_old_child_metadata_without_removing_files(owner_context, monkeypatch):
    context, csrf = owner_context
    _, show_id, season_id, episode_ids = make_hierarchy(context, csrf)
    provider = FixtureProvider()
    monkeypatch.setattr("app.metadata.identify.configured_provider", lambda _: provider)
    with context.session_factory() as db:
        Enricher(db, context.config, provider).enrich(db.get(MediaItem, episode_ids[0]))
        provider.get_series = lambda identity: replace(provider.show, provider_id=identity)
        identify(db, db.get(MediaItem, show_id), context.config, "303")
        assert record_for(db, db.get(MediaItem, show_id)).provider_id == "303"
        assert not list(db.scalars(select(MetadataRecord).where(MetadataRecord.season_id == season_id)))
        assert not list(db.scalars(select(MetadataRecord).where(MetadataRecord.media_item_id.in_(episode_ids))))
        assert len(list(db.scalars(select(MediaFile)))) == 2
