"""Durable, non-secret General settings owned by the local Agent."""

from __future__ import annotations

import json
import locale
import os
import re
import socket
from collections.abc import Mapping
from typing import Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import AppConfig, get_product_config

SETTING_KEY = "general.settings"
LANGUAGES = ("en-US",)
DATE_FORMATS = ("system", "mdy", "dmy", "iso")
TIME_FORMATS = ("system", "12h", "24h")

# ISO 3166-1 alpha-2 codes. Keeping validation independent of display labels
# lets the Portal add translated country names without changing this contract.
REGION_CODES = frozenset(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM
    JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP
    MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY
    QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
    TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split()  # noqa: SIM905
)

WINDOWS_IANA = {
    "Dateline Standard Time": "Etc/GMT+12",
    "UTC": "UTC",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Tokyo Standard Time": "Asia/Tokyo",
    "China Standard Time": "Asia/Shanghai",
    "India Standard Time": "Asia/Kolkata",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "New Zealand Standard Time": "Pacific/Auckland",
}


class IdentitySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)

    @field_validator("agent_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Agent name cannot be blank or contain control characters")
        return value

    @field_validator("description")
    @classmethod
    def valid_description(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\r\n\t" for character in value):
            raise ValueError("Description contains unsupported control characters")
        return value.strip()


class LanguageRegionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["en-US"] = "en-US"
    region: str = Field(min_length=2, max_length=2)
    timezone: str = Field(min_length=1, max_length=100)
    date_format: Literal["system", "mdy", "dmy", "iso"] = "system"
    time_format: Literal["system", "12h", "24h"] = "system"

    @field_validator("region")
    @classmethod
    def valid_region(cls, value: str) -> str:
        value = value.upper()
        if value not in REGION_CODES:
            raise ValueError("Unknown country or region code")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        known = available_timezones()
        valid_shape = re.fullmatch(r"[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+", value)
        if value != "UTC" and (not valid_shape or known and value not in known):
            raise ValueError("Unknown IANA time zone")
        return value


class StartupConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_with_windows: bool = False
    launch_portal_on_start: bool = False
    auto_connect: bool = True
    auto_reconnect: bool = True


class GeneralSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    identity: IdentitySettings
    language_region: LanguageRegionSettings
    startup_connection: StartupConnectionSettings
    capabilities: dict[str, bool] = Field(default_factory=dict)


def _region_default() -> str:
    candidates = [locale.getlocale()[0], os.getenv("LANG", "")]
    for candidate in candidates:
        if candidate:
            match = re.search(r"[-_]([A-Za-z]{2})(?:[.@]|$)", candidate)
            if match and match.group(1).upper() in REGION_CODES:
                return match.group(1).upper()
    return "US"


def _timezone_default() -> str:
    try:
        if os.name == "nt":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation",
            ) as key:
                windows_name = str(winreg.QueryValueEx(key, "TimeZoneKeyName")[0]).strip("\x00 ")
            return WINDOWS_IANA.get(windows_name, "UTC")
        target = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in target:
            candidate = target.split(marker, 1)[1]
            if candidate in available_timezones():
                return candidate
    except (OSError, ValueError):
        pass
    return "UTC"


def _device_name() -> str:
    value = "".join(character for character in socket.gethostname().strip() if character.isprintable())[:80]
    return value or get_product_config().agent_name


def _existing_name(config: AppConfig) -> str:
    if config.native_data_dir:
        try:
            from app.remote.storage import read_json

            name = read_json(config.native_data_dir / "remote-identity/identity.json").get("name")
            return IdentitySettings(agent_name=name, description="").agent_name
        except (OSError, TypeError, ValueError):
            pass
    return _device_name()


def _stored(db: Session) -> Mapping[str, Any]:
    raw = db.execute(
        text("SELECT value FROM application_settings WHERE key = :key"), {"key": SETTING_KEY}
    ).scalar_one_or_none()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def defaults(db: Session, config: AppConfig) -> GeneralSettings:
    stored = _stored(db)
    return GeneralSettings.model_validate(
        {
            "schema_version": 1,
            "identity": {"agent_name": _existing_name(config), "description": ""},
            "language_region": {
                "language": "en-US",
                "region": _region_default(),
                "timezone": _timezone_default(),
                "date_format": "system",
                "time_format": "system",
            },
            "startup_connection": {
                "start_with_windows": False,
                "launch_portal_on_start": False,
                "auto_connect": True,
                "auto_reconnect": True,
            },
            **stored,
        }
    )


def read_general(db: Session, config: AppConfig, startup: Any | None = None) -> GeneralSettings:
    value = defaults(db, config)
    supported = config.deployment_mode == "native_windows" and startup is not None
    current = startup.enabled() if supported and startup is not None else False
    return value.model_copy(
        update={
            "startup_connection": value.startup_connection.model_copy(update={"start_with_windows": current}),
            "capabilities": {"start_with_windows": supported},
        }
    )


def update_general(
    db: Session, config: AppConfig, patch: Mapping[str, Any], startup: Any | None = None
) -> GeneralSettings:
    if set(patch) - {"identity", "language_region", "startup_connection"}:
        raise ValueError("Unknown General settings group")
    current = read_general(db, config, startup)
    proposed = current.model_dump(exclude={"capabilities"})
    for group, fields in patch.items():
        if not isinstance(fields, dict):
            raise ValueError("Settings groups must be objects")
        proposed[group] = {**proposed[group], **fields}
    validated = GeneralSettings.model_validate(proposed)
    requested_startup = validated.startup_connection.start_with_windows
    if requested_startup != current.startup_connection.start_with_windows:
        if startup is None or config.deployment_mode != "native_windows":
            raise ValueError("Windows startup is unavailable for this Agent")
        startup.set_enabled(requested_startup)
        if startup.enabled() != requested_startup:
            raise OSError("Windows startup registration did not reach the requested state")
    durable = validated.model_dump(exclude={"capabilities"})
    # Registry state is authoritative and is never restored later from SQLite.
    durable["startup_connection"].pop("start_with_windows", None)
    from app.models import ApplicationSetting

    db.merge(ApplicationSetting(key=SETTING_KEY, value=durable))
    try:
        db.commit()
    except Exception:
        db.rollback()
        if requested_startup != current.startup_connection.start_with_windows and startup is not None:
            startup.set_enabled(current.startup_connection.start_with_windows)
        raise
    return read_general(db, config, startup)
