from __future__ import annotations

from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import AppConfig, get_config
from app.database import get_db
from app.models import User, UserSession
from app.security import find_session, valid_csrf


@dataclass(frozen=True)
class Principal:
    user: User
    session: UserSession


def current_principal(
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
    session_cookie: str | None = Cookie(default=None, alias="media_session"),
) -> Principal:
    # Support a configured cookie name without making it part of public API state.
    token = request.cookies.get(config.session_cookie_name) or session_cookie
    session = find_session(db, token, config)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return Principal(user=session.user, session=session)


def require_owner(principal: Principal = Depends(current_principal)) -> Principal:
    if "Owner" not in {role.name for role in principal.user.roles}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")
    return principal


def require_manager(principal: Principal = Depends(current_principal)) -> Principal:
    if not {"Owner", "Administrator"}.intersection(role.name for role in principal.user.roles):
        raise HTTPException(status_code=403, detail="Administration access required")
    return principal


def require_user_csrf(
    principal: Principal = Depends(current_principal),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    config: AppConfig = Depends(get_config),
) -> Principal:
    if not valid_csrf(principal.session, csrf_token, config):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return principal


def require_manager_csrf(
    principal: Principal = Depends(require_manager),
    _csrf: Principal = Depends(require_user_csrf),
) -> Principal:
    return principal


def require_csrf(
    principal: Principal = Depends(require_owner),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    config: AppConfig = Depends(get_config),
) -> Principal:
    if not valid_csrf(principal.session, csrf_token, config):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    return principal
