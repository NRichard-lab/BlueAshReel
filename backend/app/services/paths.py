from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath, PureWindowsPath

from app.config import AppConfig

NATIVE_WINDOWS = os.name == "nt"


class UnsafeMediaPath(ValueError):
    pass


def valid_windows_component(value: str) -> bool:
    """Exclude Win32 aliases, device names and alternate data streams."""
    stem = value.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    return bool(
        value
        and value not in {".", ".."}
        and not value.endswith((".", " "))
        and not any(character in value for character in '<>:"/\\|?*')
        and not any(ord(character) < 32 for character in value)
        and stem not in reserved
    )


def validate_windows_path_text(value: str) -> None:
    path = PureWindowsPath(value)
    if value.replace("/", "\\").startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        raise UnsafeMediaPath("Windows device paths cannot be selected")
    if not path.is_absolute():
        raise UnsafeMediaPath("Select an absolute Windows folder path from an approved drive or share")
    if any(not valid_windows_component(part) for part in path.parts[1:]):
        raise UnsafeMediaPath("The Windows folder path contains an unsafe component")


def assert_no_link_components(path: Path) -> None:
    """Check ancestors as well as the leaf: a root can sit below a junction."""
    for component in (*reversed(path.absolute().parents), path.absolute()):
        component.lstat()
        if is_link_or_reparse(component):
            raise UnsafeMediaPath("Linked media folders cannot be selected")


@contextmanager
def native_directory_guard(path: Path) -> Iterator[None]:
    """Pin Win32 directory components during validation/enumeration.

    Handles deny write/delete sharing, so a component cannot be replaced or
    changed to a junction between checking it and the directory-only browse.
    This opens read/list handles only; no source media is ever written.
    """
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handles: list[int] = []
    try:
        for component in (*reversed(path.absolute().parents), path.absolute()):
            # LIST_DIRECTORY | READ_ATTRIBUTES; FILE_SHARE_READ; OPEN_EXISTING;
            # BACKUP_SEMANTICS | OPEN_REPARSE_POINT (never follow the leaf).
            # READ_ATTRIBUTES alone does not participate in share denial.
            handle = create_file(str(component), 0x81, 0x1, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
            component.lstat()
            if is_link_or_reparse(component):
                raise UnsafeMediaPath("Linked media folders cannot be selected")
        yield
    finally:
        for handle in reversed(handles):
            close_handle(handle)


def protected_media_directories(config: AppConfig) -> tuple[Path, ...]:
    directories = [config.app_data_dir, config.temp_dir, config.artwork_dir]
    if config.deployment_mode == "native_windows":
        directories.extend(path for path in (config.native_program_dir, config.native_data_dir) if path is not None)
    return tuple(path.expanduser().resolve(strict=False) for path in directories)


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
    if NATIVE_WINDOWS:
        validate_windows_path_text(raw_path)
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
            with native_directory_guard(supplied):
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
        except PermissionError as exc:
            raise UnsafeMediaPath(
                "The service account cannot read the approved media folder; check folder permissions"
            ) from exc
        except (OSError, RuntimeError, ValueError):
            continue
    if canonical is None:
        raise UnsafeMediaPath("Media directory is unavailable or unsafe; check that its drive or share is connected")
    if not canonical.is_dir():
        raise UnsafeMediaPath("Media path must be a directory")
    protected = protected_media_directories(config)
    if any(_is_relative_to(canonical, item) or _is_relative_to(item, canonical) for item in protected):
        raise UnsafeMediaPath("Application data directories cannot be used as media libraries")
    try:
        with native_directory_guard(canonical), os.scandir(canonical) as iterator:
            next(iterator, None)
    except OSError as exc:
        raise UnsafeMediaPath("Media directory is not readable") from exc
    return canonical


def safe_discovered_file(candidate: Path, root: Path) -> Path | None:
    if is_link_or_reparse(candidate):
        return None
    try:
        if NATIVE_WINDOWS:
            assert_no_link_components(candidate)
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError, UnsafeMediaPath):
        return None
    if not _is_relative_to(resolved, root) or not resolved.is_file():
        return None
    return resolved
