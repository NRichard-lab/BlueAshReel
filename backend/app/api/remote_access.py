from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.config import AppConfig, get_config
from app.database import get_db
from app.dependencies import Principal, require_csrf, require_owner
from app.remote.control import queue_action, snapshot
from app.services.audit import record_audit

router = APIRouter(prefix="/remote-access", tags=["remote access"])


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=26, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    confirm_enable: Literal[True]

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        code = value.replace("-", "").replace(" ", "").upper()
        if len(code) != 26 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in code):
            raise ValueError("Enter the complete pairing code from the portal")
        return code

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(not character.isprintable() for character in value):
            raise ValueError("Choose a friendly name without control characters")
        return value


@router.get("")
def remote_access_status(
    _principal: Principal = Depends(require_owner), config: AppConfig = Depends(get_config)
) -> dict[str, Any]:
    return snapshot(config.remote_control_dir)


def _action(
    action: str, principal: Principal, db: Session, config: AppConfig, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    directory = config.remote_control_dir
    if directory is None:
        raise HTTPException(503, "The isolated remote connector has not been installed for this server")
    state = snapshot(directory)
    if action == "pair" and (state["paired"] or state["central_revocation_pending"]):
        raise HTTPException(409, "Finish unpairing the existing Agent before using another code")
    if action == "pair" and not state["available"]:
        raise HTTPException(503, "Start the isolated connector before pairing")
    if action == "reconnect" and not state["paired"]:
        raise HTTPException(409, "Pair this Agent before reconnecting")
    try:
        queue_action(directory, action, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    record_audit(db, f"remote.{action}_requested", actor_user_id=principal.user.id, target_type="remote_access")
    db.commit()
    return snapshot(directory)


@router.post("/pair", status_code=202)
def pair_remote_access(
    payload: PairRequest, principal: Principal = Depends(require_csrf),
    db: Session = Depends(get_db), config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    return _action("pair", principal, db, config, {"code": payload.code, "name": payload.name})


@router.post("/{action}", status_code=202)
def remote_access_action(
    action: Literal["reconnect", "unpair", "revoke"], principal: Principal = Depends(require_csrf),
    db: Session = Depends(get_db), config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    return _action(action, principal, db, config)
