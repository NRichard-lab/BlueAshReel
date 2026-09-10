"""Owner-only sparse presentation overrides and provider artwork selection."""

from __future__ import annotations

import base64
import hashlib
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.metadata.artwork import _format, cache_image
from app.metadata.provider import ProviderError
from app.metadata.service import configured_provider
from app.metadata.view import artwork_url, metadata_for
from app.models import MediaItem

EDITABLE = {
    "title",
    "original_title",
    "year",
    "release_date",
    "runtime_seconds",
    "overview",
    "tagline",
    "content_rating",
    "vote_average",
    "genres",
    "studios",
    "creators",
}
LIST_FIELDS = {"genres", "studios", "creators"}
LIMITS = {"title": 500, "original_title": 500, "overview": 20000, "tagline": 1000, "content_rating": 40}


def _row(db: Session, item: MediaItem):
    row = metadata_for(db, item.id)
    if item.kind != "movie" or row is None or not row.provider_id:
        raise ValueError("Editable provider metadata is unavailable for this item")
    return row


def read(db: Session, item: MediaItem) -> dict[str, Any]:
    row = _row(db, item)
    provider = {field: getattr(row, field) for field in EDITABLE}
    overrides = dict(row.field_overrides or {})
    effective = {field: overrides.get(field, provider[field]) for field in EDITABLE}
    directors = [c.get("name") for c in row.credits if c.get("job") == "Director" and c.get("name")]
    provider["director"] = directors
    effective["director"] = directors
    return {
        "kind": item.kind,
        "provider": row.provider,
        "provider_values": provider,
        "values": effective,
        "overridden": sorted(overrides),
        "artwork": {
            kind: {"url": artwork_url(db, item.id, kind), "manual": kind in (row.artwork_selections or {})}
            for kind in ("poster", "background")
        },
    }


def _validate(field: str, value: Any) -> Any:
    if field in LIST_FIELDS:
        if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) for v in value):
            raise ValueError(f"Invalid {field}")
        return list(dict.fromkeys(v.strip() for v in value if v.strip()))
    if field == "year":
        if value is not None and (type(value) is not int or not 1800 <= value <= 2200):
            raise ValueError("Year must be between 1800 and 2200")
    elif field == "runtime_seconds":
        if value is not None and (type(value) is not int or not 0 <= value <= 604800):
            raise ValueError("Runtime is invalid")
    elif field == "vote_average":
        if value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 10
        ):
            raise ValueError("Community rating must be between 0 and 10")
    elif field == "release_date" and value not in (None, ""):
        if not isinstance(value, str):
            raise ValueError("Release date is invalid")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Release date is invalid") from exc
    elif field in LIMITS and value is not None and (not isinstance(value, str) or len(value) > LIMITS[field]):
        raise ValueError(f"Invalid {field}")
    return value


def save(db: Session, item: MediaItem, values: object, restore: object) -> dict[str, Any]:
    row = _row(db, item)
    if not isinstance(values, dict) or not isinstance(restore, list) or any(f not in EDITABLE for f in values):
        raise ValueError("Invalid metadata changes")
    if any(not isinstance(f, str) or f not in EDITABLE for f in restore):
        raise ValueError("Invalid restore request")
    updated = dict(row.field_overrides or {})
    for field in restore:
        updated.pop(field, None)
    for field, value in values.items():
        updated[field] = _validate(field, value)
    row.field_overrides = updated
    db.commit()
    return read(db, item)


def _key(path: str) -> str:
    return hashlib.sha256(path.encode()).hexdigest()[:24]


def _sources(row, config: AppConfig, kind: str):
    if kind not in {"poster", "background"}:
        raise ValueError("Invalid artwork kind")
    provider = configured_provider(config)
    if provider is None:
        raise ProviderError("provider_not_configured")
    sources = provider.get_image_candidates(row.kind, row.provider_id)
    wanted = "poster" if kind == "poster" else "backdrop"
    return provider, [s for s in sources if s.kind == wanted]


def artwork_candidates(db: Session, item: MediaItem, config: AppConfig, kind: str) -> dict:
    row = _row(db, item)
    provider, sources = _sources(row, config, kind)
    try:
        return {
            "items": [
                {"id": _key(s.provider_path), "width": s.width, "height": s.height, "language": s.language}
                for s in sources[:60]
            ]
        }
    finally:
        provider.close()


def artwork_preview(db: Session, item: MediaItem, config: AppConfig, kind: str, candidate_id: str) -> dict:
    row = _row(db, item)
    provider, sources = _sources(row, config, kind)
    try:
        source = next((s for s in sources if _key(s.provider_path) == candidate_id), None)
        if source is None:
            raise ValueError("Artwork candidate expired")
        data, mime = provider.download_image(source)
        _format(data, mime)
        if len(data) > 1048576:
            return {}
        return {"data": base64.b64encode(data).decode(), "mime": mime}
    finally:
        provider.close()


def select_artwork(db: Session, item: MediaItem, config: AppConfig, kind: str, candidate_id: str) -> dict:
    row = _row(db, item)
    provider, sources = _sources(row, config, kind)
    try:
        source = next((s for s in sources if _key(s.provider_path) == candidate_id), None)
        if source is None:
            raise ValueError("Artwork candidate expired")
        art = cache_image(db, config, provider, item, source)
        selections = dict(row.artwork_selections or {})
        selections[kind] = {
            "source": "provider",
            "provider": source.provider,
            "provider_path": source.provider_path,
            "artwork_id": art.id,
        }
        row.artwork_selections = selections
        db.commit()
        return read(db, item)
    except Exception:
        db.rollback()
        raise
    finally:
        provider.close()


def restore_artwork(db: Session, item: MediaItem, kind: str) -> dict:
    row = _row(db, item)
    if kind not in {"poster", "background"}:
        raise ValueError("Invalid artwork kind")
    selections = dict(row.artwork_selections or {})
    selections.pop(kind, None)
    row.artwork_selections = selections
    db.commit()
    return read(db, item)
