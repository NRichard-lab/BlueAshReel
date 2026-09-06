"""Isolated, bounded Windows service runner for the shared application.

There is deliberately no repository/current-directory configuration discovery.
The installer supplies only an instance directory on the command line, never a
secret. WinSW owns service restart policy; this runner never restarts itself.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import ipaddress
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values

from app.config import AppConfig, get_config, get_product_config
from app.services.paths import assert_no_link_components, is_link_or_reparse, validate_windows_path_text
from app.services.process_supervisor import lock_file, unlock_file

Role = Literal["api", "worker", "web", "proxy"]
ROLES: tuple[Role, ...] = ("api", "worker", "web", "proxy")
logger = logging.getLogger("native_service")
_process_job: int | None = None


class NativeRuntimeError(RuntimeError):
    """Safe diagnostics which contain no host paths or configuration values."""


@dataclass(frozen=True)
class Installation:
    program_dir: Path
    data_dir: Path
    service_prefix: str
    port: int
    api_port: int
    web_port: int
    bind_address: str
    runtime_mode: str = "legacy_service"
    storage: dict[str, str] | None = None

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    def stop_file(self, role: Role) -> Path:
        return self.state_dir / f"stop-{role}"


def load_installation(data_dir: Path) -> Installation:
    if not data_dir.is_absolute() or data_dir == Path(data_dir.anchor) or ".." in data_dir.parts:
        raise NativeRuntimeError("An explicit instance data directory is required")
    assert_no_link_components(data_dir)
    metadata = data_dir / "configuration/installation.json"
    assert_no_link_components(metadata)
    raw = json.loads(metadata.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise NativeRuntimeError("The native installation record is invalid")
    try:
        installation = Installation(
            program_dir=Path(raw["program_dir"]), data_dir=Path(raw["data_dir"]),
            service_prefix=raw["service_prefix"], port=int(raw["port"]),
            api_port=int(raw["api_port"]), web_port=int(raw["web_port"]), bind_address=raw["bind_address"],
            runtime_mode=raw.get("runtime_mode", "legacy_service"), storage=raw.get("storage"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NativeRuntimeError("The native installation record is incomplete") from exc
    if (
        installation.data_dir != data_dir or not installation.program_dir.is_absolute()
        or ".." in installation.program_dir.parts
        or not isinstance(installation.service_prefix, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", installation.service_prefix)
    ):
        raise NativeRuntimeError("The installation record does not match the requested instance")
    assert_no_link_components(installation.program_dir)
    if installation.program_dir.is_relative_to(data_dir) or data_dir.is_relative_to(installation.program_dir):
        raise NativeRuntimeError("Program and persistent data directories must be separate")
    ports = (installation.port, installation.api_port, installation.web_port)
    if len(set(ports)) != 3 or any(not 1024 <= port <= 65535 for port in ports):
        raise NativeRuntimeError("The native service ports are invalid")
    address = ipaddress.ip_address(installation.bind_address)
    if not address.is_loopback and not address.is_private or address.is_unspecified:
        raise NativeRuntimeError("The native listener must use loopback or an explicit private address")
    if not installation.state_dir.is_dir():
        raise NativeRuntimeError("The protected runtime state directory is missing")
    assert_no_link_components(installation.state_dir)
    return installation


def load_configuration(installation: Installation) -> AppConfig:
    source = installation.data_dir / "configuration/.env"
    assert_no_link_components(source)
    values = dotenv_values(source, encoding="utf-8-sig", interpolate=False)
    # Explicit defaults for EVERY model field prevent inherited environment
    # variables (including a developer/Docker database or secret) taking effect.
    explicit: dict[str, Any] = {}
    for name, field in AppConfig.model_fields.items():
        value = values.get(name.upper())
        if value is not None:
            explicit[name] = value
        elif field.is_required():
            raise NativeRuntimeError("A required private native setting is missing")
        else:
            explicit[name] = field.get_default(call_default_factory=True)
    config = AppConfig(_env_file=None, **explicit)
    if installation.runtime_mode == "per_user":
        from app.native_user_install import validate_storage

        if not isinstance(installation.storage, dict):
            raise NativeRuntimeError("The installed per-user storage layout is missing")
        storage = validate_storage(installation.program_dir, installation.data_dir, installation.storage)
        expected = {
            "app_data": config.app_data_dir, "artwork": config.artwork_dir, "temp": config.temp_dir,
            "database": Path(config.database_url.removeprefix("sqlite:///")).parent,
        }
        if any(Path(storage[name]) != path for name, path in expected.items()):
            raise NativeRuntimeError("The configured storage differs from the installed per-user layout")
    if (
        config.deployment_mode != "native_windows" or config.native_data_dir != installation.data_dir
        or config.native_program_dir != installation.program_dir
        or config.windows_service_prefix != installation.service_prefix
    ):
        raise NativeRuntimeError("The private configuration does not match this native instance")
    approved = set((installation.storage or {}).values()) if installation.runtime_mode == "per_user" else set()
    for path in (config.app_data_dir, config.temp_dir, config.artwork_dir):
        if (
            not path.is_absolute() or (not path.is_relative_to(installation.data_dir) and str(path) not in approved)
            or path == installation.data_dir or ".." in path.parts
        ):
            raise NativeRuntimeError("Mutable application storage must remain within the native data directory")
        if os.name == "nt":
            validate_windows_path_text(str(path))
        assert_no_link_components(path)
    database_path = Path(config.database_url.removeprefix("sqlite:///"))
    if (
        not config.database_url.startswith("sqlite:///") or not database_path.is_absolute()
        or (not database_path.is_relative_to(installation.data_dir) and str(database_path.parent) not in approved)
        or ".." in database_path.parts
    ):
        raise NativeRuntimeError("The native database must remain within the isolated data directory")
    if os.name == "nt":
        validate_windows_path_text(str(database_path))
    assert_no_link_components(database_path.parent)
    if database_path.exists() and is_link_or_reparse(database_path):
        raise NativeRuntimeError("The native database cannot be linked storage")
    for configured, name in ((config.ffmpeg_path, "ffmpeg.exe"), (config.ffprobe_path, "ffprobe.exe")):
        expected = installation.program_dir / "runtime/ffmpeg" / name
        if Path(configured) != expected or not expected.is_file() or is_link_or_reparse(expected):
            raise NativeRuntimeError("A required bundled media runtime is unavailable")
    return config


def activate_configuration(installation: Installation, config: AppConfig) -> None:
    known = {name.upper() for name in AppConfig.model_fields}
    for key in list(os.environ):
        if key.upper() in known:
            del os.environ[key]
    for name, value in config.model_dump().items():
        if value is not None:
            os.environ[name.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    os.environ["PRODUCT_CONFIG_FILE"] = str(installation.program_dir / "config/product.json")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    os.chdir(installation.data_dir)
    get_config.cache_clear()
    get_product_config.cache_clear()


def protect_child_processes() -> None:
    """Keep every descendant in a kill-on-exit Windows Job Object.

    The current process owns the only handle. It is intentionally not closed
    while this runner is alive; OS process cleanup closes it and reaps children,
    including FFmpeg, even if the service is terminated without Python cleanup.
    """
    global _process_job
    if os.name != "nt" or _process_job is not None:
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
            ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("processes", wintypes.DWORD),
            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
        ]

    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read", "write", "other", "read_b", "write_b", "other_b")]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimit), ("io", Counters), ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise NativeRuntimeError("Windows child-process protection could not be created")
    limits = ExtendedLimit()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel.CloseHandle(job)
        raise NativeRuntimeError("Windows child-process protection could not be configured")
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        kernel.CloseHandle(job)
        raise NativeRuntimeError("Windows child-process protection could not be activated")
    _process_job = job


def heartbeat(installation: Installation, role: Role, status: str) -> None:
    from app.native_install import write_json

    target = installation.state_dir / f"heartbeat-{role}.json"
    if target.exists() and is_link_or_reparse(target):
        raise NativeRuntimeError("Protected runtime state cannot contain links")
    write_json(target, {
        "role": role, "status": status, "checked_at": datetime.now(UTC).isoformat(),
    })


def write_stop_marker(installation: Installation, role: Role) -> None:
    target = installation.stop_file(role)
    assert_no_link_components(target.parent)
    if (target.exists() or target.is_symlink()) and is_link_or_reparse(target):
        raise NativeRuntimeError("Protected stop markers cannot be links")
    target.write_text("stop\n", encoding="ascii")


def http_healthy(port: int, route: str, host: str = "127.0.0.1") -> bool:
    # Readiness launches the bundled ffprobe and ffmpeg on first use. Windows
    # executable verification can take several seconds after install/restart.
    # A two-second deadline incorrectly marked a healthy workstation failed.
    connection = http.client.HTTPConnection(host, port, timeout=15)
    try:
        connection.request("GET", route)
        response = connection.getresponse()
        response.read(65536)
        return 200 <= response.status < 400
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def database_healthy(config: AppConfig) -> bool:
    database = Path(config.database_url.removeprefix("sqlite:///"))
    try:
        with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
            connection.execute("SELECT 1 FROM application_settings LIMIT 1").fetchone()
        return True
    except (OSError, sqlite3.Error):
        return False


def watch_service(
    installation: Installation, role: Role, stop: threading.Event, check: Callable[[], bool],
    request_stop: Callable[[], None], *, startup_seconds: float = 60, interval: float = 5,
) -> bool:
    """Return false after bounded startup/health failures, never loop-restart."""
    deadline = time.monotonic() + startup_seconds
    ready = False
    failures = 0
    while not stop.is_set():
        if installation.stop_file(role).exists():
            heartbeat(installation, role, "stopping")
            stop.set()
            request_stop()
            return True
        good = check()
        if stop.is_set():
            return True
        if good:
            ready, failures = True, 0
        elif ready:
            failures += 1
        if failures >= 3 or not ready and time.monotonic() >= deadline:
            heartbeat(installation, role, "failed")
            stop.set()
            request_stop()
            return False
        heartbeat(installation, role, "ready" if ready and good else "starting" if not ready else "degraded")
        stop.wait(interval)
    return True


def _stop_child(child: subprocess.Popen[bytes], graceful: Callable[[], None]) -> None:
    if child.poll() is not None:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        graceful()
        child.wait(timeout=15)
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def child_command(installation: Installation, role: Role) -> tuple[list[str], dict[str, str]]:
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {name.upper() for name in AppConfig.model_fields}
        and key.upper() not in {"NODE_OPTIONS", "NODE_PATH", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    environment.update({"NODE_ENV": "production", "NEXT_TELEMETRY_DISABLED": "1", "HOST": "127.0.0.1"})
    environment["BLUEREEL_STOP_FILE"] = str(installation.stop_file(role))
    if role == "web":
        environment["PORT"] = str(installation.web_port)
        return [
            str(installation.program_dir / "runtime/node/node.exe"),
            "--require", str(installation.program_dir / "support/native-guard.cjs"),
            str(installation.program_dir / "frontend/server.js"),
        ], environment
    if role == "proxy":
        return [
            str(installation.program_dir / "runtime/caddy/caddy.exe"), "run", "--config",
            str(installation.data_dir / "configuration/Caddyfile"), "--adapter", "caddyfile",
        ], environment
    raise NativeRuntimeError("This service role has no external child process")


def run_child(installation: Installation, role: Role) -> int:
    command, environment = child_command(installation, role)
    child = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment, cwd=installation.data_dir, close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stop = threading.Event()
    port, route = (installation.web_port, "/") if role == "web" else (installation.port, "/api/v1/health/ready")

    def graceful() -> None:
        write_stop_marker(installation, role)
        if role == "proxy":
            # Caddy's unauthenticated admin endpoint is intentionally disabled.
            # Hidden Windows services have no console for CTRL_BREAK. This
            # stateless ingress is terminated; API/worker state drains separately.
            child.terminate()

    try:
        healthy = watch_service(
            installation, role, stop,
            lambda: child.poll() is None and http_healthy(port, route), lambda: None,
        )
        return 0 if healthy else 1
    finally:
        _stop_child(child, graceful)


def run_python(installation: Installation, config: AppConfig, role: Role) -> int:
    from app.services.outbound import install_native_network_guard

    install_native_network_guard(config, allow_portal=(role == "api" and installation.runtime_mode == "per_user"))
    stop = threading.Event()
    finished = threading.Event()
    result = [True]
    run: Callable[[], None]
    if role == "api":
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(
            "app.main:app", host="127.0.0.1",
            port=installation.port if installation.runtime_mode == "per_user" else installation.api_port,
            log_config=None, access_log=False, timeout_graceful_shutdown=20,
        ))

        def request_stop() -> None:
            server.should_exit = True

        check = lambda: http_healthy(  # noqa: E731
            installation.port if installation.runtime_mode == "per_user" else installation.api_port,
            "/api/v1/health/ready",
        )
        run = lambda: server.run()  # noqa: E731
    else:
        from app.worker import run_worker

        request_stop = stop.set
        check = lambda: database_healthy(config)  # noqa: E731
        run = lambda: run_worker(stop)  # noqa: E731

    def monitor() -> None:
        try:
            result[0] = watch_service(installation, role, stop, check, request_stop)
        except Exception:
            result[0] = False
            stop.set()
            request_stop()
        if stop.is_set() and not finished.wait(90):
            # A hung native call must not leave an unbounded service stop. OS
            # Job Object cleanup reaps descendants; committed jobs recover later.
            logger.error("Native service exceeded its bounded shutdown time")
            os._exit(1)

    thread = threading.Thread(target=monitor, daemon=True, name="native-health")
    thread.start()
    try:
        run()
        return 0 if result[0] else 1
    finally:
        finished.set()
        stop.set()
        thread.join(timeout=3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one isolated native media service")
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=ROLES)
    parser.add_argument("--stop", action="store_true")
    arguments = parser.parse_args(argv)
    from app.logging_config import configure_logging

    configure_logging()
    try:
        installation = load_installation(arguments.data_dir)
        role: Role = arguments.role
        if arguments.stop:
            write_stop_marker(installation, role)
            return 0
        config = load_configuration(installation)
        activate_configuration(installation, config)
        if installation.runtime_mode == "per_user":
            logs = Path((installation.storage or {}).get("logs", installation.data_dir / "logs"))
            assert_no_link_components(logs)
            log_file = logs / (role + ".log")
            if log_file.exists():
                assert_no_link_components(log_file)
            configure_logging(config.log_level, native_log_file=log_file)
        protect_child_processes()
        lock_path = installation.state_dir / f"service-{role}.lock"
        if (lock_path.exists() or lock_path.is_symlink()) and is_link_or_reparse(lock_path):
            raise NativeRuntimeError("Protected service locks cannot be links")
        ownership = lock_file(lock_path)
        try:
            installation.stop_file(role).unlink(missing_ok=True)
            heartbeat(installation, role, "starting")
            logger.info("Native service starting", extra={"fields": {"role": role}})
            code = (
                run_python(installation, config, role) if role in {"api", "worker"} else run_child(installation, role)
            )
            heartbeat(installation, role, "stopped" if code == 0 else "failed")
            logger.info("Native service stopped", extra={"fields": {"role": role, "result": code}})
            return code
        finally:
            unlock_file(ownership)
    except Exception:
        logger.error(
            "Native service could not start or stopped unexpectedly; check installation health and permissions"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
