from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Episode, MediaFile, MediaItem, Season, Series


def recompute_media_availability(db: Session, library_id: str) -> None:
    """Derive item and series availability from retained media-file history."""
    available_file_items = select(MediaFile.media_item_id).where(MediaFile.available.is_(True))
    db.execute(
        update(MediaItem)
        .where(
            MediaItem.library_id == library_id,
            MediaItem.kind != "series",
            MediaItem.id.not_in(available_file_items),
        )
        .values(available=False)
    )
    db.execute(
        update(MediaItem)
        .where(
            MediaItem.library_id == library_id,
            MediaItem.kind != "series",
            MediaItem.id.in_(available_file_items),
        )
        .values(available=True)
    )
    available_series = (
        select(Series.media_item_id)
        .join(Season, Season.series_id == Series.id)
        .join(Episode, Episode.season_id == Season.id)
        .join(MediaFile, MediaFile.media_item_id == Episode.media_item_id)
        .where(MediaFile.available.is_(True))
    )
    db.execute(
        update(MediaItem).where(MediaItem.library_id == library_id, MediaItem.kind == "series").values(available=False)
    )
    db.execute(
        update(MediaItem)
        .where(
            MediaItem.library_id == library_id,
            MediaItem.kind == "series",
            MediaItem.id.in_(available_series),
        )
        .values(available=True)
    )
