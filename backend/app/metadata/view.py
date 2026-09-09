"""Read-only normalized viewer projection. This module performs no provider requests."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, noload

from app.models import Episode, LibraryPath, LocalArtwork, MediaItem, MetadataRecord, Season, Series
from app.services.catalog import cards, file_available, permitted_library


def metadata_for(db: Session, media_id: str | None) -> MetadataRecord | None:
    return db.scalar(select(MetadataRecord).where(MetadataRecord.media_item_id == media_id)) if media_id else None


def artwork_url(db: Session, media_id: str, kind: str) -> str | None:
    value = db.scalar(
        select(LocalArtwork.id)
        .join(LibraryPath)
        .where(
            LocalArtwork.media_item_id == media_id,
            LocalArtwork.artwork_type == kind,
            LibraryPath.enabled.is_(True),
        )
        .order_by(LocalArtwork.provider.is_not(None), LocalArtwork.id)
        .limit(1)
    )
    return f"/api/v1/browse/artwork/{value}" if value else None


def detail_metadata(db: Session, user_id: str, item: MediaItem) -> dict[str, Any]:
    row = metadata_for(db, item.id)
    show_id = (
        db.scalar(select(Series.media_item_id).join(Season).join(Episode).where(Episode.media_item_id == item.id))
        if item.kind == "episode"
        else None
    )
    show = metadata_for(db, show_id)
    context = show or row
    fields = ("original_title", "release_date", "runtime_seconds", "overview", "tagline", "original_language")
    result: dict[str, Any] = {field: getattr(row, field) if row else None for field in fields}
    result.update(
        {
            "metadata_status": row.status if row else "unmatched",
            "metadata_provider": row.provider if row else None,
            "external_ids": row.external_ids if row else {},
            "content_rating": (row.content_rating if row else None) or (context.content_rating if context else None),
            "rating": {"provider": row.provider, "value": row.vote_average, "vote_count": row.vote_count}
            if row and row.vote_average is not None and row.vote_count
            else None,
            "metadata_updated_at": row.metadata_updated_at if row else None,
            "series_status": context.series_status if context else None,
            "number_of_seasons": context.number_of_seasons if context else None,
            "number_of_episodes": context.number_of_episodes if context else None,
            "last_air_date": context.last_air_date if context else None,
        }
    )
    for field in ("genres", "studios", "networks", "creators", "countries"):
        result[field] = (getattr(row, field) if row else []) or (getattr(context, field) if context else [])
    credits = list(row.credits if row else [])
    if show:
        # Show cast is useful episode context; show-level directors/writers are
        # not necessarily credited on this specific episode.
        credits.extend(credit for credit in show.credits if not credit.get("job"))
    cast: list[dict[str, Any]] = []
    crew: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for credit in credits:
        key = (credit.get("name", ""), credit.get("job") or credit.get("character") or "")
        if key in seen:
            continue
        seen.add(key)
        target = crew if credit.get("job") else cast
        if len(target) < (20 if target is crew else 10):
            target.append(credit)
    result["credits"] = cast + crew
    related_context = context
    related_ids = related_context.related_provider_ids if related_context else []
    related = (
        list(
            db.scalars(
                select(MediaItem)
                .join(MetadataRecord, MetadataRecord.media_item_id == MediaItem.id)
                .where(
                    MetadataRecord.provider == (related_context.provider if related_context else None),
                    MetadataRecord.kind == (related_context.kind if related_context else "movie"),
                    MetadataRecord.provider_id.in_(related_ids),
                    MediaItem.id != item.id,
                    permitted_library(user_id),
                    MediaItem.available.is_(True),
                    file_available(),
                )
                .options(noload(MediaItem.files))
                .order_by(MediaItem.sort_title, MediaItem.id)
                .limit(12)
            )
        )
        if related_ids
        else []
    )
    result["related"] = cards(db, user_id, related) if related else []
    if show_id:
        result["background_url"] = artwork_url(db, item.id, "background") or artwork_url(db, show_id, "background")
        result["poster_url"] = artwork_url(db, item.id, "poster") or artwork_url(db, show_id, "poster")
        local_show = db.get(MediaItem, show_id)
        result["show_title"] = show.title if show and show.title else local_show.title if local_show else None
    return result


def season_metadata(db: Session, season_id: str, show_id: str, number: int) -> dict[str, Any]:
    row = db.scalar(select(MetadataRecord).where(MetadataRecord.season_id == season_id))
    return {
        "show_id": show_id,
        "name": row.title if row else None,
        "overview": row.overview if row else None,
        "air_date": row.release_date if row else None,
        "provider_episode_count": row.number_of_episodes if row else None,
        "poster_url": artwork_url(db, show_id, f"season_poster_{number}"),
        "metadata_status": row.status if row else "unmatched",
    }
