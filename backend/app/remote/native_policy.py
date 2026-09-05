"""Fail closed unless the native child is in AppContainer and cannot read media state."""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
from ctypes import wintypes
from pathlib import Path

from app.remote.connector import Connector
from app.remote.network import pin_resolution
from app.remote.storage import read_json


def require_appcontainer() -> None:
    if os.name != "nt":
        raise RuntimeError("Native isolation requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        value, needed = wintypes.DWORD(), wintypes.DWORD()
        if not advapi.GetTokenInformation(token, 29, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        if value.value != 1:
            raise RuntimeError("Remote connector requires an AppContainer token")
    finally:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(token)


def verify_denied(paths: list[str]) -> None:
    if not paths:
        raise ValueError("Native privacy policy has no protected paths")
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Invalid native privacy boundary")
        try:
            with os.scandir(path) as entries:
                next(entries, None)
        except PermissionError:
            continue
        except NotADirectoryError:
            try:
                with path.open("rb"):
                    pass
            except PermissionError:
                continue
        except FileNotFoundError:
            # A missing/disconnected source cannot establish a permission boundary.
            raise ValueError("Protected native path unavailable; revalidate before remote access") from None
        raise ValueError("Native connector can read a protected path; remote access refused")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    require_appcontainer()
    policy = read_json(args.policy)
    if set(policy) != {"control_dir", "identity_dir", "product_config", "denied_paths", "allowed_ips"}:
        raise ValueError("Invalid native connector policy")
    verify_denied(policy["denied_paths"])
    pin_resolution(policy["allowed_ips"])
    product = json.loads(Path(policy["product_config"]).read_text(encoding="utf-8"))
    connector = Connector(Path(policy["control_dir"]), Path(policy["identity_dir"]), product["version"],
                          preprotected_native=True)
    asyncio.run(connector.run())


if __name__ == "__main__":
    main()
