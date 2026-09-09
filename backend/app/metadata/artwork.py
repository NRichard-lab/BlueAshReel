"""Private content-addressed artwork cache, served only through local authorization."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.metadata.provider import MetadataProvider, ProviderError
from app.metadata.types import ArtworkSource
from app.models import LibraryPath, LocalArtwork, MediaItem
from app.services.paths import assert_no_link_components

MAX_IMAGE_BYTES = 8 * 1024 * 1024


def cached_file(config: AppConfig, relative: str) -> Path:
    if not re.fullmatch(r"metadata/[a-f0-9]{2}/[a-f0-9]{64}\.(jpg|png|webp)", relative):
        raise HTTPException(404, "Local artwork unavailable")
    try:
        root = config.artwork_dir.absolute()
        candidate = root / relative
        assert_no_link_components(candidate)
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
        if not candidate.is_file() or not 0 < candidate.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError("Invalid cache file")
        return candidate
    except (OSError, ValueError) as exc:
        raise HTTPException(404, "Local artwork unavailable") from exc


def _format(data: bytes, declared: str) -> tuple[str, str]:
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ProviderError("invalid_image")
    if data.startswith(b"\xff\xd8\xff") and declared == "image/jpeg":
        return "jpg", "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n") and declared == "image/png":
        return "png", "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and declared == "image/webp":
        return "webp", "image/webp"
    raise ProviderError("invalid_image")


def cache_image(
    db: Session,
    config: AppConfig,
    provider: MetadataProvider,
    owner: MediaItem,
    source: ArtworkSource,
    *,
    artwork_type: str | None = None,
) -> LocalArtwork:
    kind = artwork_type or {"backdrop": "background", "still": "poster"}.get(source.kind, source.kind)
    root_id = db.scalar(
        select(LibraryPath.id)
        .where(LibraryPath.library_id == owner.library_id, LibraryPath.enabled.is_(True))
        .order_by(LibraryPath.id)
        .limit(1)
    )
    if root_id is None:
        raise ProviderError("library_unavailable")
    identity = f"{source.provider}:{source.provider_path}"
    existing = db.scalar(
        select(LocalArtwork).where(
            LocalArtwork.media_item_id == owner.id,
            LocalArtwork.library_path_id == root_id,
            LocalArtwork.artwork_type == kind,
            LocalArtwork.source_path == identity,
        )
    )
    shared = db.scalar(
        select(LocalArtwork)
        .where(
            LocalArtwork.provider == source.provider,
            LocalArtwork.provider_path == source.provider_path,
            LocalArtwork.cached_path.is_not(None),
        )
        .order_by(LocalArtwork.id)
        .limit(1)
    )
    valid = False
    if shared and shared.cached_path:
        try:
            path = cached_file(config, shared.cached_path)
            valid = hashlib.sha256(path.read_bytes()).hexdigest() == shared.fingerprint
        except HTTPException:
            pass
    if valid and shared:
        relative, digest, content_type = shared.cached_path, shared.fingerprint, shared.content_type
    else:
        data, declared = provider.download_image(source)
        extension, content_type = _format(data, declared)
        digest = hashlib.sha256(data).hexdigest()
        relative = f"metadata/{digest[:2]}/{digest}.{extension}"
        target = config.artwork_dir.absolute() / relative
        root = config.artwork_dir.absolute()
        assert_no_link_components(root)
        for directory in (root / "metadata", target.parent):
            directory.mkdir(exist_ok=True)
            assert_no_link_components(directory)
        if target.exists():
            assert_no_link_components(target)
        # Atomic replacement also repairs a corrupted cache file. Source media is never written.
        descriptor, temporary = tempfile.mkstemp(prefix=".artwork-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if existing is None:
        existing = LocalArtwork(
            media_item_id=owner.id,
            library_path_id=root_id,
            source_path=identity,
            artwork_type=kind,
            fingerprint=digest,
        )
        db.add(existing)
    existing.cached_path = relative
    existing.fingerprint = digest
    existing.provider = source.provider
    existing.provider_path = source.provider_path
    existing.content_type = content_type
    db.flush()
    return existing
