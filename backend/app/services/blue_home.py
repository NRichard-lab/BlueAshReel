"""Resolve trusted Portal profile claims independently of account permissions."""

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import BlueHomeState, User


def viewing_user(db: Session, authorization: dict[str, Any], owner_id: str, account_user: User) -> str:
    context = authorization.get("blue_home")
    if context is None:
        return account_user.id
    if not isinstance(context, dict) or context.get("owner_account_id") != owner_id:
        raise HTTPException(403, "Profile unavailable")
    try:
        profile_id = str(uuid.UUID(context["profile_id"]))
        home_id = str(uuid.UUID(context["home_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(403, "Profile unavailable") from None
    is_owner = context.get("owner_profile") is True
    if is_owner and authorization.get("user_id") != owner_id:
        raise HTTPException(403, "Profile unavailable")
    row = db.get(BlueHomeState, profile_id)
    if row is None:
        if is_owner:
            state_user = account_user  # Preserve original row IDs, positions and watched flags.
        else:
            state_user = User(
                id=profile_id,
                username="Blue Home viewing state",
                normalized_username="blue-home-" + profile_id,
                password_hash="!profile-no-login",  # noqa: S106 - invalid credential; cannot authenticate
                is_active=True,
            )
            db.add(state_user)
            db.flush()
        row = BlueHomeState(
            profile_id=profile_id, home_id=home_id, owner_account_id=owner_id, local_user_id=state_user.id
        )
        db.add(row)
        db.flush()
    if row.home_id != home_id or row.owner_account_id != owner_id:
        raise HTTPException(403, "Profile unavailable")
    return row.local_user_id
