"""Outbound-only remote connector. Run with its own account/container and zero media mounts.

This module intentionally imports no application configuration, database, media or
HTTP routing modules. It has no listening sockets and no generic request forwarder.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import logging
import re
import secrets
import ssl
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.remote.control import PORTAL_ORIGIN
from app.remote.network import pin_resolution
from app.remote.protocol import MAX_FRAME, MAX_SESSIONS, EncryptedSession, canonical, decode, encode, fingerprint
from app.remote.storage import protect_directory, protect_secret, read_json, unprotect_secret, write_json

BROKER_URL = PORTAL_ORIGIN.replace("https:", "wss:") + "/ws/agent"
RELAY_URL = PORTAL_ORIGIN.replace("https:", "wss:") + "/ws/relay/agent"
OS_CATEGORY = {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(sys.platform, "other")


class CredentialRejected(Exception):
    """A durable identity rejection requires fresh local Owner pairing."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def post_control(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path != "/api/agents/pair" and not re.fullmatch(r"/api/agents/[0-9a-f-]{36}/revoke", path):
        raise ValueError("Unsupported public control request")
    # Ignore proxy environment variables and reject redirects: credentials go only
    # to the fixed, certificate-validated canonical origin on TLS port 443.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls_context()), NoRedirect()
    )
    request = urllib.request.Request(  # noqa: S310 - constant HTTPS origin and allowlisted paths only
        PORTAL_ORIGIN + path, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"}, method="POST",
    )
    with opener.open(request, timeout=10) as response:
        raw = response.read(MAX_FRAME + 1)
        if len(raw) > MAX_FRAME:
            raise ValueError("Oversized control response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid control response")
        return result


def reconnect_delay(attempt: int) -> float:
    return min(60.0, 2.0 ** min(max(attempt, 0), 6)) * (0.75 + secrets.randbelow(251) / 1000)


class Connector:
    def __init__(self, control: Path, identity: Path, version: str, *, preprotected_native: bool = False) -> None:
        if control.resolve() == identity.resolve() or control.resolve() in identity.resolve().parents:
            raise ValueError("The identity directory must be separate from the shared control spool")
        if not re.fullmatch(r"[A-Za-z0-9.+_-]{1,40}", version):
            raise ValueError("Invalid software version")
        self.control = control
        self.identity = identity
        self.version = version
        if not preprotected_native:
            protect_directory(control)
            protect_directory(identity)
        self.credentials: dict[str, Any] = read_json(identity / "identity.json")
        self.private_key: Ed25519PrivateKey | None = None
        if self.credentials:
            self.private_key = Ed25519PrivateKey.from_private_bytes(unprotect_secret(self.credentials["secret"]))
            if self.credentials.get("agent_id"):
                uuid.UUID(self.credentials["agent_id"])
        self.connection: asyncio.Task[None] | None = None
        self.state = "disabled"
        self.last_heartbeat: str | None = None
        self.retry_at = 0.0
        self.attempt = 0
        self.tombstone_retry_at = 0.0
        self.tombstone_attempt = 0

    @property
    def paired(self) -> bool:
        return bool(self.credentials.get("agent_id") and self.private_key)

    def enabled(self) -> bool:
        return read_json(self.control / "desired.json").get("enabled") is True

    def publish(self) -> None:
        public = self.private_key.public_key().public_bytes_raw() if self.private_key else None
        write_json(self.control / "status.json", {
            "enabled": self.enabled(), "paired": self.paired, "state": self.state,
            "agent_id": self.credentials.get("agent_id"), "account_email": self.credentials.get("account_email"),
            "name": self.credentials.get("name"), "fingerprint": fingerprint(public) if public else None,
            "last_heartbeat": self.last_heartbeat, "updated_at": time.time(),
            "central_revocation_pending": (self.identity / "revocation.json").exists(),
        })

    async def stop_connection(self) -> None:
        if self.connection:
            self.connection.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.connection
            self.connection = None

    async def pair(self, command: dict[str, Any]) -> None:
        if self.paired or (self.identity / "revocation.json").exists():
            raise ValueError("Previous pairing has not been revoked")
        name = command.get("name")
        code = command.get("code")
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or not name.isprintable():
            raise ValueError("Invalid friendly name")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z2-7]{26}", code):
            raise ValueError("Invalid pairing code")
        self.private_key = Ed25519PrivateKey.generate()
        public = encode(self.private_key.public_key().public_bytes_raw())
        self.credentials = {"secret": protect_secret(self.private_key.private_bytes_raw()), "name": name}
        write_json(self.identity / "identity.json", self.credentials)
        self.state = "pairing"
        self.publish()
        signature = encode(self.private_key.sign(canonical(
            "blueashreel-pair-v1", code, public, name, OS_CATEGORY, self.version
        )))
        result = await asyncio.to_thread(post_control, "/api/agents/pair", {
            "code": code, "public_key": public, "name": name, "os": OS_CATEGORY,
            "version": self.version, "signature": signature,
        })
        if (
            result.get("protocol") != 1 or result.get("broker_url") != BROKER_URL
            or result.get("relay_url") != RELAY_URL
            or result.get("fingerprint") != fingerprint(decode(public, 32))
        ):
            raise ValueError("Invalid pairing identity or endpoints")
        for field in ("agent_id", "user_id"):
            if str(uuid.UUID(result[field])) != result[field]:
                raise ValueError("Invalid account association")
        if not isinstance(result.get("account_email"), str) or len(result["account_email"]) > 320:
            raise ValueError("Invalid account association")
        self.credentials.update({
            "agent_id": result["agent_id"], "user_id": result["user_id"],
            "account_email": result["account_email"],
        })
        write_json(self.identity / "identity.json", self.credentials)
        self.state = "reconnecting"
        self.attempt = 0
        self.retry_at = 0

    def remove_identity(self, *, central_already_revoked: bool = False) -> None:
        if self.paired and not central_already_revoked:
            assert self.private_key is not None
            agent_id = self.credentials["agent_id"]
            signature = encode(self.private_key.sign(canonical("blueashreel-agent-revoke-v1", agent_id)))
            # This durable proof can only revoke. It cannot reconnect, rotate or
            # decrypt, and remains retryable after the private key is removed.
            write_json(self.identity / "revocation.json", {"agent_id": agent_id, "signature": signature})
        (self.identity / "identity.json").unlink(missing_ok=True)
        self.credentials = {}
        self.private_key = None
        write_json(self.control / "desired.json", {"enabled": False})
        self.state = "revoked" if central_already_revoked else "disabled"
        self.last_heartbeat = None

    async def retry_revocation(self) -> None:
        path = self.identity / "revocation.json"
        tombstone = read_json(path)
        if not tombstone or time.monotonic() < self.tombstone_retry_at:
            return
        try:
            result = await asyncio.to_thread(
                post_control, f"/api/agents/{tombstone['agent_id']}/revoke", {"signature": tombstone["signature"]}
            )
            if result.get("revoked") is not True:
                raise ValueError("Central revocation has not been confirmed")
            path.unlink(missing_ok=True)
            self.tombstone_attempt = 0
        except Exception:
            # No exception strings: network errors may contain request details.
            self.tombstone_retry_at = time.monotonic() + reconnect_delay(self.tombstone_attempt)
            self.tombstone_attempt += 1

    async def command(self, path: Path) -> None:
        command = read_json(path)
        try:
            await self.stop_connection()
            action = command.get("action")
            if action in {"unpair", "revoke"}:
                self.remove_identity()
                await self.retry_revocation()
            elif action == "pair":
                if not 0 <= time.time() - command.get("created_at", 0) <= 300:
                    raise ValueError("Pairing command expired")
                await self.pair(command)
            elif action == "reconnect" and self.paired:
                self.retry_at = 0
                self.attempt = 0
                self.state = "reconnecting"
            else:
                raise ValueError("Invalid control command")
        except Exception:
            self.state = "pairing_failed" if command.get("action") == "pair" else "connection_failed"
            write_json(self.control / "desired.json", {"enabled": False})
        finally:
            # Pairing codes never enter logs, status, the database or backups.
            path.unlink(missing_ok=True)
            self.publish()

    async def authenticate(self, socket: Any, role: str) -> None:
        raw = await asyncio.wait_for(socket.recv(), timeout=10)
        challenge = json.loads(raw)
        if (
            challenge.get("type") != "challenge" or challenge.get("protocol") != 1
            or challenge.get("role") != role or not isinstance(challenge.get("nonce"), str)
            or len(challenge["nonce"]) > 256 or challenge.get("expires_in") != 30
        ):
            raise ValueError("Invalid broker challenge")
        assert self.private_key is not None
        proof = self.private_key.sign(canonical(
            "blueashreel-agent-auth-v1", role, self.credentials["agent_id"], challenge["nonce"]
        ))
        await self.send(socket, {
            "type": "authenticate", "protocol": 1, "agent_id": self.credentials["agent_id"],
            "signature": encode(proof),
        })
        result = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
        if result.get("type") != "authenticated" or result.get("protocol") != 1:
            raise CredentialRejected()

    @staticmethod
    async def send(socket: Any, frame: dict[str, Any]) -> None:
        await asyncio.wait_for(socket.send(json.dumps(frame, separators=(",", ":"))), timeout=5)

    async def broker_loop(self, socket: Any) -> None:
        while True:
            await self.send(socket, {"type": "heartbeat", "protocol": 1, "version": self.version, "os": OS_CATEGORY})
            response = json.loads(await asyncio.wait_for(socket.recv(), timeout=30))
            if response.get("type") != "heartbeat_ack":
                raise ValueError("Invalid heartbeat response")
            self.last_heartbeat = dt.datetime.now(dt.UTC).isoformat()
            self.publish()
            await asyncio.sleep(20)

    async def relay_loop(self, socket: Any) -> None:
        sessions: dict[str, EncryptedSession] = {}
        next_heartbeat = 0.0
        try:
            while True:
                if time.monotonic() >= next_heartbeat:
                    await self.send(socket, {"type": "heartbeat", "protocol": 1})
                    next_heartbeat = time.monotonic() + 20
                for existing_sid, session in list(sessions.items()):
                    if session.expired():
                        del sessions[existing_sid]
                        await self.send(socket, {"type": "close", "sid": existing_sid})
                try:
                    frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=1))
                except TimeoutError:
                    continue
                if not isinstance(frame, dict):
                    raise ValueError("Invalid relay envelope")
                if frame.get("type") == "heartbeat_ack":
                    continue
                sid = frame.get("sid")
                if not isinstance(sid, str) or len(sid) > 36:
                    raise ValueError("Invalid session identifier")
                try:
                    if frame.get("type") == "offer":
                        if sid in sessions or len(sessions) >= MAX_SESSIONS:
                            raise ValueError("Session limit exceeded")
                        assert self.private_key is not None
                        session, reply = EncryptedSession.accept(frame, self.private_key, self.credentials["agent_id"])
                        sessions[sid] = session
                        await self.send(socket, reply)
                    elif frame.get("type") == "data" and sid in sessions:
                        reply = sessions[sid].respond(frame, name=self.credentials["name"], version=self.version)
                        await self.send(socket, reply)
                    elif frame.get("type") == "close":
                        sessions.pop(sid, None)
                    else:
                        raise ValueError("Unknown session")
                except Exception:
                    sessions.pop(sid, None)
                    await self.send(socket, {"type": "close", "sid": sid})
        finally:
            sessions.clear()

    async def connected(self) -> None:
        options: dict[str, Any] = {
            "ssl": tls_context(), "proxy": None, "max_size": MAX_FRAME, "max_queue": 8,
            "write_limit": 32768, "open_timeout": 10, "close_timeout": 2,
            "ping_interval": 20, "ping_timeout": 20, "compression": None,
        }
        try:
            async with connect(BROKER_URL, **options) as broker, connect(RELAY_URL, **options) as relay:
                await self.authenticate(broker, "broker")
                await self.authenticate(relay, "relay")
                self.state = "connected_through_relay"
                self.attempt = 0
                self.publish()
                async with asyncio.TaskGroup() as group:
                    group.create_task(self.broker_loop(broker))
                    group.create_task(self.relay_loop(relay))
        except* ConnectionClosed as errors:
            if errors.subgroup(
                lambda error: isinstance(error, ConnectionClosed)
                and error.rcvd is not None and error.rcvd.code in {4401, 4403}
            ):
                raise CredentialRejected() from None
            raise

    async def run(self) -> None:
        next_publish = 0.0
        # Crash between writing revocation proof and deleting the identity must
        # complete local destruction before any reconnection is considered.
        if (self.identity / "revocation.json").exists():
            self.remove_identity()
        try:
            while True:
                for command in sorted(self.control.glob("command-*.json"))[:4]:
                    await self.command(command)
                if self.connection and self.connection.done():
                    try:
                        self.connection.result()
                    except BaseExceptionGroup as errors:
                        if errors.subgroup(CredentialRejected):
                            self.remove_identity(central_already_revoked=True)
                    except CredentialRejected:
                        self.remove_identity(central_already_revoked=True)
                    except Exception:
                        self.state = "connection_failed"
                    self.connection = None
                    if self.enabled():
                        self.state = "reconnecting"
                    self.retry_at = time.monotonic() + reconnect_delay(self.attempt)
                    self.attempt += 1
                if not self.enabled():
                    await self.stop_connection()
                elif self.paired and self.connection is None and time.monotonic() >= self.retry_at:
                    self.state = "reconnecting"
                    self.connection = asyncio.create_task(self.connected())
                if time.monotonic() >= next_publish:
                    self.publish()
                    next_publish = time.monotonic() + 5
                    await self.retry_revocation()
                await asyncio.sleep(0.5)
        finally:
            await self.stop_connection()
            self.state = "agent_offline" if self.enabled() else "disabled"
            self.publish()


def main() -> None:
    parser = argparse.ArgumentParser(description="Isolated outbound diagnostic connector")
    parser.add_argument("--control-dir", type=Path, required=True)
    parser.add_argument("--identity-dir", type=Path, required=True)
    parser.add_argument("--product-config", type=Path, required=True)
    parser.add_argument("--endpoint-pins", type=Path)
    args = parser.parse_args()
    # Third-party socket/HTTP loggers must never emit frames, cookies or proofs.
    logging.disable(logging.CRITICAL)
    if args.endpoint_pins:
        pin_resolution(read_json(args.endpoint_pins)["allowed_ips"])
    product = read_json(args.product_config)
    connector = Connector(args.control_dir, args.identity_dir, product["version"])
    asyncio.run(connector.run())


if __name__ == "__main__":
    main()
