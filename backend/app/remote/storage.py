"""Durable service-private files; Windows identities additionally use user-scope DPAPI."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def state_write_lock(path: Path) -> Iterator[None]:
    """Coordinate Windows atomic replacements of one destination across processes."""
    if os.name != "nt":
        yield
        return
    from ctypes import wintypes

    # Resolve only the parent: replacing a link must never follow its leaf.
    # Local namespace and the process token's default DACL keep this within the
    # interactive user session. No lock file or host path is exposed by the name.
    canonical = os.path.normcase(str(path.parent.resolve(strict=False) / path.name))
    name = "Local\\BlueAshReel-State-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        result = kernel.WaitForSingleObject(handle, 500)
        if result == 0x102:
            raise TimeoutError("Agent state writer remained busy beyond the bounded wait")
        if result not in {0, 0x80}:  # WAIT_OBJECT_0 or an abandoned owner's mutex.
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        try:
            if acquired and not kernel.ReleaseMutex(handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)


def protect_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Remote storage cannot be a symbolic link")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
        return
    # A fresh, protected DACL grants the current service identity and SYSTEM.
    # Administrators can take ownership, as with other local protected storage.
    output = subprocess.check_output(
        [str(Path(os.environ["SYSTEMROOT"]) / "System32/whoami.exe"), "/user", "/fo", "csv", "/nh"],
        creationflags=subprocess.CREATE_NO_WINDOW,
        text=True,
    )
    sid = output.strip().split(",")[-1].strip('"')
    if not sid.startswith("S-1-") or any(character not in "S-0123456789" for character in sid):
        raise RuntimeError("Cannot identify remote-storage service account")
    descriptor = ctypes.c_void_p()
    advapi = ctypes.windll.advapi32
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        f"D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError()
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ctypes.WinError()
    finally:
        ctypes.windll.kernel32.LocalFree(descriptor)


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Replace atomically; never expose an incompletely written credential or command."""
    with state_write_lock(path):
        _write_json_locked(path, value)


def _write_json_locked(path: Path, value: dict[str, Any]) -> None:
    temporary: Path | None = None
    prepared = False
    deadline = time.monotonic() + 0.5
    try:
        while True:
            try:
                if not prepared:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                        temporary = None
                    # A different destination's native directory guard can
                    # briefly conflict before replacement, including creation.
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    descriptor, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
                    temporary = Path(name)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                        json.dump(value, stream, ensure_ascii=True, separators=(",", ":"))
                        stream.flush()
                        os.fsync(stream.fileno())
                    if os.name != "nt":
                        os.chmod(temporary, 0o600)
                    prepared = True
                os.replace(temporary, path)
                temporary = None
                break
            except OSError as error:
                if not _wait_for_windows_sharing(error, deadline):
                    raise
        if os.name != "nt":
            directory = os.open(path.parent, getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None:
            while True:
                try:
                    temporary.unlink(missing_ok=True)
                    break
                except OSError as error:
                    if not _wait_for_windows_sharing(error, deadline):
                        raise


def _wait_for_windows_sharing(error: OSError, deadline: float) -> bool:
    if os.name != "nt" or getattr(error, "winerror", None) not in {32, 33}:
        return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(0.025, remaining))
    return True


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError("Invalid remote state file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid remote state")
    return value


def protect_secret(secret: bytes) -> str:
    if os.name != "nt":
        return "protected-file:" + base64.b64encode(secret).decode("ascii")
    return "dpapi-user:" + base64.b64encode(_dpapi(secret, decrypt=False)).decode("ascii")


def unprotect_secret(secret: str) -> bytes:
    protection, encoded = secret.split(":", 1)
    value = base64.b64decode(encoded, validate=True)
    if protection == "dpapi-user" and os.name == "nt":
        return _dpapi(value, decrypt=True)
    if protection == "protected-file" and os.name != "nt":
        return value
    raise ValueError("Identity protection does not match this platform/service account")


def _dpapi(value: bytes, *, decrypt: bool) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    method = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    # CRYPTPROTECT_UI_FORBIDDEN, user scope; machine-wide DPAPI is deliberately not used.
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(result.data)
