from __future__ import annotations

import os
import stat
from pathlib import Path, PurePath, PureWindowsPath

from app.config import AppConfig

NATIVE_WINDOWS = os.name == "nt"


class UnsafeMediaPath(ValueError):
    pass


def is_link_or_reparse(path: Path) -> bool:
    try:
        information = path.lstat()
    except OSError:
        return True
    attributes = getattr(information, "st_file_attributes", 0)
    return stat.S_ISLNK(information.st_mode) or bool(attributes & 0x400)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_media_directory(raw_path: str, config: AppConfig) -> Path:
    if not raw_path or "\x00" in raw_path:
        raise UnsafeMediaPath("A media directory is required")
    if not NATIVE_WINDOWS and PureWindowsPath(raw_path).is_absolute():
        raise UnsafeMediaPath(
            "Windows folders must first be configured as approved media roots; "
            "use Browse folders after recreating the containers"
        )
    supplied = Path(raw_path).expanduser()
    if ".." in PurePath(raw_path).parts:
        raise UnsafeMediaPath("Parent-directory traversal is not allowed")
    if not supplied.is_absolute():
        raise UnsafeMediaPath("Media directories must use an absolute path")
    roots = config.approved_media_roots
    if not roots:
        raise UnsafeMediaPath("No media roots are configured on this server")
    supplied_absolute = supplied.absolute()
    lexical_matches: list[tuple[Path, Path]] = []
    for root in roots:
        declared_root = root.path.absolute()
        try:
            relative_supplied = supplied_absolute.relative_to(declared_root)
        except ValueError:
            continue
        lexical_matches.append((declared_root, relative_supplied))
    # Reject unapproved absolute paths before resolving or inspecting them. This
    # also prevents the limited setup session from becoming a host-path oracle.
    if not lexical_matches:
        raise UnsafeMediaPath("Media directory is outside the configured media roots")

    canonical: Path | None = None
    for declared_root, relative_supplied in lexical_matches:
        try:
            declared_root.lstat()
            if is_link_or_reparse(declared_root):
                continue
            current = declared_root
            for part in relative_supplied.parts:
                current /= part
                current.lstat()
                if is_link_or_reparse(current):
                    raise UnsafeMediaPath("Linked media folders cannot be selected")
            canonical_root = declared_root.resolve(strict=True)
            candidate = supplied.resolve(strict=True)
            candidate.relative_to(canonical_root)
            canonical = candidate
            break
        except UnsafeMediaPath:
            raise
        except (OSError, RuntimeError, ValueError):
            continue
    if canonical is None:
        raise UnsafeMediaPath("Media directory is unavailable or unsafe")
    if not canonical.is_dir():
        raise UnsafeMediaPath("Media path must be a directory")
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
    if is_link_or_reparse(candidate):
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not _is_relative_to(resolved, root) or not resolved.is_file():
        return None
    return resolved
