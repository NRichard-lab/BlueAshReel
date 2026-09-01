from __future__ import annotations

import os
from pathlib import Path, PurePath

from app.config import AppConfig


class UnsafeMediaPath(ValueError):
    pass


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_media_directory(raw_path: str, config: AppConfig) -> Path:
    if not raw_path or "\x00" in raw_path:
        raise UnsafeMediaPath("A media directory is required")
    supplied = Path(raw_path).expanduser()
    if ".." in PurePath(raw_path).parts:
        raise UnsafeMediaPath("Parent-directory traversal is not allowed")
    if not supplied.is_absolute():
        raise UnsafeMediaPath("Media directories must use an absolute path")
    try:
        canonical = supplied.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise UnsafeMediaPath("Media directory does not exist or cannot be resolved") from exc
    if not canonical.is_dir():
        raise UnsafeMediaPath("Media path must be a directory")

    roots = config.allowed_media_roots
    if not roots:
        raise UnsafeMediaPath("No media roots are configured on this server")
    if not any(_is_relative_to(canonical, root) for root in roots):
        raise UnsafeMediaPath("Media directory is outside the configured media roots")
    protected = (
        config.app_data_dir.expanduser().resolve(),
        config.temp_dir.expanduser().resolve(),
        config.artwork_dir.expanduser().resolve(),
    )
    if any(_is_relative_to(canonical, item) or _is_relative_to(item, canonical) for item in protected):
        raise UnsafeMediaPath("Application data directories cannot be used as media libraries")
    try:
        with os.scandir(canonical) as iterator:
            next(iterator, None)
    except OSError as exc:
        raise UnsafeMediaPath("Media directory is not readable") from exc
    return canonical


def safe_discovered_file(candidate: Path, root: Path) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _is_relative_to(resolved, root) or not resolved.is_file():
        return None
    return resolved
