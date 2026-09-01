from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import AppConfig
from app.models import User, UserSession, utcnow

password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_DUMMY_HASH = password_hasher.hash("constant dummy password used to equalize unknown users")


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def verify_login(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(
        select(User).options(selectinload(User.roles)).where(User.normalized_username == normalize_username(username))
    )
    valid = verify_password(user.password_hash if user else _DUMMY_HASH, password)
    if not valid or user is None or not user.is_active:
        return None
    return user


def token_digest(token: str, secret_key: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class NewSession:
    session: UserSession
    token: str
    csrf_token: str


def create_session(db: Session, user: User, config: AppConfig) -> NewSession:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=token_digest(token, config.app_secret_key),
        csrf_hash=token_digest(csrf_token, config.app_secret_key),
        expires_at=utcnow() + timedelta(hours=config.session_ttl_hours),
    )
    db.add(session)
    db.flush()
    return NewSession(session=session, token=token, csrf_token=csrf_token)


def find_session(db: Session, token: str | None, config: AppConfig) -> UserSession | None:
    if not token:
        return None
    session = db.scalar(
        select(UserSession)
        .options(selectinload(UserSession.user).selectinload(User.roles))
        .where(UserSession.token_hash == token_digest(token, config.app_secret_key))
    )
    now = datetime.now(UTC)
    if (
        session is None
        or session.revoked_at is not None
        or _as_utc(session.expires_at) <= now
        or not session.user.is_active
    ):
        return None
    session.last_seen_at = utcnow()
    return session


def valid_csrf(session: UserSession, supplied: str | None, config: AppConfig) -> bool:
    return bool(supplied) and hmac.compare_digest(
        session.csrf_hash, token_digest(supplied or "", config.app_secret_key)
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
