from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.config import AppConfig, ApprovedMediaRoot
from app.services.paths import (
    UnsafeMediaPath,
    is_link_or_reparse,
    native_directory_guard,
    protected_media_directories,
    valid_windows_component,
    validate_media_directory,
)

MAX_SELECTION_LENGTH = 8192
MAX_FOLDER_DEPTH = 64
MAX_FOLDER_COMPONENT = 255
MAX_LISTED_FOLDERS = 10_000
MAX_CURSOR_LENGTH = 1024


class InvalidFolderSelection(ValueError):
    pass


class FolderUnavailable(ValueError):
    pass


class FolderPermissionDenied(PermissionError):
    pass


@dataclass(frozen=True)
class FolderSelection:
    root: ApprovedMediaRoot
    path: Path
    relative_parts: tuple[str, ...]


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_selection_id(root: ApprovedMediaRoot, relative_parts: tuple[str, ...], config: AppConfig) -> str:
    payload = json.dumps(
        {"v": 1, "r": root.id, "p": relative_parts}, ensure_ascii=False, separators=(",", ":")
    ).encode()
    encoded = _b64encode(payload)
    signature = _b64encode(hmac.new(config.app_secret_key.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"mf1.{encoded}.{signature}"


def _root_by_id(config: AppConfig, root_id: str) -> ApprovedMediaRoot:
    root = next((item for item in config.approved_media_roots if item.id == root_id), None)
    if root is None:
        raise InvalidFolderSelection("The selected media root is no longer configured")
    return root


def _open_relative_directory(root: Path, parts: tuple[str, ...]) -> int | None:
    """Open a directory without following any component on the Linux container runtime."""
    if os.name == "nt" or not hasattr(os, "O_NOFOLLOW"):
        return None
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) | int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_not_protected(canonical: Path, config: AppConfig) -> None:
    protected = protected_media_directories(config)
    for directory in protected:
        try:
            canonical.relative_to(directory)
        except ValueError:
            pass
        else:
            raise InvalidFolderSelection("Application data folders cannot be selected")
        try:
            directory.relative_to(canonical)
        except ValueError:
            pass
        else:
            raise InvalidFolderSelection("Application data folders cannot be selected")


def resolve_selection_id(selection_id: str, config: AppConfig) -> FolderSelection:
    if not selection_id or len(selection_id) > MAX_SELECTION_LENGTH:
        raise InvalidFolderSelection("The folder selection is invalid")
    try:
        prefix, encoded, supplied_signature = selection_id.split(".", 2)
        expected = _b64encode(hmac.new(config.app_secret_key.encode(), encoded.encode(), hashlib.sha256).digest())
        if prefix != "mf1" or not hmac.compare_digest(supplied_signature, expected):
            raise InvalidFolderSelection("The folder selection is invalid")
        payload = json.loads(_b64decode(encoded))
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or not isinstance(payload.get("r"), str)
            or not isinstance(payload.get("p"), list)
        ):
            raise InvalidFolderSelection("The folder selection is invalid")
        parts = tuple(payload["p"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        if isinstance(exc, InvalidFolderSelection):
            raise
        raise InvalidFolderSelection("The folder selection is invalid") from exc
    if len(parts) > MAX_FOLDER_DEPTH or any(
        not isinstance(part, str)
        or not part
        or len(part) > MAX_FOLDER_COMPONENT
        or part in {".", ".."}
        or "\x00" in part
        or "/" in part
        or "\\" in part
        or ":" in part
        or (os.name == "nt" and not valid_windows_component(part))
        for part in parts
    ):
        raise InvalidFolderSelection("The folder selection is invalid")
    root = _root_by_id(config, payload["r"])
    candidate = root.path.joinpath(*parts)
    current = root.path
    try:
        with native_directory_guard(candidate):
            root.path.lstat()
            if is_link_or_reparse(root.path):
                raise InvalidFolderSelection("Linked media roots cannot be selected")
            canonical_root = root.path.resolve(strict=True)
            if read_only_enforced(canonical_root) is False:
                raise FolderUnavailable("The approved media root is not mounted read-only")
            for part in parts:
                current = current / part
                current.lstat()
                if is_link_or_reparse(current):
                    raise InvalidFolderSelection("Linked media folders cannot be selected")
            canonical = candidate.resolve(strict=True)
            canonical.relative_to(canonical_root)
    except PermissionError as exc:
        raise FolderPermissionDenied("The selected folder cannot be read") from exc
    except FolderUnavailable:
        raise
    except InvalidFolderSelection:
        raise
    except UnsafeMediaPath as exc:
        raise InvalidFolderSelection("Linked media folders cannot be selected") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise FolderUnavailable("The selected folder is unavailable") from exc
    if not canonical.is_dir():
        raise InvalidFolderSelection("The folder selection is not a directory")
    _ensure_not_protected(canonical, config)
    descriptor: int | None = None
    try:
        descriptor = _open_relative_directory(root.path, parts)
        with native_directory_guard(canonical), os.scandir(
            descriptor if descriptor is not None else canonical
        ) as iterator:
            next(iterator, None)
    except PermissionError as exc:
        raise FolderPermissionDenied("The selected folder cannot be read") from exc
    except OSError as exc:
        raise FolderUnavailable("The selected folder is unavailable") from exc
    except UnsafeMediaPath as exc:
        raise InvalidFolderSelection("Linked media folders cannot be selected") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return FolderSelection(root=root, path=canonical, relative_parts=parts)


def selection_from_path(raw_path: str, config: AppConfig) -> FolderSelection:
    canonical = validate_media_directory(raw_path, config)
    for root in config.approved_media_roots:
        try:
            relative = canonical.relative_to(root.path.resolve(strict=True))
        except (OSError, RuntimeError, ValueError):
            continue
        current = root.path
        parts = relative.parts
        for part in parts:
            current = current / part
            if is_link_or_reparse(current):
                raise UnsafeMediaPath("Linked media folders cannot be selected")
        selection_id = make_selection_id(root, parts, config)
        try:
            return resolve_selection_id(selection_id, config)
        except (InvalidFolderSelection, FolderUnavailable, FolderPermissionDenied) as exc:
            raise UnsafeMediaPath("Media directory is unavailable or unsafe") from exc
    raise UnsafeMediaPath("Media directory is outside the configured media roots")


def safe_display_name(value: str) -> str:
    cleaned = "".join(
        character
        for character in value
        if character.isprintable() and unicodedata.category(character) not in {"Cf", "Cc", "Cs"}
    ).strip()
    return cleaned or "Folder"


def selection_display_path(selection: FolderSelection) -> str:
    names = [selection.root.display_name, *(safe_display_name(part) for part in selection.relative_parts)]
    return " / ".join(names)


def friendly_path(path: str | Path, config: AppConfig) -> str:
    try:
        canonical = Path(path).resolve(strict=False)
        for root in config.approved_media_roots:
            try:
                relative = canonical.relative_to(root.path.resolve(strict=False))
            except (OSError, RuntimeError, ValueError):
                continue
            return " / ".join([root.display_name, *(safe_display_name(part) for part in relative.parts)])
    except (OSError, RuntimeError, ValueError):
        pass
    return "Approved media folder"


def root_state(
    root: ApprovedMediaRoot,
) -> tuple[Literal["available", "unavailable", "permission_denied"], Path | None]:
    try:
        with native_directory_guard(root.path):
            root.path.lstat()
            if is_link_or_reparse(root.path):
                return "unavailable", None
            canonical = root.path.resolve(strict=True)
            if not canonical.is_dir():
                return "unavailable", None
            with os.scandir(canonical) as iterator:
                next(iterator, None)
            return "available", canonical
    except PermissionError:
        return "permission_denied", None
    except (OSError, RuntimeError, UnsafeMediaPath):
        return "unavailable", None


def read_only_enforced(path: Path) -> bool | None:
    if os.name == "nt":
        # Application read-only behavior is not an NTFS ACL enforcement claim.
        return None
    mount_info = Path("/proc/self/mountinfo")
    if not mount_info.is_file():
        return None
    try:
        target = path.resolve(strict=True)
        matches: list[tuple[int, frozenset[str]]] = []
        for line in mount_info.read_text(encoding="utf-8").splitlines():
            before, _separator, _after = line.partition(" - ")
            fields = before.split()
            if len(fields) < 6:
                continue
            mount_point = Path(fields[4].replace("\\040", " "))
            try:
                target.relative_to(mount_point)
            except ValueError:
                continue
            matches.append((len(mount_point.parts), frozenset(fields[5].split(","))))
        return "ro" in max(matches, key=lambda item: item[0])[1] if matches else None
    except (OSError, RuntimeError, ValueError):
        return None


def natural_key(value: str) -> tuple[tuple[int, object], ...]:
    return tuple((0, int(part)) if part.isdecimal() else (1, part.casefold()) for part in re.split(r"(\d+)", value))


def make_page_cursor(selection_id: str, offset: int, config: AppConfig) -> str:
    payload = json.dumps(
        {"v": 1, "s": hashlib.sha256(selection_id.encode()).hexdigest(), "o": offset},
        separators=(",", ":"),
    ).encode()
    encoded = _b64encode(payload)
    signature = _b64encode(hmac.new(config.app_secret_key.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"mc1.{encoded}.{signature}"


def resolve_page_cursor(cursor: str | None, selection_id: str, config: AppConfig) -> int:
    if cursor is None:
        return 0
    if not cursor or len(cursor) > MAX_CURSOR_LENGTH:
        raise InvalidFolderSelection("The folder page cursor is invalid")
    try:
        prefix, encoded, supplied_signature = cursor.split(".", 2)
        expected = _b64encode(hmac.new(config.app_secret_key.encode(), encoded.encode(), hashlib.sha256).digest())
        if prefix != "mc1" or not hmac.compare_digest(supplied_signature, expected):
            raise InvalidFolderSelection("The folder page cursor is invalid")
        payload = json.loads(_b64decode(encoded))
        offset = payload.get("o") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("s") != hashlib.sha256(selection_id.encode()).hexdigest()
            or not isinstance(offset, int)
            or not 0 <= offset <= MAX_LISTED_FOLDERS
        ):
            raise InvalidFolderSelection("The folder page cursor is invalid")
        return offset
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        if isinstance(exc, InvalidFolderSelection):
            raise
        raise InvalidFolderSelection("The folder page cursor is invalid") from exc


def folder_state(path: Path) -> Literal["available", "unavailable", "permission_denied"]:
    try:
        with native_directory_guard(path), os.scandir(path) as iterator:
            next(iterator, None)
        return "available"
    except PermissionError:
        return "permission_denied"
    except (OSError, UnsafeMediaPath):
        return "unavailable"


def list_folders(selection: FolderSelection) -> list[FolderSelection]:
    descriptor: int | None = None
    try:
        if is_link_or_reparse(selection.path):
            raise FolderUnavailable("The selected folder is unavailable")
        descriptor = _open_relative_directory(selection.root.path, selection.relative_parts)
        entries: list[FolderSelection] = []
        with native_directory_guard(selection.path), os.scandir(
            descriptor if descriptor is not None else selection.path
        ) as iterator:
            for entry in iterator:
                candidate = selection.path / entry.name
                try:
                    if not entry.is_dir(follow_symlinks=False) or is_link_or_reparse(candidate):
                        continue
                    resolved = candidate.resolve(strict=True)
                    resolved.relative_to(selection.root.path.resolve(strict=True))
                except (OSError, RuntimeError, ValueError):
                    continue
                if len(entries) >= MAX_LISTED_FOLDERS:
                    raise FolderUnavailable("This folder contains too many subfolders to browse safely")
                entries.append(
                    FolderSelection(
                        root=selection.root,
                        path=resolved,
                        relative_parts=selection.relative_parts + (entry.name,),
                    )
                )
    except PermissionError as exc:
        raise FolderPermissionDenied("The selected folder cannot be read") from exc
    except OSError as exc:
        raise FolderUnavailable("The selected folder is unavailable") from exc
    except UnsafeMediaPath as exc:
        raise FolderUnavailable("The selected folder is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return sorted(entries, key=lambda item: (natural_key(item.relative_parts[-1]), item.relative_parts[-1]))


def validated_at() -> datetime:
    return datetime.now(UTC)
