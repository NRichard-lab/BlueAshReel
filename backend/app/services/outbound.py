from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import ApplicationSetting

KNOWN_INTEGRATIONS = ("metadata", "artwork", "portal", "telemetry")


class OutboundConnectionDisabled(PermissionError):
    pass


def outbound_enabled(db: Session, config: AppConfig, integration: str) -> bool:
    if integration not in KNOWN_INTEGRATIONS or not config.outbound_integrations_enabled:
        return False
    setting = db.get(ApplicationSetting, f"outbound.{integration}.enabled")
    return bool(setting and setting.value is True)


def require_outbound_permission(db: Session, config: AppConfig, integration: str) -> None:
    if not outbound_enabled(db, config, integration):
        raise OutboundConnectionDisabled(f"Outbound integration '{integration}' is disabled")
