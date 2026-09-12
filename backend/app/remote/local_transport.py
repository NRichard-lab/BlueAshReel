"""Ticket-gated LAN WebSocket carrying the unchanged blueashreel-e2e-v1 protocol."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from collections import defaultdict, deque
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from websockets.asyncio.server import serve

from app.remote.protocol import EncryptedSession, decode
from app.remote.storage import read_json, write_json

MAX_LOCAL_PLAINTEXT = 2 * 1024 * 1024
MAX_LOCAL_FRAME = 3 * 1024 * 1024
MAX_LOCAL_SESSIONS = 8


class LocalTransport:
    def __init__(self, connector: Any) -> None:
        self.connector = connector
        self.server: Any = None
        stored = read_json(connector.identity / "local-ticket-replays.json").get("tickets", {})
        now = int(time.time())
        self.replays: dict[str, int] = (
            {key: value for key, value in stored.items()
             if isinstance(key, str) and type(value) is int and value > now}
            if isinstance(stored, dict) else {}
        )
        self.attempts: dict[str, deque[float]] = defaultdict(deque)
        self.active = 0
        self.sessions: dict[str, tuple[Any, dict[str, Any]]] = {}

    def verify_ticket(self, value: object, offer: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, str) or len(value) > 4096 or value.count(".") != 1:
            raise ValueError("Invalid ticket")
        encoded, signature = value.split(".")
        key = self.connector.credentials.get("local_ticket_public_key")
        if not key:
            raise ValueError("Ticket verification unavailable")
        Ed25519PublicKey.from_public_bytes(decode(key, 32)).verify(decode(signature, 64), encoded.encode())
        raw = decode(encoded)
        if len(raw) > 2048:
            raise ValueError("Invalid ticket")
        payload = json.loads(raw)
        expected = {
            "version", "jti", "agent_id", "user_id", "session_id", "client_key", "purpose",
            "role", "access_version", "iat", "exp", "authorization_expires_at",
        }
        now = int(time.time())
        if (
            not isinstance(payload, dict) or set(payload) != expected or payload["version"] != 1
            or payload["purpose"] != "local" or payload["agent_id"] != self.connector.credentials.get("agent_id")
            or payload["client_key"] != offer.get("browser_key") or payload["jti"] != offer.get("sid")
            or payload["jti"] != offer.get("ticket_id") or payload["user_id"] != offer.get("user_id")
            or payload["session_id"] != offer.get("session_id") or payload["role"] != offer.get("role")
            or type(payload["iat"]) is not int or type(payload["exp"]) is not int
            or type(payload["authorization_expires_at"]) is not int
            or not payload["iat"] <= now < payload["exp"] <= payload["iat"] + 60
            or not payload["exp"] <= payload["authorization_expires_at"] <= payload["iat"] + 900
            or payload["jti"] in self.replays
        ):
            raise ValueError("Invalid ticket binding")
        self.replays = {jti: expiry for jti, expiry in self.replays.items() if expiry > now}
        self.replays[payload["jti"]] = payload["exp"]
        if len(self.replays) > 4096:
            raise ValueError("Ticket replay capacity reached")
        write_json(self.connector.identity / "local-ticket-replays.json", {"tickets": self.replays})
        return payload

    def allow_source(self, source: str) -> bool:
        try:
            address = ipaddress.ip_address(source)
        except ValueError:
            return False
        if not (address.is_private or address.is_link_local):
            return False
        now = time.monotonic()
        attempts = self.attempts[source]
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 12:
            return False
        attempts.append(now)
        return True

    async def handle(self, socket: Any) -> None:
        source = str(socket.remote_address[0]) if socket.remote_address else ""
        if socket.request.path != "/ws/local" or not self.allow_source(source) or self.active >= MAX_LOCAL_SESSIONS:
            await socket.close(4403)
            return
        self.active += 1
        tasks: set[asyncio.Task[None]] = set()
        send_lock = asyncio.Lock()
        semaphore = asyncio.Semaphore(4)
        authorization: dict[str, Any] | None = None
        try:
            wrapper = json.loads(await asyncio.wait_for(socket.recv(), 10))
            if set(wrapper) != {"type", "ticket", "offer"} or wrapper["type"] != "local_offer":
                raise ValueError("Ticket required")
            offer = wrapper["offer"]
            payload = self.verify_ticket(wrapper["ticket"], offer)
            identity = self.connector.private_key
            if identity is None:
                raise ValueError("Agent is unpaired")
            session, reply = EncryptedSession.accept(
                offer, identity, self.connector.credentials["agent_id"], max_plaintext=MAX_LOCAL_PLAINTEXT
            )
            authorization = {
                "authorized": True, "agent_id": payload["agent_id"], "user_id": payload["user_id"],
                "session_id": payload["session_id"], "role": payload["role"],
                "access_version": payload["access_version"],
                "expires_at": payload["authorization_expires_at"], "transport": "local",
            }
            self.sessions[payload["jti"]] = (socket, authorization)
            await socket.send(json.dumps(reply, separators=(",", ":")))

            async def dispatch(request: dict[str, Any]) -> None:
                async with semaphore:
                    response = await self.connector.media.dispatch(request, authorization)
                    async with send_lock:
                        await socket.send(json.dumps(session.seal(response), separators=(",", ":")))

            async for raw in socket:
                frame = json.loads(raw)
                request = session.open(frame)
                if len(tasks) >= 4:
                    async with send_lock:
                        rejected = {"id": request.get("id"), "ok": False, "error": "rate_limited"}
                        await socket.send(json.dumps(session.seal(rejected), separators=(",", ":")))
                    continue
                task = asyncio.create_task(dispatch(request))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except Exception:
            await socket.close(4403)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if authorization:
                self.connector.media.discard_authorization(authorization)
                self.sessions.pop(payload["jti"], None)
            self.active -= 1

    def bindings(self) -> list[dict[str, Any]]:
        return [{"sid": sid, "user_id": auth["user_id"], "session_id": auth["session_id"],
                 "role": auth["role"], "access_version": auth["access_version"]}
                for sid, (_, auth) in self.sessions.items()]

    async def revoke(self, session_ids: list[str]) -> None:
        for sid in session_ids:
            item = self.sessions.get(sid)
            if item:
                socket, authorization = item
                authorization.update(expires_at=0, revoked=True)
                await asyncio.to_thread(self.connector.media.release, authorization)
                await socket.close(4403)

    async def start(self) -> None:
        config = self.connector.media.config
        self.server = await serve(
            self.handle, config.local_transport_address, config.local_transport_port,
            max_size=MAX_LOCAL_FRAME, max_queue=8, compression=None, ping_interval=20, ping_timeout=20,
        )

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
