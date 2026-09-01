from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.logging_config import redact
from app.models import AuditEvent


def record_audit(
    db: Session,
    event_type: str,
    *,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_user_id=actor_user_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        details=redact(details or {}),
    )
    db.add(event)
    return event
