from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import Principal, current_principal, require_manager, require_manager_csrf, require_user_csrf
from app.models import Library, Role, User, UserLibrary, UserPreference, UserSession, WatchProgress, utcnow
from app.security import hash_password, normalize_username
from app.services.audit import record_audit

router = APIRouter(tags=["household"])


class PasswordInput(BaseModel):
    @field_validator("password", check_fields=False)
    @classmethod
    def varied_password(cls, value: str | None) -> str | None:
        if value is not None and (value.isspace() or len(set(value)) < 4):
            raise ValueError("Password must contain a reasonable variety of characters")
        return value


class HouseholdUserInput(PasswordInput):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[\w .@+-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Literal["Owner", "Administrator", "Viewer"] = "Viewer"
    library_ids: list[str] = Field(default_factory=list, max_length=1000)


class HouseholdUserUpdate(PasswordInput):
    role: Literal["Owner", "Administrator", "Viewer"] | None = None
    library_ids: list[str] | None = Field(default=None, max_length=1000)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


class PreferencesInput(BaseModel):
    auto_next: bool = True
    next_countdown: int = Field(default=10, ge=5, le=60)


def assignments(db: Session, user_id: str) -> list[str]:
    return list(db.scalars(select(UserLibrary.library_id).where(UserLibrary.user_id == user_id)))


def assign_libraries(db: Session, user_id: str, ids: list[str]) -> None:
    ids = list(set(ids))
    if len(ids) != db.scalar(select(func.count(Library.id)).where(Library.id.in_(ids))):
        raise HTTPException(422, "One or more libraries no longer exist")
    db.execute(delete(UserLibrary).where(UserLibrary.user_id == user_id))
    db.add_all(UserLibrary(user_id=user_id, library_id=value) for value in ids)


def public_user(user: User, library_ids: list[str]) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "roles": [role.name for role in user.roles],
        "is_active": user.is_active,
        "library_ids": library_ids,
    }


def serialize_user_change(db: Session, principal: Principal) -> None:
    # Serialize last-Owner checks against concurrent account mutations in SQLite.
    db.rollback()
    db.execute(text("BEGIN IMMEDIATE"))
    if not principal.user.is_active or principal.session.revoked_at is not None:
        raise HTTPException(401, "Authentication required")
    require_manager(principal)


def guard_change(principal: Principal, user: User, role: str | None = None) -> None:
    owner = "Owner" in {r.name for r in principal.user.roles}
    if not owner and ("Owner" in {r.name for r in user.roles} or role == "Owner"):
        raise HTTPException(403, "Only an Owner may manage Owner accounts")
    if not owner and user.id == principal.user.id:
        raise HTTPException(403, "An Owner must change an Administrator's own access")


def protect_last_owner(db: Session, user: User, removing_owner: bool) -> None:
    if removing_owner and user.is_active and "Owner" in {r.name for r in user.roles}:
        count = db.scalar(
            select(func.count(User.id)).join(User.roles).where(User.is_active.is_(True), Role.name == "Owner")
        )
        if (count or 0) <= 1:
            raise HTTPException(409, "The last active Owner must be preserved")


def revoke(db: Session, user_id: str) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


@router.get("/users")
def users(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    _principal: Principal = Depends(require_manager),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.scalars(
        select(User).order_by(User.normalized_username, User.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    grants: dict[str, list[str]] = {u.id: [] for u in rows}
    for grant in db.scalars(select(UserLibrary).where(UserLibrary.user_id.in_(grants))):
        grants[grant.user_id].append(grant.library_id)
    return {
        "items": [public_user(u, grants[u.id]) for u in rows],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(select(func.count(User.id))),
    }


@router.post("/users", status_code=201)
def create_user(
    payload: HouseholdUserInput, principal: Principal = Depends(require_manager_csrf), db: Session = Depends(get_db)
) -> dict[str, Any]:
    password_hash = hash_password(payload.password)
    serialize_user_change(db, principal)
    if payload.role == "Owner" and "Owner" not in {r.name for r in principal.user.roles}:
        raise HTTPException(403, "Only an Owner may create another Owner")
    normalized = normalize_username(payload.username)
    if not normalized or db.scalar(select(User.id).where(User.normalized_username == normalized)):
        raise HTTPException(409, "Choose a different household username")
    role = db.scalar(select(Role).where(Role.name == payload.role))
    if role is None:
        raise HTTPException(409, "Household roles are not initialized")
    user = User(
        username=payload.username.strip(),
        normalized_username=normalized,
        password_hash=password_hash,
        roles=[role],
    )
    db.add(user)
    db.flush()
    assign_libraries(db, user.id, payload.library_ids)
    record_audit(db, "user.created", actor_user_id=principal.user.id, target_type="user", target_id=user.id)
    db.commit()
    return public_user(user, payload.library_ids)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    payload: HouseholdUserUpdate,
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    password_hash = hash_password(payload.password) if payload.password is not None else None
    serialize_user_change(db, principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Household user not found")
    guard_change(principal, user, payload.role)
    role_changed = payload.role is not None and {r.name for r in user.roles} != {payload.role}
    protect_last_owner(db, user, payload.is_active is False or payload.role not in (None, "Owner"))
    if payload.role is not None:
        role = db.scalar(select(Role).where(Role.name == payload.role))
        assert role is not None
        user.roles = [role]
    if payload.library_ids is not None:
        assign_libraries(db, user.id, payload.library_ids)
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if password_hash is not None:
        user.password_hash = password_hash
    if payload.password is not None or payload.is_active is False or role_changed:
        revoke(db, user.id)
    record_audit(db, "user.updated", actor_user_id=principal.user.id, target_type="user", target_id=user.id)
    db.commit()
    return public_user(user, assignments(db, user.id))


@router.post("/users/{user_id}/revoke", status_code=204)
def revoke_user(
    user_id: str, principal: Principal = Depends(require_manager_csrf), db: Session = Depends(get_db)
) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Household user not found")
    guard_change(principal, user)
    revoke(db, user.id)
    record_audit(db, "user.sessions_revoked", actor_user_id=principal.user.id, target_type="user", target_id=user.id)
    db.commit()


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    delete_history: bool = Query(...),
    principal: Principal = Depends(require_manager_csrf),
    db: Session = Depends(get_db),
) -> None:
    serialize_user_change(db, principal)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Household user not found")
    guard_change(principal, user)
    protect_last_owner(db, user, True)
    if delete_history:
        db.execute(delete(WatchProgress).where(WatchProgress.user_id == user_id))
    record_audit(
        db,
        "user.deleted",
        actor_user_id=principal.user.id,
        target_type="user",
        target_id=user_id,
        details={"history_deleted": delete_history},
    )
    db.flush()
    db.execute(delete(User).where(User.id == user_id))
    db.commit()


@router.get("/profile")
def profile(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)) -> dict[str, Any]:
    preferences = db.get(UserPreference, principal.user.id)
    return {
        **public_user(principal.user, assignments(db, principal.user.id)),
        "auto_next": preferences.auto_next if preferences else True,
        "next_countdown": preferences.next_countdown if preferences else 10,
    }


@router.patch("/profile")
def update_profile(
    payload: PreferencesInput, principal: Principal = Depends(require_user_csrf), db: Session = Depends(get_db)
) -> dict[str, Any]:
    preferences = db.get(UserPreference, principal.user.id)
    if preferences is None:
        preferences = UserPreference(user_id=principal.user.id)
        db.add(preferences)
    preferences.auto_next = payload.auto_next
    preferences.next_countdown = payload.next_countdown
    db.commit()
    return profile(principal, db)


@router.delete("/profile/history", status_code=204)
def delete_history(principal: Principal = Depends(require_user_csrf), db: Session = Depends(get_db)) -> None:
    db.execute(delete(WatchProgress).where(WatchProgress.user_id == principal.user.id))
    db.commit()
