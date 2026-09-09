"""One bounded Home snapshot from the local, viewer-authorized catalog."""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, noload

from app.home_schemas import HomeResponse
from app.models import Episode, Library, MediaItem, Season, Series, UserLibrary, WatchProgress
from app.services.catalog import cards, file_available, permitted_library, progress_join, safe_text

HOME_RAIL_LIMIT = 20
LEGACY_RAIL_LIMIT = 8


def home_catalog(db: Session, user_id: str) -> dict[str, Any]:
    base = (
        select(MediaItem)
        .where(permitted_library(user_id), MediaItem.available.is_(True), file_available())
        .options(noload(MediaItem.files))
    )

    def rail(kind: str | None = None, history: str | None = None, alphabetical: bool = False) -> list[MediaItem]:
        query = base
        if kind:
            query = query.where(MediaItem.kind == kind)
        if kind in {"episode", "series"}:
            query = query.where(MediaItem.library_id.in_(select(Library.id).where(Library.library_type == "tv")))
        if history:
            query = query.join(WatchProgress, progress_join(user_id))
            if history == "continue":
                query = query.where(
                    WatchProgress.watched.is_(False),
                    WatchProgress.completed_at.is_(None),
                    WatchProgress.position_seconds >= 5,
                    or_(
                        WatchProgress.duration_seconds <= 0,
                        WatchProgress.position_seconds < WatchProgress.duration_seconds,
                    ),
                )
            else:
                query = query.where(WatchProgress.watched.is_(True))
        order = (
            WatchProgress.last_played_at.desc()
            if history
            else (MediaItem.sort_title if alphabetical else MediaItem.created_at.desc())
        )
        limit = LEGACY_RAIL_LIMIT if alphabetical or history == "recent" else HOME_RAIL_LIMIT
        return list(db.scalars(query.order_by(order, MediaItem.id).limit(limit)))

    rails = {
        "continue": rail(history="continue"),
        "recent_movies": rail("movie"),
        "recent_episodes": rail("episode"),
        "movies": rail("movie", alphabetical=True),
        "shows": rail("series", alphabetical=True),
        "recent_watched": rail(history="recent"),
    }
    unique = {item.id: item for items in rails.values() for item in items}
    # Files, video metadata, progress and artwork are fetched once for the
    # bounded union, never once per card or repeatedly for overlapping rails.
    card_map = {card["id"]: card for card in cards(db, user_id, list(unique.values()))} if unique else {}
    shows = (
        dict(
            db.execute(
                select(Episode.media_item_id, MediaItem.title)
                .join(Season)
                .join(Series)
                .join(MediaItem, MediaItem.id == Series.media_item_id)
                .where(Episode.media_item_id.in_(unique))
            ).all()
        )
        if unique
        else {}
    )
    for media_id, card in card_map.items():
        card["title"] = safe_text(card["title"]) or "Untitled"
        card["show_title"] = safe_text(shows.get(media_id))
        card["added_at"] = unique[media_id].created_at
    libraries = [
        {
            "id": row.id,
            "name": safe_text(row.name) or "Unnamed library",
            "library_type": row.library_type,
            "enabled": True,
        }
        for row in db.execute(
            select(Library.id, Library.name, Library.library_type)
            .join(UserLibrary)
            .where(UserLibrary.user_id == user_id, Library.enabled.is_(True))
            .order_by(Library.name, Library.id)
        )
    ]
    result = {key: [card_map[item.id] for item in items] for key, items in rails.items()}
    return HomeResponse.model_validate({**result, "libraries": libraries}).model_dump(by_alias=True, mode="json")
