from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models import ApplicationSetting
from app.services.general_settings import GeneralSettings, read_general, update_general
from app.services.windows_startup import RUN_KEY, WindowsStartupRegistration


class Startup:
    def __init__(self, enabled: bool = False) -> None:
        self.value = enabled
        self.changes: list[bool] = []

    def enabled(self) -> bool:
        return self.value

    def set_enabled(self, value: bool) -> None:
        self.changes.append(value)
        self.value = value


def test_general_defaults_and_persistence_survive_session_reload(context) -> None:
    with context.session_factory() as db:
        initial = read_general(db, context.config)
        assert initial.schema_version == 1
        assert initial.identity.agent_name
        assert initial.language_region.language == "en-US"
        assert initial.language_region.region
        assert initial.language_region.date_format == "system"
        assert initial.startup_connection.auto_connect is True
        assert initial.startup_connection.auto_reconnect is True
        saved = update_general(
            db,
            context.config,
            {
                "identity": {"agent_name": "  Family Agent  ", "description": " Movies and shows. "},
                "language_region": {"region": "ca", "timezone": "America/Toronto"},
                "startup_connection": {"launch_portal_on_start": True, "auto_connect": False},
            },
        )
        assert saved.identity.agent_name == "Family Agent"
        assert saved.identity.description == "Movies and shows."
        assert saved.language_region.region == "CA"
    with context.session_factory() as db:
        reloaded = read_general(db, context.config)
        assert reloaded.identity.agent_name == "Family Agent"
        assert reloaded.language_region.timezone == "America/Toronto"
        assert reloaded.startup_connection.launch_portal_on_start is True
        assert reloaded.startup_connection.auto_connect is False
        assert reloaded.startup_connection.auto_reconnect is True


@pytest.mark.parametrize(
    "patch",
    [
        {"identity": {"agent_name": "   "}},
        {"identity": {"agent_name": "x" * 81}},
        {"identity": {"description": "x" * 501}},
        {"language_region": {"language": "fr-FR"}},
        {"language_region": {"region": "ZZ"}},
        {"language_region": {"timezone": "Mountain Standard Time"}},
        {"language_region": {"date_format": "DD/MM/YYYY"}},
        {"language_region": {"time_format": "military"}},
        {"unknown": {}},
    ],
)
def test_general_settings_reject_invalid_values_without_persisting(context, patch) -> None:
    with context.session_factory() as db, pytest.raises((ValidationError, ValueError)):
        update_general(db, context.config, patch)
    with context.session_factory() as db:
        assert db.get(ApplicationSetting, "general.settings") is None


def test_windows_startup_uses_actual_state_and_is_idempotent(context) -> None:
    registration = Startup()
    native = context.config.model_copy(update={"deployment_mode": "native_windows"})
    with context.session_factory() as db:
        enabled = update_general(
            db, native, {"startup_connection": {"start_with_windows": True}}, registration
        )
        assert enabled.startup_connection.start_with_windows is True
        assert registration.changes == [True]
        update_general(db, native, {"startup_connection": {"start_with_windows": True}}, registration)
        assert registration.changes == [True]
        disabled = update_general(
            db, native, {"startup_connection": {"start_with_windows": False}}, registration
        )
        assert disabled.startup_connection.start_with_windows is False
        assert registration.changes == [True, False]


def test_remote_general_settings_are_partial_and_owner_only(owner_context) -> None:
    from test_remote_media import call, setup_remote

    media, authorization, _ = setup_remote(owner_context)
    initial = call(media, authorization, "settings.general.get")
    assert initial["identity"]["agent_name"]
    updated = call(media, authorization, "settings.general.update", identity={"description": "Optional"})
    assert updated["identity"]["description"] == "Optional"
    viewer = {
        **authorization,
        "user_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "role": "viewer",
    }
    card = call(media, authorization, "catalog.list")["items"][0]
    call(media, authorization, "grants.set", user_id=viewer["user_id"], role="viewer", library_ids=[card["library_id"]])
    with pytest.raises(HTTPException) as denied:
        call(media, viewer, "settings.general.get")
    assert denied.value.status_code == 403


def test_windows_registration_matches_existing_tray_convention(monkeypatch) -> None:
    values: dict[str, str] = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def query(_key, name):
        if name not in values:
            raise FileNotFoundError
        return values[name], 1

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="hkcu",
        REG_SZ=1,
        OpenKey=lambda root, path: Key(),
        CreateKey=lambda root, path: Key(),
        QueryValueEx=query,
        SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value),
        DeleteValue=lambda key, name: values.pop(name),
    )
    registration = object.__new__(WindowsStartupRegistration)
    registration.name = "BlueReelDevelopmentTray"
    registration.command = '"C:\\Program Files\\BlueAshReelAgent.exe" --data-dir "C:\\Agent Data"'
    monkeypatch.setattr(registration, "_registry", lambda: registry)
    assert RUN_KEY.endswith(r"CurrentVersion\Run") and not registration.enabled()
    registration.set_enabled(True)
    assert registration.enabled() and values[registration.name] == registration.command
    registration.set_enabled(True)
    assert len(values) == 1
    registration.set_enabled(False)
    assert not registration.enabled()


def test_general_model_does_not_accept_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GeneralSettings.model_validate(
            {
                "schema_version": 1,
                "identity": {"agent_name": "Agent", "description": "", "secret": "no"},
                "language_region": {"language": "en-US", "region": "US", "timezone": "UTC"},
                "startup_connection": {},
            }
        )
