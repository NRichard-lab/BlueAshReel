"""Outbound, canonical-origin connector. Optional native media dispatch stays local."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import hashlib
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
from app.remote.protocol import (
    MAX_FRAME,
    MAX_SESSIONS,
    EncryptedSession,
    canonical,
    decode,
    encode,
    fingerprint,
    readable_fingerprint,
)
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
    if path not in {"/api/agents/pair", "/api/agent-authorizations/exchange"} and not re.fullmatch(
        r"/api/agents/[0-9a-f-]{36}/(?:revoke|challenge|session/validate|local-session/validate|updates)", path
    ):
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


def newer_version(candidate: str, current: str) -> bool:
    """SemVer precedence, including numeric prerelease parts; build data is ignored."""
    def parsed(value: str) -> tuple[Any, ...]:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError("Invalid software version")
        match = re.fullmatch(
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
            r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?", value)
        if not match:
            raise ValueError("Invalid software version")
        parts = (match[4] or "").split(".") if match[4] else []
        if any(part.isdigit() and len(part) > 1 and part[0] == "0" for part in parts):
            raise ValueError("Invalid prerelease version")
        prerelease = tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)
        return int(match[1]), int(match[2]), int(match[3]), not bool(parts), prerelease
    return parsed(candidate) > parsed(current)


class Connector:
    def __init__(self, control: Path, identity: Path, version: str, *, preprotected_native: bool = False,
                 media: Any = None) -> None:
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
        self.state = "revoked" if read_json(identity / "revoked.json").get("revoked") is True else "disabled"
        self.pairing_expires_at = 0.0
        self.last_heartbeat: str | None = None
        self.retry_at = 0.0
        self.attempt = 0
        self.tombstone_retry_at = 0.0
        self.tombstone_attempt = 0
        self.media = media
        self.next_update_check = 0.0
        self.update_available = False
        self.update_version: str | None = None
        # Integrated native mode does not use main()'s process-wide log disable.
        # Never let debug socket logging serialize authentication frames.
        logging.getLogger("websockets.client").setLevel(logging.CRITICAL + 1)
        if self.media and self.paired:
            self.media.bind(self.credentials["agent_id"], self.credentials["user_id"])

    def prepare_identity(self, name: str) -> dict[str, str]:
        if self.paired or (self.identity / "revocation.json").exists():
            raise ValueError("Existing pairing must be explicitly revoked")
        if not self.private_key:
            self.private_key = Ed25519PrivateKey.generate()
            self.credentials = {"secret": protect_secret(self.private_key.private_bytes_raw()), "name": name}
            write_json(self.identity / "identity.json", self.credentials)
        public = self.private_key.public_key().public_bytes_raw()
        return {"public_key": encode(public), "fingerprint": fingerprint(public), "name": self.credentials["name"]}

    @property
    def paired(self) -> bool:
        return bool(self.credentials.get("agent_id") and self.private_key)

    @property
    def broker_url(self) -> str:
        return PORTAL_ORIGIN.replace("https:", "wss:") + f"/ws/broker/{self.credentials['agent_id']}"

    @property
    def relay_url(self) -> str:
        return PORTAL_ORIGIN.replace("https:", "wss:") + f"/ws/relay/{self.credentials['agent_id']}/agent"

    def enabled(self) -> bool:
        return read_json(self.control / "desired.json").get("enabled") is True

    def publish(self) -> None:
        public = self.private_key.public_key().public_bytes_raw() if self.private_key else None
        write_json(self.control / "status.json", {
            "enabled": self.enabled(), "paired": self.paired, "state": self.state,
            "agent_id": self.credentials.get("agent_id"), "account_email": self.credentials.get("account_email"),
            "name": self.credentials.get("name"), "fingerprint": fingerprint(public) if public else None,
            "fingerprint_short": readable_fingerprint(fingerprint(public)) if public else None,
            "last_heartbeat": self.last_heartbeat, "updated_at": time.time(),
            "central_revocation_pending": (self.identity / "revocation.json").exists(),
            "remote_media_available": self.media is not None,
            "update_available": self.update_available, "update_version": self.update_version,
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
        if self.media is not None and command.get("local_confirmed") is not True:
            raise ValueError("Native local fingerprint confirmation required")
        binding: dict[str, str] = {}
        if self.media is not None or any(key in command for key in ("code_verifier", "state", "nonce", "callback")):
            for key in ("code_verifier", "state", "nonce"):
                value = command.get(key)
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value):
                    raise ValueError("Missing browser authorization binding")
                binding[key] = value
            callback = command.get("callback")
            if not isinstance(callback, str) or not re.fullmatch(
                r"http://127\.0\.0\.1:([1-9][0-9]{3,4})/portal/callback", callback
            ) or not 1024 <= int(callback.split(":")[2].split("/")[0]) <= 65535:
                raise ValueError("Invalid browser callback binding")
            binding["callback"] = callback
        self.prepare_identity(name)
        assert self.private_key is not None
        public = encode(self.private_key.public_key().public_bytes_raw())
        self.state = "pairing"
        self.publish()
        challenge = encode(hashlib.sha256(binding["code_verifier"].encode("ascii")).digest()) if binding else None
        transcript = canonical("blueashreel-pair-v1", code, public, name, OS_CATEGORY, self.version)
        if binding:
            assert challenge is not None
            transcript = canonical("blueashreel-pair-v2", code, public, name, OS_CATEGORY, self.version,
                                   binding["state"], binding["nonce"], challenge, binding["callback"])
        signature = encode(self.private_key.sign(transcript))
        result = await asyncio.to_thread(post_control, "/api/agents/pair", {
            "code": code, "public_key": public, "name": name, "os": OS_CATEGORY,
            "version": self.version, "signature": signature,
            **binding,
        })
        if binding and any(result.get(key) != value for key, value in {
            "state": binding["state"], "nonce": binding["nonce"],
            "code_challenge": challenge, "callback": binding["callback"],
        }.items()):
            raise ValueError("Pairing authorization binding rejected")
        received_id = str(uuid.UUID(result["agent_id"]))
        scoped_origin = PORTAL_ORIGIN.replace("https:", "wss:")
        if (
            result.get("protocol") != 1 or result.get("broker_url") not in {
                BROKER_URL, scoped_origin + f"/ws/broker/{received_id}"}
            or result.get("relay_url") not in {RELAY_URL, scoped_origin + f"/ws/relay/{received_id}/agent"}
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
        (self.identity / "revoked.json").unlink(missing_ok=True)
        if self.media:
            self.media.bind(result["agent_id"], result["user_id"])
        self.state = "reconnecting"
        self.pairing_expires_at = 0
        self.attempt = 0
        self.retry_at = 0

    def remove_identity(self, *, central_already_revoked: bool = False) -> None:
        if central_already_revoked:
            write_json(self.identity / "revoked.json", {"revoked": True})
        else:
            (self.identity / "revoked.json").unlink(missing_ok=True)
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
        self.state = "revoked" if central_already_revoked else "unpaired"
        self.pairing_expires_at = 0
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

    async def check_updates(self) -> None:
        if not self.paired or time.monotonic() < self.next_update_check:
            return
        # Reserve the interval before network work, including failure/reconnect.
        self.next_update_check = time.monotonic() + 3600
        try:
            agent_id = self.credentials["agent_id"]
            challenge = await asyncio.to_thread(
                post_control, f"/api/agents/{agent_id}/challenge", {"purpose": "update"})
            nonce = challenge["nonce"]
            assert self.private_key is not None
            signature = encode(self.private_key.sign(canonical(
                "blueashreel-agent-update-v1", agent_id, self.version, nonce)))
            result = await asyncio.to_thread(post_control, f"/api/agents/{agent_id}/updates", {
                "version": self.version, "nonce": nonce, "signature": signature})
            if result.get("available") is False and result.get("version") is None:
                self.update_available, self.update_version = False, None
            elif result.get("available") is True and isinstance(result.get("version"), str):
                available = newer_version(result["version"], self.version)
                self.update_available = available
                self.update_version = result["version"] if available else None
            else:
                return
            self.publish()
        except Exception:
            # Optional approved-version metadata never affects tunnel health.
            return

    async def update_loop(self) -> None:
        while True:
            await self.check_updates()
            await asyncio.sleep(60)

    async def relay_loop(self, socket: Any) -> None:
        sessions: dict[str, EncryptedSession] = {}
        queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        workers: dict[str, asyncio.Task[None]] = {}
        authorizations: dict[str, dict[str, Any]] = {}
        next_heartbeat = 0.0
        async def process(sid: str, authorization: dict[str, Any]) -> None:
            try:
                while sid in sessions:
                    request = await queues[sid].get()
                    reply = await self.media.dispatch(request, authorization)
                    if sid in sessions:
                        await self.send(socket, sessions[sid].seal(reply))
            except asyncio.CancelledError:
                raise
            except Exception:
                sessions.pop(sid, None)
                await self.send(socket, {"type": "close", "sid": sid})

        def discard(sid: str, *, revoked: bool = False) -> None:
            authorization = authorizations.pop(sid, None)
            if revoked and authorization:
                authorization.update(expires_at=0, revoked=True)
                asyncio.create_task(asyncio.to_thread(self.media.release, authorization))
            sessions.pop(sid, None)
            queues.pop(sid, None)
            task = workers.pop(sid, None)
            if task:
                task.cancel()
        try:
            while True:
                if time.monotonic() >= next_heartbeat:
                    await self.send(socket, {"type": "heartbeat", "protocol": 1})
                    next_heartbeat = time.monotonic() + 20
                for existing_sid, session in list(sessions.items()):
                    if session.expired():
                        discard(existing_sid)
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
                        authorization = await self.validate_offer(frame) if self.media else None
                        session, reply = EncryptedSession.accept(frame, self.private_key, self.credentials["agent_id"])
                        sessions[sid] = session
                        if authorization:
                            authorizations[sid] = authorization
                            queues[sid] = asyncio.Queue(maxsize=4)
                            workers[sid] = asyncio.create_task(process(sid, authorization))
                        await self.send(socket, reply)
                    elif frame.get("type") == "data" and sid in sessions:
                        if self.media:
                            queues[sid].put_nowait(sessions[sid].open(frame))
                        else:
                            reply = sessions[sid].respond(frame, name=self.credentials["name"], version=self.version)
                            await self.send(socket, reply)
                    elif frame.get("type") == "close":
                        discard(sid, revoked=frame.get("reason") == "revoked")
                    else:
                        raise ValueError("Unknown session")
                except Exception:
                    discard(sid)
                    await self.send(socket, {"type": "close", "sid": sid})
        finally:
            for sid in list(sessions):
                discard(sid)

    async def validate_offer(self, frame: dict[str, Any]) -> dict[str, Any]:
        agent_id = self.credentials["agent_id"]
        if frame.get("mfa_verified") is not True or frame.get("agent_id") != agent_id:
            raise ValueError("MFA-bound authorization required")
        challenge = await asyncio.to_thread(post_control, f"/api/agents/{agent_id}/challenge", {"purpose": "access"})
        nonce = challenge["nonce"]
        assert self.private_key is not None
        signature = encode(self.private_key.sign(canonical("blueashreel-session-validate-v1", agent_id,
            frame["sid"], frame["user_id"], frame["session_id"], frame["browser_key"], nonce)))
        payload = {key: frame[key] for key in ("sid", "user_id", "session_id", "browser_key")}
        result = await asyncio.to_thread(post_control, f"/api/agents/{agent_id}/session/validate",
                                        {**payload, "nonce": nonce, "signature": signature})
        if (result.get("authorized") is not True or result.get("agent_id") != agent_id
                or result.get("user_id") != frame["user_id"] or result.get("role") != frame["role"]
                or not time.time() < result.get("expires_at", 0) <= time.time() + 901):
            raise ValueError("Invalid Agent authorization")
        result["session_id"] = frame["session_id"]
        # Reject before handshake if no matching, locally approved grant exists.
        with self.media.factory() as db:
            self.media.principal(db, result)
        return result

    async def connected(self) -> None:
        options: dict[str, Any] = {
            "ssl": tls_context(), "proxy": None, "max_size": MAX_FRAME, "max_queue": 8,
            "write_limit": 32768, "open_timeout": 10, "close_timeout": 2,
            "ping_interval": 20, "ping_timeout": 20, "compression": None,
        }
        try:
            async with connect(self.broker_url, **options) as broker, connect(self.relay_url, **options) as relay:
                await self.authenticate(broker, "broker")
                await self.authenticate(relay, "relay")
                self.state = "connected_through_relay"
                self.attempt = 0
                self.publish()
                async with asyncio.TaskGroup() as group:
                    group.create_task(self.broker_loop(broker))
                    group.create_task(self.relay_loop(relay))
                    group.create_task(self.update_loop())
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
        elif read_json(self.identity / "revoked.json").get("revoked") is True:
            self.remove_identity(central_already_revoked=True)
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
                if self.state in {"waiting_portal_approval", "waiting_local_confirmation"} and (
                    self.pairing_expires_at <= time.time()
                ):
                    self.state = "pairing_expired"
                pairing_states = {"waiting_portal_approval", "waiting_local_confirmation", "pairing_expired",
                                  "pairing_cancelled", "pairing_failed", "revoked"}
                if not self.enabled():
                    await self.stop_connection()
                    if self.state not in pairing_states:
                        self.state = "disabled"
                elif not self.paired:
                    if self.state not in pairing_states:
                        self.state = "unpaired"
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
            for pending in self.control.glob("command-*.json"):
                if read_json(pending).get("action") == "pair":
                    pending.unlink(missing_ok=True)
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
