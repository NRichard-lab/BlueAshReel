"""Small local control spool, shared only with the isolated connector."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

from app.remote.storage import read_json, write_json

PORTAL_ORIGIN = "https://blueashreel.com"
SHARED_FIELDS = [
    "Opaque Agent ID", "Public Ed25519 identity and fingerprint", "Owner-chosen friendly name",
    "Software version", "Operating-system category", "Encrypted protocol capabilities",
    "Online/offline status and heartbeat", "Coarse connector health", "Opaque encrypted frames",
]
_lock = Lock()


def snapshot(directory: Path | None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "available": directory is not None, "enabled": False, "paired": False, "state": "disabled",
        "account_email": None, "agent_id": None, "name": None, "fingerprint": None,
        "last_heartbeat": None, "central_revocation_pending": False,
        "update_available": False, "update_version": None,
        "relay_endpoint": PORTAL_ORIGIN.replace("https:", "wss:") + "/ws/relay/agent",
        "shared_fields": SHARED_FIELDS, "remote_media_available": False,
    }
    if directory is None:
        return state
    status = read_json(directory / "status.json")
    # Do not reflect unknown data, paths, secrets or arbitrary exception text into the Owner UI.
    for key in set(state) - {"available", "shared_fields", "relay_endpoint"}:
        if key in status:
            state[key] = status[key]
    state["enabled"] = read_json(directory / "desired.json").get("enabled") is True
    fresh = time.time() - status.get("updated_at", 0) < 15
    state["available"] = fresh
    if not fresh:
        state["state"] = "agent_offline" if state["enabled"] else "disabled"
    if list(directory.glob("command-*.json")):
        state["state"] = "action_pending" if fresh else "agent_offline"
    return state


def queue_action(directory: Path, action: str, payload: dict[str, Any] | None = None) -> str:
    if action not in {"pair", "reconnect", "unpair", "revoke"}:
        raise ValueError("Unsupported action")
    with _lock:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if list(directory.glob("command-*.json")):
            raise ValueError("A remote-access action is already pending")
        identifier = uuid.uuid4().hex
        command = {"id": identifier, "action": action, "created_at": time.time(), **(payload or {})}
        write_json(directory / f"command-{identifier}.json", command)
        # Disablement closes both sockets before the connector processes revocation.
        write_json(directory / "desired.json", {"enabled": action in {"pair", "reconnect"}})
        return identifier
