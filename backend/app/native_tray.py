"""Per-user runtime supervisor for the native Windows tray host.

No service registration, elevation, local passwords, or media scanning at install.
Child ownership is established with process handles and an instance lock; restart
never begins until the prior owned children have exited.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from app.native_install import write_json
from app.native_runtime import (
    Installation,
    http_healthy,
    load_configuration,
    load_installation,
    protect_child_processes,
    write_stop_marker,
)
from app.services.paths import assert_no_link_components
from app.services.process_supervisor import lock_file, unlock_file

logger = logging.getLogger(__name__)


def connection_label(status: dict[str, Any], *, healthy: bool, paused: bool = False,
                     now: float | None = None) -> str:
    if paused:
        return "Paused"
    if not healthy:
        return "Agent error"
    now = time.time() if now is None else now
    if now - float(status.get("updated_at", 0)) > 20:
        return "Portal unavailable"
    if not status.get("paired"):
        return "Unpaired"
    if status.get("state") == "connected_through_relay":
        return "Update available" if status.get("update_available") is True else "Connected"
    if status.get("state") in {"pairing", "reconnecting", "connecting"}:
        return "Connecting"
    return "Portal unavailable"


class Supervisor:
    def __init__(self, installation: Installation, parent_pid: int | None = None) -> None:
        self.installation = installation
        self.config = load_configuration(installation)
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self.paused = False
        self.started = time.monotonic()
        self.parent_pid = parent_pid

    @property
    def control(self) -> Path:
        return self.config.remote_control_dir or self.installation.data_dir / "remote-control"

    def spawn(self, role: str) -> None:
        installation = self.installation
        python = installation.program_dir / "runtime/python/pythonw.exe"
        command = [str(python), "-I", "-B", "-m"]
        installation.stop_file(role).unlink(missing_ok=True)
        command += ["app.native_runtime", "--data-dir", str(installation.data_dir), "--role", role]
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
        self.children[role] = subprocess.Popen(command, cwd=installation.data_dir, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)

    def start(self) -> None:
        if any(child.poll() is None for child in self.children.values()):
            raise RuntimeError("The previous Agent process has not ended")
        self.children.clear()
        for directory in (self.control, self.installation.data_dir / "remote-identity"):
            assert_no_link_components(directory)
            directory.mkdir(parents=True, exist_ok=True)
        write_json(self.control / "desired.json", {"enabled": True})
        for role in ("api", "worker"):
            self.spawn(role)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if any(child.poll() is not None for child in self.children.values()):
                raise RuntimeError("A required Agent process stopped during startup")
            if http_healthy(self.installation.port, "/api/v1/health/ready"):
                self.paused = False
                return
            time.sleep(0.5)
        raise RuntimeError("The Agent did not become healthy")

    def stop(self) -> None:
        write_json(self.control / "desired.json", {"enabled": False})
        # The connector lives in API lifespan and drains browser sessions before
        # the API and scan worker stop. Device identity remains separate.
        if self.children:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self.remote_status().get("state") == "disabled":
                    break
                time.sleep(0.25)
        for role in ("api", "worker"):
            write_stop_marker(self.installation, role)
        for child in self.children.values():
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        self.children.clear()
        # Only ephemeral queue files are removed; durable identity/catalog stay.
        spool = self.installation.state_dir / "tray-requests"
        if spool.exists():
            assert_no_link_components(spool)
            for path in spool.glob("*.json"):
                assert_no_link_components(path)
                path.unlink(missing_ok=True)
        write_json(self.control / "status.json", {"state": "agent_offline", "updated_at": time.time()})

    def remote_status(self) -> dict[str, Any]:
        try:
            path = self.control / "status.json"
            assert_no_link_components(path)
            if path.stat().st_size > 32768:
                return {}
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def publish(self, override: str | None = None) -> None:
        healthy = bool(self.children) and all(child.poll() is None for child in self.children.values())
        label = override or connection_label(self.remote_status(), healthy=healthy, paused=self.paused)
        write_json(self.installation.state_dir / "tray-status.json", {
            "state": label, "healthy": healthy, "updated_at": time.time(), "pid": os.getpid(),
            "port": self.installation.port, "fingerprint": self.remote_status().get("fingerprint"),
            "update_version": self.remote_status().get("update_version"),
        })

    def run(self) -> int:
        self.publish("Running")
        clean_exit = False
        try:
            self.start()
            while True:
                if self.parent_pid and not parent_alive(self.parent_pid):
                    clean_exit = True
                    break
                command = self.installation.state_dir / "tray-command.json"
                if command.exists():
                    assert_no_link_components(command)
                    value = json.loads(command.read_text(encoding="utf-8-sig"))
                    command.unlink(missing_ok=True)
                    action = value.get("action") if time.time() - float(value.get("created_at", 0)) < 30 else None
                    if action == "exit":
                        clean_exit = True
                        break
                    if action in {"pause", "restart", "reconnect"}:
                        self.publish("Running")
                        self.stop()
                        if action == "pause":
                            self.paused = True
                        else:
                            self.start()
                self.publish()
                time.sleep(1)
            return 0
        except Exception as exc:
            logger.error(
                "Agent runtime could not start or continue (%s); review local runtime settings", type(exc).__name__
            )
            self.publish("Agent error")
            return 1
        finally:
            self.stop()
            self.publish("Stopped" if clean_exit else "Agent error")


def parent_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Blue Ash Reel user-session runtime")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    installation = load_installation(args.data_dir)
    if installation.runtime_mode != "per_user":
        raise RuntimeError("This installation requires validated migration to the per-user Agent")
    protect_child_processes()
    from logging.handlers import RotatingFileHandler
    log_directory = Path((installation.storage or {}).get("logs", installation.data_dir / "logs"))
    assert_no_link_components(log_directory)
    log_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
        log_directory / "agent.log", maxBytes=1024 * 1024, backupCount=4, encoding="utf-8")])
    lock = lock_file(installation.state_dir / "tray-runtime.lock")
    try:
        return Supervisor(installation, args.parent_pid).run()
    finally:
        unlock_file(lock)


if __name__ == "__main__":
    raise SystemExit(main())
