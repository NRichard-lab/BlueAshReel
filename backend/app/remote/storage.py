"""Durable service-private files; Windows identities additionally use user-scope DPAPI."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


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
