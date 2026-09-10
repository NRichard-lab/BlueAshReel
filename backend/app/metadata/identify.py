"""Owner-directed identification using the existing provider and local cache."""
from __future__ import annotations

import base64
import re
from dataclasses import asdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.metadata.provider import ProviderError
from app.metadata.service import Enricher, configured_provider, local_query, record_for
from app.models import Episode, LocalArtwork, MediaItem, MetadataRecord, Season, Series, utcnow


def defaults(db: Session, item: MediaItem) -> dict:
    if item.kind not in {"movie", "series"}:
        raise ValueError("Only movies and series can be identified")
    title, year, _ = local_query(db, item)
    return {"title": title, "year": year, "kind": item.kind}


def search(db: Session, item: MediaItem, config: AppConfig, title: object, year: object) -> dict:
    defaults(db, item)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
        raise ValueError("Invalid search title")
    if year is not None and (type(year) is not int or not 1800 <= year <= 2200):
        raise ValueError("Invalid search year")
    provider = configured_provider(config)
    if provider is None:
        raise ProviderError("provider_not_configured")
    try:
        candidates = (provider.search_movie(title.strip(), year) if item.kind == "movie"
                      else provider.search_series(title.strip(), year))
        return {"items": [asdict(candidate) for candidate in candidates[:20]]}
    finally:
        provider.close()


def identify(db: Session, item: MediaItem, config: AppConfig, provider_id: object) -> None:
    defaults(db, item)
    if not isinstance(provider_id, str) or not re.fullmatch(r"[1-9][0-9]{0,11}", provider_id):
        raise ValueError("Invalid TMDB identifier")
    provider = configured_provider(config)
    if provider is None:
        raise ProviderError("provider_not_configured")
    try:
        details = provider.get_movie(provider_id) if item.kind == "movie" else provider.get_series(provider_id)
        if (details.provider != "tmdb" or details.provider_id != provider_id
                or details.kind != item.kind or not details.title):
            raise ProviderError("malformed_response")
        row = record_for(db, item)
        previous_id = row.provider_id
        # _persist writes metadata and artwork references in this transaction.
        # Failed downloads roll back every change; old cache files stay readable.
        Enricher(db, config, provider)._persist(item, row, details)
        if item.kind == "series" and previous_id != provider_id:
            # Child provider IDs belong to the previous show. Keep the local TV
            # hierarchy and files, but let later enrichment derive new child IDs.
            seasons = select(Season.id).join(Series).where(Series.media_item_id == item.id)
            episodes = select(Episode.media_item_id).where(Episode.season_id.in_(seasons))
            db.execute(delete(MetadataRecord).where(
                MetadataRecord.season_id.in_(seasons) | MetadataRecord.media_item_id.in_(episodes)))
            db.execute(delete(LocalArtwork).where(
                LocalArtwork.provider.is_not(None),
                LocalArtwork.media_item_id.in_(episodes) |
                ((LocalArtwork.media_item_id == item.id) & LocalArtwork.artwork_type.like("season_poster_%"))))
        row.manually_confirmed = True
        row.auto_match_enabled = True
        row.match_method, row.match_confidence = "manual", 1.0
        row.matched_at = row.attempted_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        provider.close()


def preview(db: Session, item: MediaItem, config: AppConfig, provider_id: object) -> dict:
    defaults(db, item)
    if not isinstance(provider_id, str) or not re.fullmatch(r"[1-9][0-9]{0,11}", provider_id):
        raise ValueError("Invalid TMDB identifier")
    provider = configured_provider(config)
    if provider is None:
        raise ProviderError("provider_not_configured")
    try:
        details = provider.get_movie(provider_id) if item.kind == "movie" else provider.get_series(provider_id)
        source = next((art for art in details.artwork if art.kind == "poster"), None)
        if source is None:
            return {}
        data, mime = provider.download_image(source)
        from app.metadata.artwork import _format

        _format(data, mime)
        # Preview bytes are transient, bounded and never entered into the catalog.
        if len(data) > 131072:
            return {}
        return {"data": base64.b64encode(data).decode("ascii"), "mime": mime}
    finally:
        provider.close()
