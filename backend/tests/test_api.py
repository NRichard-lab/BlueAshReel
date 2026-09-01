from __future__ import annotations

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import AppConfig, ProductConfig, get_product_config
from app.models import AuditEvent, User, UserSession
from app.services.rate_limit import login_rate_limiter


def test_health_version_and_public_config_do_not_expose_sensitive_state(context) -> None:
    live = context.client.get("/api/v1/health/live")
    ready = context.client.get("/api/v1/health/ready")
    version = context.client.get("/api/v1/version")
    public = context.client.get("/api/v1/config/public")

    assert live.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
    assert ready.json()["checks"]["database"] == "ok"
    assert ready.json()["checks"]["ffmpeg"] == "unavailable"
    assert ready.json()["checks"]["application_data"] == "ok"
    assert ready.json()["checks"]["temporary_storage"] == "ok"
    assert ready.json()["checks"]["artwork_storage"] == "ok"
    assert version.json()["version"] == "0.1.0"
    assert public.json()["api_prefix"] == "/api/v1"
    combined = live.text + ready.text + version.text + public.text
    assert str(context.media_root) not in combined
    assert "test-secret" not in combined


def test_ready_requires_database_binaries_and_storage(context, monkeypatch) -> None:
    monkeypatch.setattr("app.api.router.ffprobe_available", lambda _path: True)
    monkeypatch.setattr("app.api.router.ffmpeg_available", lambda _path: True)
    ready = context.client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert set(ready.json()["checks"].values()) == {"ok"}


def test_owner_setup_is_one_time_and_session_secrets_are_hashed(context) -> None:
    status = context.client.get("/api/v1/setup/status")
    assert status.json()["setup_required"] is True
    csrf, cookies = context.setup_owner()

    assert cookies["media_session"]
    assert cookies["csrf_token"] == csrf
    repeated = context.client.post(
        "/api/v1/setup/owner",
        json={"username": "other", "password": "another sufficiently long password"},
    )
    assert repeated.status_code == 409
    assert context.client.get("/api/v1/setup/status").json()["setup_required"] is False

    with context.session_factory() as db:
        user = db.scalar(select(User))
        session = db.scalar(select(UserSession))
        assert user is not None and user.password_hash.startswith("$argon2id$")
        assert session is not None
        expected = hmac.new(
            context.config.app_secret_key.encode(),
            cookies["media_session"].encode(),
            hashlib.sha256,
        ).hexdigest()
        assert session.token_hash == expected
        assert cookies["media_session"] not in session.token_hash
        assert csrf not in session.csrf_hash


def test_authentication_csrf_logout_and_generic_failures(context) -> None:
    csrf, _ = context.setup_owner()
    assert context.client.get("/api/v1/auth/me").status_code == 200

    rejected = context.client.patch("/api/v1/settings", json={"scan_extensions": ["mkv"]})
    assert rejected.status_code == 403
    accepted = context.client.patch(
        "/api/v1/settings",
        headers={"X-CSRF-Token": csrf},
        json={"scan_extensions": ["mkv", ".mp4"]},
    )
    assert accepted.status_code == 200
    assert accepted.json()["scan_extensions"] == [".mkv", ".mp4"]

    logout = context.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert context.client.get("/api/v1/auth/me").status_code == 401
    failed = context.client.post("/api/v1/auth/login", json={"username": "missing", "password": "not the password"})
    assert failed.status_code == 401
    assert "Invalid username or password" in failed.text
    login = context.client.post(
        "/api/v1/auth/login",
        json={"username": "OWNER", "password": "correct horse battery staple"},
    )
    assert login.status_code == 200
    assert login.cookies["csrf_token"] == login.json()["csrf_token"]
    cookie_headers = login.headers.get_list("set-cookie")
    session_cookie = next(value for value in cookie_headers if value.startswith("media_session="))
    csrf_cookie = next(value for value in cookie_headers if value.startswith("csrf_token="))
    assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie and "SameSite=strict" in csrf_cookie


def test_login_throttles_repeated_failures_without_exposing_identifier(context) -> None:
    context.setup_owner()
    for _ in range(5):
        failed = context.client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong password"})
        assert failed.status_code == 401
    throttled = context.client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong password"})
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1
    assert "owner" not in throttled.text.casefold()


def test_login_throttles_rotating_usernames_by_transport_host_and_ignores_xff(context) -> None:
    context.setup_owner()
    supplied_identifiers = ["testclient", "an-entirely-new-name"]
    for index in range(5):
        username = f"rotating-name-{index}"
        forwarded_host = f"203.0.113.{index + 1}"
        supplied_identifiers.extend((username, forwarded_host))
        failed = context.client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": forwarded_host},
            json={"username": username, "password": "wrong password"},
        )
        assert failed.status_code == 401

    throttled = context.client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "198.51.100.250"},
        json={"username": "an-entirely-new-name", "password": "wrong password"},
    )
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1

    # The bounded process-local cache retains only keyed SHA-256 digests, never
    # the raw transport host, forwarded host, or attempted username.
    cached_keys = tuple(login_rate_limiter._states)  # noqa: SLF001
    assert cached_keys
    assert all(len(key) == 64 and set(key) <= set("0123456789abcdef") for key in cached_keys)
    serialized_keys = " ".join(cached_keys)
    assert all(identifier.casefold() not in serialized_keys for identifier in supplied_identifiers)


def test_library_paths_pagination_and_audit(owner_context, tmp_path: Path) -> None:
    context, csrf = owner_context
    allowed = context.media_root / "Movies"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    headers = {"X-CSRF-Token": csrf}

    traversal = context.client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Unsafe", "library_type": "movies", "paths": [str(allowed / ".." / "Movies")]},
    )
    assert traversal.status_code == 422
    out_of_root = context.client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Outside", "library_type": "movies", "paths": [str(outside)]},
    )
    assert out_of_root.status_code == 422

    created_ids = []
    for index in range(103):
        response = context.client.post(
            "/api/v1/libraries",
            headers=headers,
            json={
                "name": f"Library {index:03d}",
                "library_type": "other",
                "paths": [str(allowed)] if index == 0 else [],
            },
        )
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["id"])
    page = context.client.get("/api/v1/libraries?page=2&page_size=10")
    assert page.json()["total"] == 103
    assert len(page.json()["items"]) == 10
    assert context.client.get(f"/api/v1/libraries/{created_ids[-1]}").status_code == 200

    with context.session_factory() as db:
        assert (db.scalar(select(func.count()).select_from(AuditEvent)) or 0) >= 104


def test_privacy_defaults_and_pagination_bounds(owner_context) -> None:
    context, csrf = owner_context
    privacy = context.client.get("/api/v1/privacy")
    assert privacy.json() == {
        "local_only": True,
        "telemetry_enabled": False,
        "runtime_outbound_allowed": False,
        "integrations": {"metadata": False, "artwork": False, "portal": False, "telemetry": False},
    }
    latent_enable = context.client.patch(
        "/api/v1/privacy",
        headers={"X-CSRF-Token": csrf},
        json={"integrations": {"metadata": True}},
    )
    assert latent_enable.status_code == 409
    assert context.client.get("/api/v1/privacy").json()["integrations"]["metadata"] is False
    assert context.client.get("/api/v1/media?page_size=101").status_code == 422


def test_whitespace_names_are_rejected(context) -> None:
    response = context.client.post(
        "/api/v1/setup/owner", json={"username": "   ", "password": "correct horse battery staple"}
    )
    assert response.status_code == 422


def test_openapi_is_local_and_swagger_cdn_page_is_disabled(context) -> None:
    assert context.client.get("/api/v1/openapi.json").status_code == 200
    docs = context.client.get("/api/v1/docs")
    assert docs.status_code == 404
    assert "http://" not in docs.text and "https://" not in docs.text


def test_concurrent_first_run_requests_create_exactly_one_owner(context) -> None:
    def attempt(username: str) -> int:
        return context.client.post(
            "/api/v1/setup/owner",
            json={"username": username, "password": "a secure and sufficiently varied password"},
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(attempt, ("owner-one", "owner-two")))
    assert statuses == [201, 409]
    with context.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_explicit_product_config_missing_fails_fast(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRODUCT_CONFIG_FILE", str(tmp_path / "missing-product.json"))
    get_product_config.cache_clear()
    with pytest.raises(RuntimeError, match="missing"):
        get_product_config()
    get_product_config.cache_clear()


def test_product_api_prefix_is_fixed_for_phase_one() -> None:
    with pytest.raises(ValueError, match="/api/v1"):
        ProductConfig(api_prefix="/other")


def test_runtime_secret_is_required_and_placeholders_are_rejected(monkeypatch) -> None:
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    with pytest.raises(ValidationError, match="app_secret_key"):
        AppConfig(_env_file=None)
    monkeypatch.setenv("APP_SECRET_KEY", "GENERATE_WITH_BOOTSTRAP_DO_NOT_USE")
    with pytest.raises(ValidationError, match="securely generated"):
        AppConfig(_env_file=None)


def test_api_timestamps_are_explicit_utc(owner_context) -> None:
    context, _csrf = owner_context
    audit = context.client.get("/api/v1/audit-events").json()
    created_at = audit["items"][0]["created_at"]
    assert created_at.endswith("Z") or created_at.endswith("+00:00")
