"""Browser handoff, explicit native consent, response binding and recovery regressions."""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.native_tray import Supervisor, command_action, connection_label
from app.remote.callback_server import CallbackListener
from app.remote.connector import BROKER_URL, RELAY_URL, Connector
from app.remote.local_auth import COOKIE, router
from app.remote.protocol import canonical, decode, encode, fingerprint, readable_fingerprint
from app.remote.storage import read_json, write_json

BASE = "http://127.0.0.1:18080"


@pytest.fixture
def local_agent(tmp_path):
    application = FastAPI()
    application.include_router(router)
    connector = Connector(tmp_path / "control", tmp_path / "identity", "0.1.0")
    connector.media = SimpleNamespace(config=SimpleNamespace(), bind=Mock())
    application.state.portal_connector = connector
    application.state.portal_pending = {}
    application.state.portal_status_sessions = {}
    with TestClient(application, base_url=BASE) as client:
        yield client, connector, application


def begin(client):
    response = client.get("/portal/start?purpose=pair", follow_redirects=False)
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.scheme == "https" and location.netloc == "blueashreel.com"
    assert location.path == "/agent/authorize" and location.query == ""
    return {key: values[0] for key, values in parse_qs(location.fragment).items()}


def callback_payload(query):
    return {"state": query["state"], "nonce": query["nonce"], "code": "A" * 26}


def test_double_click_refresh_and_browser_reopen_reuse_one_authorization(local_agent):
    client, connector, application = local_agent
    first = begin(client)
    assert begin(client) == first
    client.cookies.clear()
    assert begin(client) == first
    assert len(application.state.portal_pending) == 1
    assert connector.state == "waiting_portal_approval"
    assert client.cookies.get(COOKIE) == first["state"]
    short = read_json(connector.control / "status.json")["fingerprint_short"]
    assert short == readable_fingerprint(first["fingerprint"])


def test_retry_replaces_only_browser_bound_pending_request_and_rejects_previous_callback(local_agent):
    client, connector, application = local_agent
    first = begin(client)
    saved_key = (connector.identity / "identity.json").read_bytes()
    response = client.get("/portal/start?purpose=pair&retry=true", follow_redirects=False)
    values = parse_qs(urlsplit(response.headers["location"]).fragment)
    assert response.status_code == 303 and values["state"][0] != first["state"]
    assert len(application.state.portal_pending) == 1
    assert (connector.identity / "identity.json").read_bytes() == saved_key
    with patch("app.native_consent.request_confirmation", new=AsyncMock()) as consent:
        replay = client.post("/portal/callback", json=callback_payload(first), headers={"Origin": BASE})
        assert replay.status_code == 403
        consent.assert_not_called()


def test_retry_without_original_cookie_does_not_invalidate_another_pending_request(local_agent):
    client, _connector, application = local_agent
    first = begin(client)
    client.cookies.clear()
    response = client.get("/portal/start?purpose=pair&retry=true", follow_redirects=False)
    assert parse_qs(urlsplit(response.headers["location"]).fragment)["state"] == [first["state"]]
    assert len(application.state.portal_pending) == 1


def test_callback_failure_and_cancel_page_offers_local_secret_free_retry(local_agent):
    client, _connector, _application = local_agent
    page = client.get("/portal/callback")
    assert page.status_code == 200
    assert 'href="/portal/start?purpose=pair&amp;retry=true"' in page.text
    assert '>Retry Pairing</a>' in page.text
    assert page.text.index("history.replaceState") < page.text.index("fetch(")


@pytest.mark.parametrize("tamper", ["state", "nonce", "cookie", "origin", "callback_port", "expired"])
def test_browser_binding_tampering_never_requests_local_consent(local_agent, tamper):
    client, connector, application = local_agent
    query = begin(client)
    payload = callback_payload(query)
    headers = {"Origin": BASE}
    target = "/portal/callback"
    if tamper in {"state", "nonce"}:
        payload[tamper] = "wrong-binding"
    elif tamper == "cookie":
        client.cookies.clear()
    elif tamper == "origin":
        headers["Origin"] = "http://192.168.1.1:18080"
    elif tamper == "callback_port":
        target = "http://127.0.0.1:18081/portal/callback"
        headers["Origin"] = "http://127.0.0.1:18081"
    elif tamper == "expired":
        application.state.portal_pending[query["state"]]["expires_at"] = time.time() - 1
    with patch("app.native_consent.request_confirmation", new=AsyncMock()) as consent:
        assert client.post(target, json=payload, headers=headers).status_code == 403
        consent.assert_not_called()
    assert not connector.paired


def test_portal_cancel_consumes_pending_and_replay_fails_closed(local_agent):
    client, connector, application = local_agent
    query = begin(client)
    payload = {"state": query["state"], "nonce": query["nonce"], "error": "access_denied"}
    assert client.post("/portal/callback", json=payload, headers={"Origin": BASE}).status_code == 200
    assert connector.state == "pairing_cancelled" and not application.state.portal_pending
    assert client.post("/portal/callback", json=callback_payload(query), headers={"Origin": BASE}).status_code == 403
    assert begin(client)["state"] != query["state"]


def test_native_fingerprint_decline_never_redeems_code(local_agent):
    client, connector, _application = local_agent
    query = begin(client)
    with patch("app.native_consent.request_confirmation", new=AsyncMock(return_value=False)) as consent:
        response = client.post("/portal/callback", json=callback_payload(query), headers={"Origin": BASE})
    assert response.status_code == 403 and connector.state == "pairing_cancelled"
    assert readable_fingerprint(query["fingerprint"]) in consent.call_args.args[1]
    assert not list(connector.control.glob("command-*.json"))


def test_native_confirmation_expiry_cannot_queue_pairing(local_agent):
    client, connector, application = local_agent
    query = begin(client)
    value = application.state.portal_pending[query["state"]]

    async def expired_consent(*_args):
        value["expires_at"] = time.time() - 1
        return True

    with patch("app.native_consent.request_confirmation", side_effect=expired_consent):
        response = client.post("/portal/callback", json=callback_payload(query), headers={"Origin": BASE})
        assert response.status_code == 403
    assert connector.state == "pairing_expired" and not list(connector.control.glob("command-*.json"))


def test_status_after_revocation_clears_stale_local_authorization(local_agent):
    client, _connector, application = local_agent
    application.state.portal_status_sessions["stale"] = {"expires_at": time.time() + 100}
    assert client.get("/portal/status", follow_redirects=False).status_code == 303
    assert application.state.portal_status_sessions == {}


def bound_command():
    return {"code": "A" * 26, "name": "Synthetic", "code_verifier": "v" * 43, "state": "s" * 43,
            "nonce": "n" * 43, "callback": BASE + "/portal/callback", "local_confirmed": True}


def pairing_response(_path, payload):
    challenge = encode(hashlib.sha256(payload["code_verifier"].encode()).digest())
    fields = ("code", "public_key", "name", "os", "version", "state", "nonce")
    proof = canonical("blueashreel-pair-v2", *(payload[key] for key in fields), challenge, payload["callback"])
    Ed25519PublicKey.from_public_bytes(decode(payload["public_key"])).verify(decode(payload["signature"]), proof)
    return {"agent_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "account_email": "synthetic@example.test",
            "fingerprint": fingerprint(decode(payload["public_key"])), "broker_url": BROKER_URL,
            "relay_url": RELAY_URL, "protocol": 1, "state": payload["state"], "nonce": payload["nonce"],
            "callback": payload["callback"], "code_challenge": challenge}


@pytest.mark.parametrize("field", ["state", "nonce", "code_challenge", "callback", "fingerprint"])
def test_native_pairing_rejects_altered_response_before_persistence(local_agent, field):
    _client, connector, _application = local_agent

    def altered(path, payload):
        return {**pairing_response(path, payload), field: "altered"}

    with patch("app.remote.connector.post_control", side_effect=altered), pytest.raises(ValueError):
        asyncio.run(connector.pair(bound_command()))
    assert not connector.paired
    assert "agent_id" not in read_json(connector.identity / "identity.json")


@pytest.mark.parametrize("field", ["state", "nonce", "code_verifier", "callback", "local_confirmed"])
def test_native_pairing_requires_all_bindings_and_local_consent(local_agent, field):
    _client, connector, _application = local_agent
    command = bound_command()
    del command[field]
    with patch("app.remote.connector.post_control") as network, pytest.raises(ValueError):
        asyncio.run(connector.pair(command))
    network.assert_not_called()


def test_native_pairing_proof_and_identity_survive_restart(local_agent):
    _client, connector, _application = local_agent
    with patch("app.remote.connector.post_control", side_effect=pairing_response):
        asyncio.run(connector.pair(bound_command()))
    assert connector.paired
    restarted = Connector(connector.control, connector.identity, "0.1.0")
    assert restarted.paired and restarted.credentials == connector.credentials
    assert restarted.private_key.public_key() == connector.private_key.public_key()


def test_revocation_crash_marker_destroys_stale_credentials_before_reconnect(local_agent):
    _client, connector, _application = local_agent
    with patch("app.remote.connector.post_control", side_effect=pairing_response):
        asyncio.run(connector.pair(bound_command()))
    # Simulate termination after recording the irreversible Portal revocation
    # but before the private key file could be removed.
    write_json(connector.identity / "revoked.json", {"revoked": True})
    write_json(connector.control / "desired.json", {"enabled": True})
    restarted = Connector(connector.control, connector.identity, "0.1.0")
    assert restarted.state == "revoked"

    async def run_once():
        task = asyncio.create_task(restarted.run())
        await asyncio.sleep(0.01)
        assert restarted.state == "revoked" and not restarted.paired and not restarted.enabled()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with patch.object(restarted, "connected", new=AsyncMock()) as network:
        asyncio.run(run_once())
        network.assert_not_called()
    assert not (connector.identity / "identity.json").exists()


def test_waiting_pairing_expires_without_browser_callback(local_agent):
    client, connector, _application = local_agent
    begin(client)
    connector.pairing_expires_at = time.time() - 1
    write_json(connector.control / "desired.json", {"enabled": True})

    async def run_once():
        task = asyncio.create_task(connector.run())
        await asyncio.sleep(0.01)
        assert connector.state == "pairing_expired"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_once())


@pytest.mark.parametrize(("state", "label"), [
    ("unpaired", "Not paired"), ("waiting_portal_approval", "Waiting for Portal approval"),
    ("waiting_local_confirmation", "Waiting for local confirmation"), ("revoked", "Revoked"),
    ("pairing_expired", "Pairing expired"), ("pairing_cancelled", "Pairing cancelled"), ("pairing_failed", "Error"),
])
def test_tray_does_not_collapse_pairing_failure_or_progress_to_unpaired(state, label):
    assert connection_label({"state": state, "paired": False, "updated_at": 99}, healthy=True, now=100) == label


def test_random_callback_listener_binds_only_loopback_and_closes(local_agent):
    _client, _connector, application = local_agent

    async def exercise():
        listener = CallbackListener(application)
        await listener.start()
        application.state.portal_callback_origin = listener.origin
        assert listener.socket.getsockname()[0] == "127.0.0.1"
        assert 1024 <= listener.socket.getsockname()[1] <= 65535
        assert listener.origin != BASE
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(listener.origin + "/portal/start?purpose=pair")
            fragment = urlsplit(response.headers["location"]).fragment
            query = {key: values[0] for key, values in parse_qs(fragment).items()}
            assert query["callback"] == listener.origin + "/portal/callback"
            page = await client.get(query["callback"])
            assert page.headers["Cache-Control"] == "no-store"
            assert page.text.index("history.replaceState") < page.text.index("fetch(")
            assert (await client.get(listener.origin + "/api/v1/browse/media")).status_code == 404
        await listener.close()
        assert listener.socket.fileno() == -1

    asyncio.run(exercise())


@pytest.mark.parametrize("race", ["none", "completed_pairing", "revocation_proof", "replaced_key"])
def test_explicit_incomplete_discard_rechecks_after_drain_and_rejects_old_callback(tmp_path, race):
    connector = Connector(tmp_path / "control", tmp_path / "remote-identity", "0.1.0")
    application = FastAPI()
    application.include_router(router)
    application.state.portal_connector = connector
    application.state.portal_pending = {}
    application.state.portal_status_sessions = {}
    supervisor = object.__new__(Supervisor)
    supervisor.installation = SimpleNamespace(data_dir=tmp_path)
    retained = tmp_path / "database" / "catalog-sentinel"
    retained.parent.mkdir()
    retained.write_text("synthetic catalog and media must remain")
    events = []
    old_public = None
    surviving_record = None
    restarted = None
    with TestClient(application, base_url=BASE) as client:
        request = begin(client)
        old_public = connector.private_key.public_key()
        connector.state = "pairing_failed"
        connector.publish()

        def stop():
            nonlocal surviving_record
            events.append("stop")
            assert (connector.identity / "identity.json").exists()
            # Mirror native API shutdown, including a credential write that may
            # complete after the tray displayed its previous unpaired status.
            if race == "completed_pairing":
                connector.credentials.update(agent_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))
                write_json(connector.identity / "identity.json", connector.credentials)
            elif race == "revocation_proof":
                proof = {"agent_id": str(uuid.uuid4()), "signature": "proof"}
                write_json(connector.identity / "revocation.json", proof)
            elif race == "replaced_key":
                connector.remove_identity()
                connector.prepare_identity("Replacement")
            surviving_record = read_json(connector.identity / "identity.json")
            application.state.portal_pending.clear()
            application.state.portal_status_sessions.clear()

        def start():
            nonlocal restarted
            events.append("start")
            restarted = Connector(connector.control, connector.identity, "0.1.0")
            application.state.portal_connector = restarted

        with (
            patch.object(supervisor, "remote_status", return_value=read_json(connector.control / "status.json")),
            patch.object(supervisor, "publish"), patch.object(supervisor, "stop", side_effect=stop),
            patch.object(supervisor, "start", side_effect=start), patch("app.remote.connector.post_control") as network,
        ):
            assert supervisor.discard_incomplete_pairing(request["fingerprint"]) is (race == "none")
        network.assert_not_called()  # No Portal owner/record/revocation changes.
        assert events == ["stop", "start"]
        assert retained.read_text() == "synthetic catalog and media must remain"
        replay = client.post("/portal/callback", json=callback_payload(request), headers={"Origin": BASE})
        assert replay.status_code == 403
        if race == "none":
            assert not (connector.identity / "identity.json").exists()
            fresh = begin(client)
            assert fresh["fingerprint"] != request["fingerprint"] and fresh["state"] != request["state"]
            assert restarted.private_key.public_key() != old_public
        else:
            assert read_json(connector.identity / "identity.json") == surviving_record
            assert restarted.paired is (race == "completed_pairing")


@pytest.mark.parametrize("case", [
    "missing_fingerprint", "changed_fingerprint", "paired", "revocation", "portal", "local",
])
def test_incomplete_discard_never_interrupts_an_ineligible_agent(case):
    supervisor = object.__new__(Supervisor)
    status = {"fingerprint": "a" * 64, "paired": False, "state": "pairing_failed"}
    expected = status["fingerprint"]
    if case == "missing_fingerprint":
        expected = None
    elif case == "changed_fingerprint":
        expected = "b" * 64
    elif case == "paired":
        status["paired"] = True
    elif case == "revocation":
        status["central_revocation_pending"] = True
    elif case == "portal":
        status["state"] = "waiting_portal_approval"
    elif case == "local":
        status["state"] = "waiting_local_confirmation"
    with patch.object(supervisor, "remote_status", return_value=status), patch.object(supervisor, "stop") as stop:
        assert supervisor.discard_incomplete_pairing(expected) is False
        stop.assert_not_called()


def test_discard_command_uses_existing_expiry_and_runtime_binding_validation():
    command = {"action": "discard_incomplete", "created_at": 99, "expected_fingerprint": "a" * 64}
    assert command_action(command, runtime_id="current", pid=123, now=100) == "discard_incomplete"
    assert command_action(command, runtime_id="current", pid=123, now=130) is None
