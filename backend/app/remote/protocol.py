"""Blue Ash Reel v1. Application traffic is restricted to synthetic diagnostics."""
from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PROTOCOL = 1
MAX_FRAME = 16384
MAX_PLAINTEXT = 4096
MAX_SESSIONS = 4


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value: str, size: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_FRAME or "=" in value:
        raise ValueError("Invalid encoding")
    result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if encode(result) != value or (size is not None and len(result) != size):
        raise ValueError("Invalid encoding or key size")
    return result


def fingerprint(public: bytes) -> str:
    return hashlib.sha256(public).hexdigest()


def canonical(prefix: str, *values: str) -> bytes:
    if any("\n" in value or "\r" in value for value in values):
        raise ValueError("Invalid signed field")
    return "\n".join((prefix, *values)).encode("utf-8")


def diagnostic(request: dict[str, Any], *, name: str, version: str) -> dict[str, Any]:
    request_id = request.get("id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 64:
        raise ValueError("Invalid diagnostic identifier")
    if request.get("op") == "status" and set(request) == {"id", "op"}:
        result = {"name": name, "version": version, "health": "ok", "mode": "relay"}
    elif request.get("op") == "echo" and set(request) == {"id", "op", "value"}:
        value = request["value"]
        if not isinstance(value, str) or len(value.encode("utf-8")) > 1024:
            raise ValueError("Invalid synthetic echo value")
        result = {"echo": value}
    else:
        # Never dispatch paths, URLs, method names or media/library operations.
        return {"id": request_id, "ok": False, "error": "unsupported_operation"}
    return {"id": request_id, "ok": True, "result": result}


@dataclass
class EncryptedSession:
    sid: str
    digest: bytes
    inbound_key: bytes
    outbound_key: bytes
    created_at: float
    last_active: float
    receive_sequence: int = 0
    send_sequence: int = 0

    @classmethod
    def accept(
        cls, offer: dict[str, Any], identity: Ed25519PrivateKey, agent_id: str
    ) -> tuple[EncryptedSession, dict[str, Any]]:
        expected = {"type", "protocol", "sid", "ticket_id", "user_id", "session_id", "agent_id", "browser_key"}
        if set(offer) != expected or offer["type"] != "offer" or offer["protocol"] != PROTOCOL:
            raise ValueError("Invalid session offer")
        for field in ("sid", "ticket_id", "user_id", "session_id", "agent_id"):
            if str(uuid.UUID(offer[field])) != offer[field]:
                raise ValueError("Invalid session binding")
        if offer["sid"] != offer["ticket_id"] or offer["agent_id"] != agent_id:
            raise ValueError("Wrong Agent or ticket")
        browser_key = X25519PublicKey.from_public_bytes(decode(offer["browser_key"], 32))
        ephemeral = X25519PrivateKey.generate()
        agent_key = encode(ephemeral.public_key().public_bytes_raw())
        transcript = canonical(
            "blueashreel-e2e-v1", offer["ticket_id"], offer["user_id"], offer["session_id"],
            agent_id, offer["browser_key"], agent_key,
        )
        digest = hashlib.sha256(transcript).digest()
        material = HKDF(algorithm=hashes.SHA256(), length=64, salt=digest, info=b"blueashreel-e2e-v1").derive(
            ephemeral.exchange(browser_key)
        )
        now = time.monotonic()
        session = cls(offer["sid"], digest, material[:32], material[32:], now, now)
        return session, {
            "type": "accept", "sid": session.sid, "agent_key": agent_key,
            "signature": encode(identity.sign(transcript)),
        }

    def expired(self) -> bool:
        now = time.monotonic()
        return now - self.created_at > 120 or now - self.last_active > 30

    def respond(self, frame: dict[str, Any], *, name: str, version: str) -> dict[str, Any]:
        if set(frame) != {"type", "sid", "seq", "ciphertext"} or frame["type"] != "data":
            raise ValueError("Invalid encrypted frame")
        sequence = frame["seq"]
        if (
            self.expired() or frame["sid"] != self.sid or type(sequence) is not int
            or sequence != self.receive_sequence or sequence >= 2**32 or self.send_sequence >= 2**32
        ):
            raise ValueError("Expired or replayed encrypted frame")
        ciphertext = decode(frame["ciphertext"])
        if not 16 <= len(ciphertext) <= MAX_PLAINTEXT + 16:
            raise ValueError("Oversized diagnostic")
        seq = sequence.to_bytes(8, "big")
        plain = AESGCM(self.inbound_key).decrypt(b"\0" * 4 + seq, ciphertext, self.digest + b"\0" + seq)
        request = json.loads(plain)
        if not isinstance(request, dict):
            raise ValueError("Invalid diagnostic")
        response = diagnostic(request, name=name, version=version)
        outbound = json.dumps(response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(outbound) > MAX_PLAINTEXT:
            raise ValueError("Oversized response")
        seq = self.send_sequence.to_bytes(8, "big")
        encrypted = AESGCM(self.outbound_key).encrypt(b"\0" * 4 + seq, outbound, self.digest + b"\1" + seq)
        result = {"type": "data", "sid": self.sid, "seq": self.send_sequence, "ciphertext": encode(encrypted)}
        self.receive_sequence += 1
        self.send_sequence += 1
        self.last_active = time.monotonic()
        return result
