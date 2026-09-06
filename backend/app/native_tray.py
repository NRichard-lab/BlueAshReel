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
import uuid
from pathlib import Path
from typing import Any, Literal

from app.native_install import write_json
from app.native_runtime import (
    Installation,
    http_healthy,
    load_configuration,
    load_installation,
    protect_child_processes,
    write_stop_marker,
)
from app.remote.control import queue_action
from app.remote.protocol import fingerprint
from app.remote.storage import read_json, unprotect_secret
from app.services.paths import assert_no_link_components
from app.services.process_supervisor import lock_file, unlock_file

logger = logging.getLogger(__name__)


def command_action(value: Any, *, runtime_id: str, pid: int, now: float) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        age = now - float(value.get("created_at", 0))
    except (ValueError, TypeError):
        return None
    if not 0 <= age < 30:
        return None
    if "maintenance_id" in value and (value.get("runtime_id") != runtime_id or value.get("target_pid") != pid):
        return None
    action = value.get("action")
    return action if isinstance(action, str) and action in {
        "exit", "pause", "restart", "reconnect", "unpair", "discard_incomplete",
    } else None


def connection_label(status: dict[str, Any], *, healthy: bool, paused: bool = False,
                     now: float | None = None) -> str:
    if paused:
        return "Paused"
    if not healthy:
        return "Paired but Agent offline" if status.get("paired") else "Error"
    now = time.time() if now is None else now
    if now - float(status.get("updated_at", 0)) > 20:
        return "Paired but Agent offline" if status.get("paired") else "Error"
    states = {
        "waiting_portal_approval": "Waiting for Portal approval",
        "waiting_local_confirmation": "Waiting for local confirmation",
        "revoked": "Revoked", "pairing_expired": "Pairing expired", "pairing_cancelled": "Pairing cancelled",
        "pairing_failed": "Error", "connection_failed": "Error",
    }
    if status.get("state") in states:
        return states[status["state"]]
    if not status.get("paired"):
        return "Not paired"
    if status.get("state") == "connected_through_relay":
        return "Update available" if status.get("update_available") is True else "Connected"
    if status.get("state") in {"pairing", "reconnecting", "connecting"}:
        return "Connecting"
    return "Paired but Agent offline"


class Supervisor:
    def __init__(self, installation: Installation, parent_pid: int | None = None) -> None:
        self.installation = installation
        self.config = load_configuration(installation)
        self.children: dict[str, subprocess.Popen[bytes]] = {}
        self.paused = False
        self.started = time.monotonic()
        self.runtime_id = uuid.uuid4().hex
        self.parent_pid = parent_pid

    @property
    def control(self) -> Path:
        return self.config.remote_control_dir or self.installation.data_dir / "remote-control"

    def spawn(self, role: Literal["api", "worker"]) -> None:
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
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def discard_incomplete_pairing(self, expected_fingerprint: Any) -> bool:
        """Explicitly discard only the confirmed incomplete key, after draining its API."""
        status = self.remote_status()
        if (not isinstance(expected_fingerprint, str) or len(expected_fingerprint) != 64
            or status.get("fingerprint") != expected_fingerprint or status.get("paired")
            or status.get("central_revocation_pending")
            or status.get("state") in {"waiting_portal_approval", "waiting_local_confirmation", "pairing"}):
            return False
        self.publish("Running")
        self.stop()  # Shutdown consumes pending callbacks and waits for all credential writers.
        try:
            identity = self.installation.data_dir / "remote-identity" / "identity.json"
            revocation = identity.with_name("revocation.json")
            assert_no_link_components(identity.parent)
            if revocation.exists() or revocation.is_symlink() or not identity.exists():
                return False
            assert_no_link_components(identity)
            record = read_json(identity)
            if not record or set(record) != {"secret", "name"}:
                return False  # A pairing that completed while draining must remain owned and usable.
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            key = Ed25519PrivateKey.from_private_bytes(unprotect_secret(record["secret"]))
            if fingerprint(key.public_key().public_bytes_raw()) != expected_fingerprint:
                return False
            identity.unlink()
            return True
        finally:
            self.start()  # A new API has no old callbacks and chooses a new random callback port.

    def publish(self, override: str | None = None) -> None:
        healthy = bool(self.children) and all(child.poll() is None for child in self.children.values())
        status = self.remote_status()
        label = override or connection_label(status, healthy=healthy, paused=self.paused)
        write_json(self.installation.state_dir / "tray-status.json", {
            "state": label, "healthy": healthy, "updated_at": time.time(), "pid": os.getpid(),
            "runtime_id": self.runtime_id,
            "port": self.installation.port, "fingerprint": status.get("fingerprint"),
            "fingerprint_short": status.get("fingerprint_short"), "paired": status.get("paired", False),
            "agent_id": status.get("agent_id"),
            "central_revocation_pending": status.get("central_revocation_pending", False),
            "update_version": status.get("update_version"),
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
                    action = command_action(value, runtime_id=self.runtime_id, pid=os.getpid(), now=time.time())
                    if action == "exit":
                        clean_exit = True
                        break
                    if action == "unpair":
                        queue_action(self.control, "unpair")
                    if action == "discard_incomplete":
                        self.discard_incomplete_pairing(value.get("expected_fingerprint"))
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
        return bool(kernel.WaitForSingleObject(handle, 0) == 258)
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
