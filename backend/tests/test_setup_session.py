from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import TestContext
from sqlalchemy import func, select

from app.models import BackgroundJob, User
from app.services import setup_session
from app.services.setup_session import (
    SETUP_SESSION_COOKIE_NAME,
    SETUP_SESSION_TTL_SECONDS,
    create_setup_session,
    valid_setup_csrf,
    valid_setup_session,
)


@pytest.fixture(autouse=True)
def native_test_mount_state_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.media_roots.read_only_enforced", lambda _path: None)


def test_setup_session_is_limited_cookie_bound_and_csrf_protected(context: TestContext) -> None:
    owner_payload = {"username": "owner", "password": "correct horse battery staple"}
    assert context.client.get("/api/v1/media-roots").status_code == 401
    assert context.client.post("/api/v1/setup/owner", json=owner_payload).status_code == 403

    started = context.client.post("/api/v1/setup/session")
    assert started.status_code == 200
    csrf = started.json()["csrf_token"]
    assert started.json()["expires_in"] == SETUP_SESSION_TTL_SECONDS
    cookies = started.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookies if value.startswith(f"{SETUP_SESSION_COOKIE_NAME}="))
    csrf_cookie = next(value for value in cookies if value.startswith("csrf_token="))
    assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie and "SameSite=strict" in csrf_cookie
    assert csrf not in started.cookies[SETUP_SESSION_COOKIE_NAME]

    roots = context.client.get("/api/v1/media-roots")
    assert roots.status_code == 200
    root = roots.json()["items"][0]
    assert root["internal_path"] is None
    selection = {"selection_id": root["selection_id"]}
    assert context.client.post("/api/v1/media-folders/browse", json=selection).status_code == 403
    assert context.client.post(
        "/api/v1/media-folders/browse", headers={"X-CSRF-Token": "wrong"}, json=selection
    ).status_code == 403
    browsed = context.client.post(
        "/api/v1/media-folders/browse", headers={"X-CSRF-Token": csrf}, json=selection
    )
    assert browsed.status_code == 200
    assert browsed.json()["current"]["internal_path"] is None
    assert context.client.get("/api/v1/auth/me").status_code == 401
    assert context.client.get("/api/v1/libraries").status_code == 401
    assert context.client.get("/api/v1/media-storage").status_code == 401
    assert context.client.post("/api/v1/setup/owner", json=owner_payload).status_code == 403


def test_setup_session_expires_and_rejects_tampering(context: TestContext, monkeypatch: pytest.MonkeyPatch) -> None:
    created = create_setup_session(context.config)
    assert valid_setup_session(created.token, context.config)
    assert valid_setup_csrf(created.token, created.csrf_token, context.config)
    assert not valid_setup_csrf(created.token, "different", context.config)
    assert not valid_setup_csrf(None, created.csrf_token, context.config)
    for token in (None, "", "invalid", "\u00f1", created.token + "x", created.token * 10):
        assert not valid_setup_session(token, context.config)
    now = time.time()
    monkeypatch.setattr(setup_session.time, "time", lambda: now + SETUP_SESSION_TTL_SECONDS + 1)
    assert not valid_setup_session(created.token, context.config)
    assert not valid_setup_csrf(created.token, created.csrf_token, context.config)


def test_completed_setup_revokes_all_setup_browse_capabilities(context: TestContext) -> None:
    csrf = context.begin_setup()
    old_setup_cookie = context.client.cookies[SETUP_SESSION_COOKIE_NAME]
    root = context.client.get("/api/v1/media-roots").json()["items"][0]
    completed = context.client.post(
        "/api/v1/setup/owner",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "owner",
            "password": "correct horse battery staple",
            "initial_library": {
                "name": "Chosen media",
                "library_type": "movies",
                "folder_ids": [root["selection_id"]],
            },
        },
    )
    assert completed.status_code == 201, completed.text
    assert SETUP_SESSION_COOKIE_NAME not in context.client.cookies
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0

    context.client.cookies.clear()
    context.client.cookies.set(SETUP_SESSION_COOKIE_NAME, old_setup_cookie)
    context.client.cookies.set("csrf_token", csrf)
    assert context.client.get("/api/v1/media-roots").status_code == 401
    assert context.client.post(
        "/api/v1/media-folders/browse",
        headers={"X-CSRF-Token": csrf},
        json={"selection_id": root["selection_id"]},
    ).status_code == 401
    assert context.client.post("/api/v1/setup/session").status_code == 409


def test_invalid_browsed_selection_cannot_partially_create_owner(context: TestContext) -> None:
    csrf = context.begin_setup()
    rejected = context.client.post(
        "/api/v1/setup/owner",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": "owner",
            "password": "correct horse battery staple",
            "initial_library": {"name": "Unsafe", "library_type": "other", "folder_ids": ["invalid"]},
        },
    )
    assert rejected.status_code == 422
    assert context.client.get("/api/v1/setup/status").json()["setup_required"] is True
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_setup_manual_entry_rejects_outside_paths_before_filesystem_access(
    context: TestContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    csrf = context.begin_setup()
    outside = context.media_root.parent / "outside-secret"
    outside.mkdir()
    outside_file = outside / "private-file.txt"
    outside_file.write_text("private test fixture", encoding="utf-8")
    missing = outside / "missing"
    denied_paths = {outside, outside_file, missing}
    original_resolve = Path.resolve

    def guarded_resolve(path: Path, strict: bool = False) -> Path:
        assert path not in denied_paths, "An out-of-root candidate must not be inspected"
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    messages = []
    for candidate in denied_paths:
        rejected = context.client.post(
            "/api/v1/media-folders/validate",
            headers={"X-CSRF-Token": csrf},
            json={"path": str(candidate)},
        )
        assert rejected.status_code == 422
        messages.append(rejected.json()["error"]["message"])
        assert str(candidate) not in rejected.text
    assert messages == ["Media directory is outside the configured media roots"] * 3
