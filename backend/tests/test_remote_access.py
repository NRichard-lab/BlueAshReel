from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from conftest import TestContext
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from test_household_catalog import create_viewer, login
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from app.models import AuditEvent
from app.remote.connector import BROKER_URL, RELAY_URL, Connector, newer_version, post_control, reconnect_delay
from app.remote.control import queue_action, snapshot
from app.remote.protocol import MAX_PLAINTEXT, EncryptedSession, decode, diagnostic, encode, fingerprint
from app.remote.storage import protect_directory, protect_secret, read_json, unprotect_secret, write_json


def browser_session() -> tuple[EncryptedSession, bytes, bytes, dict, bytes, Ed25519PrivateKey]:
    identity = Ed25519PrivateKey.generate()
    browser = X25519PrivateKey.generate()
    offer = {
        "type": "offer", "protocol": 1, "sid": str(uuid.uuid4()), "ticket_id": "",
        "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "agent_id": str(uuid.uuid4()),
        "browser_key": encode(browser.public_key().public_bytes_raw()),
    }
    offer["ticket_id"] = offer["sid"]
    session, acceptance = EncryptedSession.accept(offer, identity, offer["agent_id"])
    transcript = (
        f"blueashreel-e2e-v1\n{offer['ticket_id']}\n{offer['user_id']}\n{offer['session_id']}\n"
        f"{offer['agent_id']}\n{offer['browser_key']}\n{acceptance['agent_key']}"
    ).encode()
    identity.public_key().verify(decode(acceptance["signature"]), transcript)
    digest = hashlib.sha256(transcript).digest()
    keys = HKDF(algorithm=hashes.SHA256(), length=64, salt=digest, info=b"blueashreel-e2e-v1").derive(
        browser.exchange(X25519PublicKey.from_public_bytes(decode(acceptance["agent_key"])))
    )
    return session, keys[:32], keys[32:], acceptance, transcript, identity


def encrypted_request(session: EncryptedSession, key: bytes, payload: dict, seq: int = 0) -> dict:
    nonce = b"\0" * 4 + seq.to_bytes(8, "big")
    aad = session.digest + b"\0" + seq.to_bytes(8, "big")
    return {"type": "data", "sid": session.sid, "seq": seq,
            "ciphertext": encode(AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), aad))}


@pytest.mark.parametrize("payload", [{"id": "a", "op": "echo", "value": "synthetic diagnostic"},
                                   {"id": "b", "op": "status"}])
def test_browser_agent_encrypted_diagnostics(payload: dict) -> None:
    session, incoming, outgoing, _, _, _ = browser_session()
    frame = encrypted_request(session, incoming, payload)
    response = session.respond(frame, name="Synthetic Agent", version="0.1.0")
    plain = AESGCM(outgoing).decrypt(b"\0" * 12, decode(response["ciphertext"]), session.digest + b"\1" + b"\0" * 8)
    assert json.loads(plain)["ok"] is True
    assert "synthetic diagnostic" not in json.dumps(frame)
    assert "Synthetic Agent" not in json.dumps(response)
    assert session.receive_sequence == session.send_sequence == 1
    with pytest.raises(ValueError, match="replayed"):
        session.respond(frame, name="Synthetic Agent", version="0.1.0")


def test_tamper_wrong_key_and_wrong_direction_fail_authentication() -> None:
    session, incoming, outgoing, _, _, _ = browser_session()
    frame = encrypted_request(session, incoming, {"id": "a", "op": "status"})
    cipher = bytearray(decode(frame["ciphertext"]))
    cipher[0] ^= 1
    with pytest.raises(InvalidTag):
        session.respond({**frame, "ciphertext": encode(bytes(cipher))}, name="Synthetic", version="0.1.0")
    wrong_direction = encrypted_request(session, outgoing, {"id": "a", "op": "status"})
    with pytest.raises(InvalidTag):
        session.respond(wrong_direction, name="Synthetic", version="0.1.0")
    assert session.receive_sequence == 0


def test_signature_binds_user_ticket_session_and_agent() -> None:
    _, _, _, acceptance, transcript, identity = browser_session()
    signature = decode(acceptance["signature"])
    for index in (1, 2, 3, 4):
        fields = transcript.split(b"\n")
        fields[index] = str(uuid.uuid4()).encode()
        with pytest.raises(InvalidSignature):
            identity.public_key().verify(signature, b"\n".join(fields))
    with pytest.raises(InvalidSignature):
        Ed25519PrivateKey.generate().public_key().verify(signature, transcript)


@pytest.mark.parametrize("sequence", [-1, True, 1, 2**32])
def test_sequence_gaps_invalid_types_and_overflow_rejected(sequence) -> None:
    session, incoming, _, _, _, _ = browser_session()
    frame = encrypted_request(session, incoming, {"id": "a", "op": "status"})
    with pytest.raises(ValueError):
        session.respond({**frame, "seq": sequence}, name="Synthetic", version="0.1.0")


@pytest.mark.parametrize("mutation", ["wrong_agent", "extra_path", "null_key"])
def test_invalid_offers_rejected(mutation: str) -> None:
    identity = Ed25519PrivateKey.generate()
    agent_id = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    offer = {"type": "offer", "protocol": 1, "sid": sid, "ticket_id": sid,
             "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()), "agent_id": agent_id,
             "browser_key": encode(X25519PrivateKey.generate().public_key().public_bytes_raw())}
    if mutation == "wrong_agent":
        offer["agent_id"] = str(uuid.uuid4())
    elif mutation == "extra_path":
        offer["path"] = "/library"
    else:
        offer["browser_key"] = encode(b"\0" * 32)
    with pytest.raises(ValueError):
        EncryptedSession.accept(offer, identity, agent_id)


@pytest.mark.parametrize("op", ["library", "browse", "read_file", "search", "stream", "playback", "http"])
def test_remote_application_allowlist_rejects_media_and_paths(op: str) -> None:
    assert diagnostic({"id": "a", "op": op, "path": "/media/private"}, name="Synthetic", version="0.1.0") == {
        "id": "a", "ok": False, "error": "unsupported_operation"
    }
    assert diagnostic({"id": "a", "op": "status", "path": "/media"}, name="Synthetic", version="0.1.0")["ok"] is False


def test_frames_are_bounded_and_sessions_expire() -> None:
    session, incoming, _, _, _, _ = browser_session()
    with pytest.raises(ValueError, match="Oversized"):
        session.respond(
            {"type": "data", "sid": session.sid, "seq": 0, "ciphertext": encode(b"a" * (MAX_PLAINTEXT + 17))},
            name="Synthetic", version="0.1.0")
    session.last_active -= 181
    with pytest.raises(ValueError, match="Expired"):
        session.respond(encrypted_request(session, incoming, {"id": "a", "op": "status"}),
                        name="Synthetic", version="0.1.0")


def test_remote_default_and_no_auth(context: TestContext) -> None:
    assert context.config.remote_control_dir is None
    assert context.client.get("/api/v1/remote-access").status_code == 401


@pytest.mark.parametrize("role", ["Viewer", "Administrator"])
def test_remote_settings_owner_only(owner_context: tuple[TestContext, str], role: str) -> None:
    context, csrf = owner_context
    create_viewer(context, csrf, [], "restricted", role)
    restricted_csrf = login(context, "restricted")
    assert context.client.get("/api/v1/remote-access").status_code == 403
    assert context.client.post(
        "/api/v1/remote-access/revoke", headers={"X-CSRF-Token": restricted_csrf}
    ).status_code == 403


@pytest.mark.parametrize("action", ["pair", "unpair", "reconnect", "revoke"])
def test_remote_mutations_require_owner_csrf(owner_context: tuple[TestContext, str], action: str) -> None:
    context, _ = owner_context
    response = context.client.post(f"/api/v1/remote-access/{action}", json={})
    assert response.status_code == 403


def test_pairing_requires_explicit_consent_and_does_not_audit_code(owner_context: tuple[TestContext, str]) -> None:
    context, csrf = owner_context
    directory = context.config.app_data_dir.parent / "remote-control"
    context.config.remote_control_dir = directory
    write_json(directory / "status.json", {"updated_at": time.time()})
    headers = {"X-CSRF-Token": csrf}
    body = {"code": "A" * 26, "name": "Synthetic"}
    assert context.client.post("/api/v1/remote-access/pair", headers=headers, json=body).status_code == 422
    response = context.client.post("/api/v1/remote-access/pair", headers=headers, json={**body, "confirm_enable": True})
    assert response.status_code == 202
    assert response.json()["enabled"] is True
    assert "A" * 26 not in response.text
    with context.session_factory() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "remote.pair_requested")).all()
        assert len(events) == 1 and events[0].details == {}
    assert len(list(directory.glob("command-*.json"))) == 1
    with pytest.raises(ValueError, match="pending"):
        queue_action(directory, "pair", body)


def test_connector_never_calls_network_while_disabled(tmp_path: Path) -> None:
    connector = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0")

    async def run_briefly() -> None:
        task = asyncio.create_task(connector.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch("app.remote.connector.post_control", side_effect=AssertionError("unexpected network")):
        asyncio.run(run_briefly())
    assert snapshot(tmp_path / "control")["enabled"] is False
    assert not (tmp_path / "identity" / "identity.json").exists()


def test_identity_protected_atomic_persistence(tmp_path: Path) -> None:
    directory = tmp_path / "private"
    protect_directory(directory)
    secret = Ed25519PrivateKey.generate().private_bytes_raw()
    protected = protect_secret(secret)
    write_json(directory / "identity.json", {"secret": protected})
    assert unprotect_secret(read_json(directory / "identity.json")["secret"]) == secret
    assert not list(directory.glob(".pending-*"))
    if os.name == "nt":
        assert protected.startswith("dpapi-user:") and encode(secret) not in protected
    else:
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "identity.json").stat().st_mode & 0o777 == 0o600


def test_pair_proof_persistence_restart_and_offline_unpair(tmp_path: Path) -> None:
    connector = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0")
    agent_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())

    def pair_response(path: str, payload: dict) -> dict:
        assert path == "/api/agents/pair"
        assert set(payload) == {"code", "public_key", "name", "os", "version", "signature"}
        proof = "\n".join(["blueashreel-pair-v1", *(payload[field] for field in
                          ("code", "public_key", "name", "os", "version"))]).encode()
        Ed25519PublicKey.from_public_bytes(decode(payload["public_key"])).verify(decode(payload["signature"]), proof)
        return {"agent_id": agent_id, "user_id": user_id, "account_email": "synthetic@example.test",
                "fingerprint": fingerprint(decode(payload["public_key"])), "broker_url": BROKER_URL,
                "relay_url": RELAY_URL, "protocol": 1}

    with patch("app.remote.connector.post_control", side_effect=pair_response):
        asyncio.run(connector.pair({"code": "A" * 26, "name": "Synthetic"}))
    assert connector.private_key is not None
    public = connector.private_key.public_key()
    restarted = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0")
    assert restarted.paired and restarted.private_key.public_key() == public
    restarted.remove_identity()
    assert not restarted.paired and not (tmp_path / "identity" / "identity.json").exists()
    tombstone = read_json(tmp_path / "identity" / "revocation.json")
    public.verify(decode(tombstone["signature"]), f"blueashreel-agent-revoke-v1\n{agent_id}".encode())
    with patch("app.remote.connector.post_control", side_effect=ConnectionError("offline")):
        asyncio.run(restarted.retry_revocation())
    restarted.publish()
    assert snapshot(tmp_path / "control")["central_revocation_pending"] is True
    assert snapshot(tmp_path / "control")["paired"] is False
    restarted.tombstone_retry_at = 0
    with patch("app.remote.connector.post_control", return_value={"revoked": True}):
        asyncio.run(restarted.retry_revocation())
    assert not (tmp_path / "identity" / "revocation.json").exists()


def test_fixed_origin_allowlist_and_bounded_reconnect() -> None:
    for url in ("https://attacker.test/api", "/api/library", "/api/agents/../../secrets"):
        with pytest.raises(ValueError):
            post_control(url, {})
    assert all(0.75 <= reconnect_delay(attempt) <= 60 for attempt in range(100))


def test_relay_application_heartbeats_continue_without_browser_sessions(tmp_path: Path) -> None:
    connector = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0")
    messages: list[dict] = []
    elapsed = 0.0

    class IdleSocket:
        async def send(self, frame: str) -> None:
            messages.append(json.loads(frame))

        async def recv(self) -> str:
            nonlocal elapsed
            elapsed += 21
            if elapsed > 90:
                raise asyncio.CancelledError
            return '{"type":"heartbeat_ack"}'

    with (
        patch("app.remote.connector.time.monotonic", side_effect=lambda: elapsed),
        pytest.raises(asyncio.CancelledError),
    ):
        asyncio.run(connector.relay_loop(IdleSocket()))
    assert len(messages) >= 4
    assert all(frame == {"type": "heartbeat", "protocol": 1} for frame in messages)


def test_transient_socket_policy_close_does_not_destroy_local_identity(tmp_path: Path) -> None:
    connector = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0")
    connector.private_key = Ed25519PrivateKey.generate()
    connector.credentials = {"agent_id": str(uuid.uuid4()), "name": "Synthetic", "secret":
                             protect_secret(connector.private_key.private_bytes_raw())}
    write_json(connector.identity / "identity.json", connector.credentials)
    write_json(connector.control / "desired.json", {"enabled": True})

    async def policy_closed() -> None:
        raise ExceptionGroup("transient policy", [ConnectionClosedError(Close(1008, ""), Close(1008, ""), True)])

    async def run_once() -> None:
        connector.connection = asyncio.create_task(policy_closed())
        await asyncio.sleep(0)
        with patch.object(connector, "connected", new=AsyncMock()):
            running = asyncio.create_task(connector.run())
            await asyncio.sleep(0.05)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running

    asyncio.run(run_once())
    assert connector.paired and connector.enabled()
    assert (connector.identity / "identity.json").exists()
    assert not (connector.identity / "revocation.json").exists()


def test_connector_import_and_container_data_network_boundaries() -> None:
    root = Path(__file__).parents[2]
    modules = root / "backend/app/remote"
    for module in modules.glob("*.py"):
        if module.name in {"media.py", "local_auth.py"}:
            continue  # Explicit native dispatch only; isolated container connector never imports these.
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
        assert not any(item.startswith(("app.database", "app.models", "app.services", "app.config", "app.api"))
                       for item in imports)
    compose = yaml.safe_load((root / "compose.remote.yml").read_text())
    service = compose["services"]["remote-connector"]
    assert service["volumes"] == ["remote_control:/remote/control", "remote_identity:/remote/identity"]
    assert service["networks"] == ["remote_egress"] and not service.get("ports")
    assert service["read_only"] and service["cap_drop"] == ["ALL"]
    assert "network_mode" not in service and "privileged" not in service
    source = (root / "docker/remote-connector-entrypoint.sh").read_text()
    assert source.index("iptables -P OUTPUT DROP") < source.index("portal_ips=")
    assert '--dport 443' in source and '--bounding-set=-all' in source
    assert 'iptables -D OUTPUT -o lo -j ACCEPT' in source
    image = (root / "docker/remote-connector.Dockerfile").read_text()
    assert "COPY backend/app/remote/" in image and "COPY backend/ " not in image


@pytest.mark.parametrize(("candidate", "current", "expected"), [
    ("0.1.0-development.10", "0.1.0-development.9", True),
    ("0.1.0-development.4", "0.1.0-development.5", False),
    ("0.1.0", "0.1.0-development.5", True),
    ("0.1.0-development.6", "0.1.0", False),
    ("0.1.0+build.2", "0.1.0+build.1", False),
    ("0.1.0-development.5", "0.1.0-development.5", False),
    ("0.2.0-development.1", "0.1.0", True),
])
def test_approved_update_versions_use_semantic_precedence(candidate, current, expected):
    assert newer_version(candidate, current) is expected


@pytest.mark.parametrize("value", ["latest", "0.1.0-development.05", "00.1.0", "0.1.0\n", "0.1.0" + "x" * 40])
def test_invalid_published_update_version_is_rejected(value):
    with pytest.raises(ValueError):
        newer_version(value, "0.1.0")


def test_signed_update_check_is_hourly_and_optional(tmp_path):
    connector = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0-development.5")
    connector.prepare_identity("Synthetic")
    agent_id = str(uuid.uuid4())
    connector.credentials["agent_id"] = agent_id
    connector.state = "connected_through_relay"

    def response(path, payload):
        if path.endswith("/challenge"):
            assert payload == {"purpose": "update"}
            return {"nonce": "one-use-update-nonce"}
        assert path == f"/api/agents/{agent_id}/updates"
        assert set(payload) == {"version", "nonce", "signature"}
        transcript = f"blueashreel-agent-update-v1\n{agent_id}\n0.1.0-development.5\none-use-update-nonce".encode()
        connector.private_key.public_key().verify(decode(payload["signature"]), transcript)
        return {"available": True, "version": "0.1.0-development.6"}

    with patch("app.remote.connector.post_control", side_effect=response) as network:
        asyncio.run(connector.check_updates())
        asyncio.run(connector.check_updates())
        assert network.call_count == 2
    assert connector.update_available and connector.update_version == "0.1.0-development.6"
    assert read_json(connector.control / "status.json")["update_available"]
    connector.next_update_check = 0
    with patch("app.remote.connector.post_control", side_effect=ConnectionError("Unavailable")) as network:
        asyncio.run(connector.check_updates())
        asyncio.run(connector.check_updates())
        assert network.call_count == 1
    assert connector.state == "connected_through_relay"
    assert connector.update_available


def test_remote_atomic_status_write_retries_transient_windows_share_denial(tmp_path):
    original = os.replace
    attempts = 0

    def replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("Synthetic temporary sharing denial")
        return original(source, target)

    with patch("app.remote.storage.os.replace", side_effect=replace):
        write_json(tmp_path / "status.json", {"state": "connected_through_relay"})
    assert attempts == 3
    assert read_json(tmp_path / "status.json")["state"] == "connected_through_relay"
    assert not list(tmp_path.glob(".pending-*"))
