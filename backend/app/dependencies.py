from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import AppConfig, get_config
from app.database import get_db
from app.models import Role, User, UserSession
from app.security import find_session, valid_csrf
from app.services.setup_session import valid_setup_csrf, valid_setup_session


@dataclass(frozen=True)
class Principal:
    user: User
    session: UserSession


@dataclass(frozen=True)
class MediaBrowserAccess:
    principal: Principal | None


def setup_is_pending(db: Session) -> bool:
    return db.scalar(select(User.id).join(User.roles).where(Role.name == "Owner").limit(1)) is None


def require_setup_csrf(
    request: Request,
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> None:
    # Completed setup is rejected by the endpoint with 409. Before completion,
    # only the short-lived browser-bound setup capability may create the Owner.
    if setup_is_pending(db) and not valid_setup_csrf(
        request.cookies.get(config.setup_cookie_name), csrf_token, config
    ):
        raise HTTPException(status_code=403, detail="A valid first-run setup session and CSRF token are required")


def current_principal(
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> Principal:
    # Support a configured cookie name without making it part of public API state.
    token = request.cookies.get(config.session_cookie_name)
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


def require_media_browser(
    request: Request,
    db: Session = Depends(get_db),
    config: AppConfig = Depends(get_config),
) -> MediaBrowserAccess:
    session = find_session(db, request.cookies.get(config.session_cookie_name), config)
    if session is not None:
        principal = Principal(user=session.user, session=session)
        principal = (
            require_owner(principal) if config.deployment_mode == "native_windows" else require_manager(principal)
        )
        return MediaBrowserAccess(principal=principal)
    if setup_is_pending(db) and valid_setup_session(request.cookies.get(config.setup_cookie_name), config):
        return MediaBrowserAccess(principal=None)
    raise HTTPException(status_code=401, detail="An authorized account or active first-run setup session is required")


def require_media_browser_csrf(
    request: Request,
    access: MediaBrowserAccess = Depends(require_media_browser),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    config: AppConfig = Depends(get_config),
) -> MediaBrowserAccess:
    valid = (
        valid_csrf(access.principal.session, csrf_token, config)
        if access.principal is not None
        else valid_setup_csrf(request.cookies.get(config.setup_cookie_name), csrf_token, config)
    )
    if not valid:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return access


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
