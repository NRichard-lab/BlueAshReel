from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from app.config import AppConfig

SETUP_SESSION_COOKIE_NAME = "bluereel_setup"
SETUP_SESSION_TTL_SECONDS = 30 * 60
MAX_SETUP_TOKEN_LENGTH = 1024


@dataclass(frozen=True)
class NewSetupSession:
    token: str
    csrf_token: str


def _digest(value: str, config: AppConfig) -> str:
    return hmac.new(config.app_secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def create_setup_session(config: AppConfig) -> NewSetupSession:
    expires_at = int(time.time()) + SETUP_SESSION_TTL_SECONDS
    nonce = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    csrf_digest = _digest(f"setup-csrf:{csrf_token}", config)
    payload = f"{expires_at}.{nonce}.{csrf_digest}"
    signature = _digest(f"setup-session:{payload}", config)
    return NewSetupSession(token=f"bs1.{payload}.{signature}", csrf_token=csrf_token)


def valid_setup_session(token: str | None, config: AppConfig) -> bool:
    if not token or len(token) > MAX_SETUP_TOKEN_LENGTH or not token.isascii():
        return False
    try:
        prefix, raw_expiry, nonce, csrf_digest, supplied_signature = token.split(".", 4)
        expires_at = int(raw_expiry)
    except (TypeError, ValueError):
        return False
    if (
        prefix != "bs1"
        or not nonce
        or len(nonce) > 128
        or len(csrf_digest) != 64
        or expires_at <= int(time.time())
        or expires_at > int(time.time()) + SETUP_SESSION_TTL_SECONDS + 60
    ):
        return False
    payload = f"{raw_expiry}.{nonce}.{csrf_digest}"
    expected_signature = _digest(f"setup-session:{payload}", config)
    return hmac.compare_digest(supplied_signature, expected_signature)


def valid_setup_csrf(token: str | None, supplied: str | None, config: AppConfig) -> bool:
    if token is None or not valid_setup_session(token, config) or supplied is None or len(supplied) > 1024:
        return False
    try:
        _prefix, _expiry, _nonce, expected_csrf_digest, _signature = token.split(".", 4)
    except (TypeError, ValueError):
        return False
    supplied_digest = _digest(f"setup-csrf:{supplied}", config)
    return hmac.compare_digest(expected_csrf_digest, supplied_digest)
