"""Private, fixed-instance probes called only by test_instance.ps1.

This file is source-side acceptance tooling, not an installed service. Results
contain only allowlisted booleans/counts/hashes; never configuration or log text.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any

PREFIX = "BlueReelDevelopment"
COUNT_TABLES = ("users", "libraries", "media_items", "media_files", "watch_progress", "scan_jobs", "background_jobs")
PROBE_STAGE = "initialization"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def configuration(program: Path, data: Path) -> tuple[Any, dict[str, Any]]:
    from app.native_install import _explicit_configuration, read_installation
    from dotenv import dotenv_values

    metadata = read_installation(data)
    if metadata["instance"] != "development" or metadata["service_prefix"] != PREFIX:
        raise ValueError("Wrong disposable instance")
    if Path(metadata["program_dir"]) != program or Path(metadata["data_dir"]) != data:
        raise ValueError("Unexpected disposable paths")
    config = _explicit_configuration(dict(dotenv_values(data / "configuration/.env", interpolate=False)))
    if config.deployment_mode != "native_windows" or config.outbound_integrations_enabled:
        raise ValueError("Disposable instance is not strict-local native")
    return config, metadata


def inventory(program: Path, data: Path) -> dict[str, Any]:
    config, metadata = configuration(program, data)
    db = data / "database/app.db"
    counts: dict[str, int | None] = {}
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        connection.execute("PRAGMA query_only=ON")
        available = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in COUNT_TABLES:
            counts[table] = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in available else None
    return {
        "secret_sha256": hashlib.sha256(config.app_secret_key.encode()).hexdigest(),
        "environment_sha256": digest(data / "configuration/.env"),
        "metadata_sha256": digest(data / "configuration/installation.json"),
        "database_counts": counts,
        "approved_root_count": len(config.approved_media_roots),
        "loopback_default": metadata["bind_address"] == "127.0.0.1",
        "port": int(metadata["port"]),
    }


def recovery_actions() -> dict[str, Any]:
    """Query SCM effective recovery policy; never change its configuration.

    Layout: https://learn.microsoft.com/windows/win32/api/winsvc/ns-winsvc-service_failure_actionsw
    """
    class FailureActions(ctypes.Structure):
        _fields_ = [
            ("reset", wintypes.DWORD), ("reboot", wintypes.LPWSTR), ("command", wintypes.LPWSTR),
            ("count", wintypes.DWORD), ("actions", ctypes.c_void_p),
        ]

    class Action(ctypes.Structure):
        _fields_ = [("kind", wintypes.DWORD), ("delay", wintypes.DWORD)]

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenSCManagerW.restype = wintypes.HANDLE
    api.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    api.OpenServiceW.restype = wintypes.HANDLE
    api.QueryServiceConfig2W.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.QueryServiceConfig2W.restype = wintypes.BOOL
    api.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    api.CloseServiceHandle.restype = wintypes.BOOL
    manager = api.OpenSCManagerW(None, None, 1)
    if not manager:
        raise OSError("SCM query unavailable")
    results: dict[str, Any] = {}
    try:
        for role in ("API", "Worker", "Web", "Proxy"):
            handle = api.OpenServiceW(manager, PREFIX + role, 1)
            if not handle:
                raise OSError("Service query unavailable")
            try:
                size = wintypes.DWORD()
                api.QueryServiceConfig2W(handle, 2, None, 0, ctypes.byref(size))
                if not 1 <= size.value <= 1024 * 1024:
                    raise OSError("Recovery policy unavailable")
                buffer = ctypes.create_string_buffer(size.value)
                if not api.QueryServiceConfig2W(handle, 2, buffer, size.value, ctypes.byref(size)):
                    raise OSError("Recovery query failed")
                config = FailureActions.from_buffer(buffer)
                if config.count > 10 or not config.actions:
                    raise ValueError("Unexpected recovery policy")
                actions = [Action.from_address(config.actions + offset * ctypes.sizeof(Action)) for offset in range(config.count)]
                sequence = [{"action": int(action.kind), "delay_ms": int(action.delay)} for action in actions]
                results[role] = {
                    "reset_seconds": int(config.reset), "actions": sequence,
                    "bounded_two_restarts": [item["action"] for item in sequence] == [1, 1, 0],
                    "no_reboot_or_command": not bool(config.reboot) and not bool(config.command),
                }
            finally:
                api.CloseServiceHandle(handle)
    finally:
        api.CloseServiceHandle(manager)
    return results


def verify_firewall(program: Path, data: Path) -> dict[str, Any]:
    global PROBE_STAGE

    config, _metadata = configuration(program, data)
    from app.services.outbound import (
        OutboundConnectionDisabled,
        _query_windows_firewall,
        _verified_firewall,
        install_native_network_guard,
    )

    PROBE_STAGE = "effective_firewall_query"
    policy = _query_windows_firewall(PREFIX)
    verified = _verified_firewall(policy, PREFIX, program)
    # Documentation-only literal address. No name resolution, HTTP payload, or
    # application hook. Only WSAEACCES proves OS policy denial; timeout/refusal
    # is inconclusive and explicitly fails this acceptance check.
    PROBE_STAGE = "direct_ip_without_hook"
    direct_error: int | None = None
    connected = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        try:
            connection.connect(("192.0.2.1", 443))
            connected = True
        except OSError as error:
            direct_error = getattr(error, "winerror", None) or error.errno
    PROBE_STAGE = "python_hostname_guard"
    install_native_network_guard(config)
    hostname_blocked = False
    try:
        socket.getaddrinfo("bluereel-acceptance.invalid", 443)
    except OutboundConnectionDisabled:
        hostname_blocked = True
    return {
        "effective_rules_verified": verified,
        "enabled_profile_count": sum(value == "True" for value in policy.get("profiles", [])),
        "expected_rule_count": 5,
        "matching_rule_count": sum(row.get("name", "").startswith(PREFIX + "-Outbound-") for row in policy.get("rules", [])),
        "direct_ip_without_hook": {"connected": connected, "windows_error": direct_error, "os_access_denied": direct_error == 10013},
        "python_hostname_blocked_before_dns": hostname_blocked,
        "passed": verified and direct_error == 10013 and not connected and hostname_blocked,
    }


def health_snapshot(program: Path, data: Path) -> dict[str, bool]:
    _config, metadata = configuration(program, data)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://127.0.0.1:{int(metadata['port'])}/api/v1/health/ready"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=2) as response:  # Literal loopback origin only.
                if response.status == 200 and json.load(response).get("status") in {"ready", "ok"}:
                    return {"healthy": True}
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    # Unlike installer health(), diagnostics never remove maintenance state.
    return {"healthy": False}


def main() -> int:
    global PROBE_STAGE

    try:
        phase, program_text, data_text = sys.argv[1:]
        program, data = Path(program_text), Path(data_text)
        PROBE_STAGE = "configuration"
        config, _metadata = configuration(program, data)
        # Application models create their engine at import time. Activate only
        # this explicit native configuration before importing those modules;
        # never let their default get_config() inspect inherited Docker settings.
        PROBE_STAGE = "activate_configuration"
        from app.native_runtime import activate_configuration, load_installation

        activate_configuration(load_installation(data), config)
        PROBE_STAGE = phase
        if phase == "inventory":
            result = inventory(program, data)
            result["recovery"] = recovery_actions()
        elif phase == "snapshot":
            result = inventory(program, data)
        elif phase == "firewall":
            result = verify_firewall(program, data)
        elif phase == "health":
            result = health_snapshot(program, data)
        elif phase == "backup":
            from app.native_install import backup

            from scripts.restore_validate import validate

            archive = backup(data)
            validate(archive)
            result = {"validated_without_restore": True, "sha256": digest(archive), "bytes": archive.stat().st_size}
        else:
            raise ValueError("Unknown probe")
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - redact every third-party diagnostic at this boundary
        print(json.dumps({"probe_failed": True, "error_type": type(error).__name__, "operation": PROBE_STAGE}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
