from __future__ import annotations

import json
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from app.config import AppConfig
from app.remote.local_transport import LocalTransport
from app.remote.media import MAX_CHUNK, MAX_CHUNK_LOCAL, RemoteMedia
from app.remote.protocol import encode


def fixture() -> tuple[LocalTransport, Ed25519PrivateKey, dict, dict]:
    signing = Ed25519PrivateKey.generate()
    agent_id, user_id, session_id, jti = (str(uuid.uuid4()) for _ in range(4))
    client_key = encode(X25519PrivateKey.generate().public_key().public_bytes_raw())
    connector = SimpleNamespace(identity=Path(tempfile.mkdtemp()), credentials={
        "agent_id": agent_id,
        "local_ticket_public_key": encode(signing.public_key().public_bytes_raw()),
    })
    transport = LocalTransport(connector)
    offer = {
        "type": "offer", "protocol": 1, "sid": jti, "ticket_id": jti, "user_id": user_id,
        "session_id": session_id, "agent_id": agent_id, "browser_key": client_key,
        "role": "viewer", "mfa_verified": True,
    }
    now = int(time.time())
    payload = {
        "version": 1, "jti": jti, "agent_id": agent_id, "user_id": user_id,
        "session_id": session_id, "client_key": client_key, "purpose": "local", "role": "viewer",
        "access_version": 3, "iat": now, "exp": now + 60,
        "authorization_expires_at": now + 900,
    }
    return transport, signing, offer, payload


def ticket(signing: Ed25519PrivateKey, payload: dict) -> str:
    encoded = encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return encoded + "." + encode(signing.sign(encoded.encode()))


def test_local_ticket_is_bound_and_single_use() -> None:
    transport, signing, offer, payload = fixture()
    assert transport.verify_ticket(ticket(signing, payload), offer)["access_version"] == 3
    with pytest.raises(ValueError, match="binding"):
        transport.verify_ticket(ticket(signing, payload), offer)


@pytest.mark.parametrize("field,value", [
    ("purpose", "relay"), ("agent_id", str(uuid.uuid4())), ("client_key", encode(bytes(range(32)))),
    ("exp", 0),
])
def test_local_ticket_rejects_wrong_bindings(field: str, value: object) -> None:
    transport, signing, offer, payload = fixture()
    payload[field] = value
    with pytest.raises(ValueError):
        transport.verify_ticket(ticket(signing, payload), offer)


def test_local_ticket_rejects_invalid_signature() -> None:
    transport, _signing, offer, payload = fixture()
    with pytest.raises(InvalidSignature):
        transport.verify_ticket(ticket(Ed25519PrivateKey.generate(), payload), offer)


def test_local_bind_and_transport_chunk_limits() -> None:
    common = {"_env_file": None, "app_secret_key": "test-secret-key-is-at-least-thirty-two-characters"}
    assert AppConfig(**common, local_transport_address="192.168.1.5").local_transport_address == "192.168.1.5"
    for address in ("0.0.0.0", "8.8.8.8", "169.254.1.2", "::"):  # noqa: S104
        with pytest.raises(ValueError):
            AppConfig(**common, local_transport_address=address)
    assert RemoteMedia.chunk_limit({"transport": "relay"}) == MAX_CHUNK
    assert RemoteMedia.chunk_limit({"transport": "local"}) == MAX_CHUNK_LOCAL
