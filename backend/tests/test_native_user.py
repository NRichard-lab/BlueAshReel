from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from app import native_consent, native_install, native_user_install
from app.native_runtime import NativeRuntimeError, load_configuration, load_installation
from app.native_tray import command_action, connection_label


@pytest.fixture
def user_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    program, data = tmp_path / "Programs/Agent", tmp_path / "User Data"
    for directory in (program / "runtime/python", program / "runtime/ffmpeg", program / "config"):
        directory.mkdir(parents=True)
    for executable in (program / "runtime/python/python.exe", program / "runtime/ffmpeg/ffmpeg.exe",
                       program / "runtime/ffmpeg/ffprobe.exe"):
        executable.write_bytes(b"synthetic runtime fixture")
    (program / "config/product.json").write_text('{"name":"Blue Ash Reel"}', encoding="utf-8")
    monkeypatch.setattr(native_install, "_executing_program_dir", lambda: program)
    monkeypatch.setattr(native_install, "validate_ports", lambda *args: None)
    return program, data


def test_user_install_separates_storage_without_service_or_media_setup(user_layout: tuple[Path, Path]) -> None:
    program, data = user_layout
    artwork = data.parent / "Dedicated artwork"
    record = native_user_install.configure(program, data, "development", 19080, {"artwork": str(artwork)})
    config = load_configuration(load_installation(data))
    assert record["runtime_mode"] == "per_user" and record["bind_address"] == "127.0.0.1"
    assert config.artwork_dir == artwork
    assert config.approved_media_roots == ()
    assert not (program / "services").exists()
    assert not (data / "database/app.db").exists()
    assert (data / "state/tray-requests").is_dir()
    assert (data / "remote-identity").is_dir()
    assert config.remote_control_dir == data / "remote-control"


def test_repair_preserves_every_storage_setting_and_secret(user_layout: tuple[Path, Path]) -> None:
    program, data = user_layout
    original = native_user_install.configure(program, data, "development", 19080, {}, max_processes=1, threads=3)
    before = (data / "configuration/.env").read_bytes()
    repaired = native_user_install.configure(
        program, data, "development", 29080, {"temp": str(data.parent / "ignored")})
    assert repaired == original
    assert (data / "configuration/.env").read_bytes() == before
    assert dotenv_values(data / "configuration/.env")["TRANSCODE_THREADS"] == "3"


def test_foreign_channel_repair_and_foreign_fresh_data_cannot_change_permissions(
    user_layout: tuple[Path, Path],
) -> None:
    program, data = user_layout
    data.mkdir()
    sentinel = data / "private.txt"
    sentinel.write_text("leave unchanged")
    with patch.object(native_user_install, "protect_directory") as protection:
        with pytest.raises(ValueError):
            native_user_install.configure(program, data, "development", 19080, {})
        protection.assert_not_called()
    sentinel.unlink()
    native_user_install.configure(program, data, "development", 19080, {})
    initial = (data / "configuration/.env").read_bytes()
    with pytest.raises(ValueError, match="channel"):
        native_user_install.configure(program, data, "stable", 8080, {})
    assert (data / "configuration/.env").read_bytes() == initial


def test_snapshot_and_rollback_preserve_advanced_database_location(user_layout: tuple[Path, Path]) -> None:
    program, data = user_layout
    long_relative = Path("nested-dependency-" * 5) / ("source-directory-" * 5) / "file.txt"
    long_file = native_user_install._filesystem_path(program / long_relative)
    long_file.parent.mkdir(parents=True)
    long_file.write_text("long bundled dependency")
    database_directory = data.parent / "Separate SQLite"
    backup_directory = data.parent / "Separate Backups"
    record = native_user_install.configure(program, data, "development", 19080,
        {"database": str(database_directory), "backups": str(backup_directory)})
    database = database_directory / "app.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel(value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('before upgrade')")
    recovery = native_user_install.snapshot(data, include_program=True)
    assert recovery.parent == backup_directory
    assert (recovery / "program/config/product.json").is_file()
    retained_file = native_user_install._filesystem_path(recovery / "program" / long_relative)
    assert retained_file.read_text() == "long bundled dependency"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sentinel SET value='failed upgrade state'")
    assert native_user_install.restore_snapshot(data) == recovery
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone() == ("before upgrade",)
    with sqlite3.connect(recovery / "failed-user-app.db") as connection:
        assert connection.execute("SELECT value FROM sentinel").fetchone() == ("failed upgrade state",)
    assert not (data / "database/app.db").exists()
    assert json.loads((data / "configuration/installation.json").read_text()) == record


def test_per_user_configuration_rejects_unregistered_storage_redirection(user_layout: tuple[Path, Path]) -> None:
    program, data = user_layout
    native_user_install.configure(program, data, "development", 19080, {})
    environment = data / "configuration/.env"
    environment.write_text(environment.read_text().replace('ARTWORK_DIR=', 'IGNORED_ARTWORK_DIR=') +
                           'ARTWORK_DIR=' + json.dumps(str(data / "state")) + '\n')
    with pytest.raises(NativeRuntimeError, match="storage"):
        load_configuration(load_installation(data))


@pytest.mark.parametrize("relationship", ["program", "same", "parent", "nonempty"])
def test_advanced_storage_rejects_overlap_and_existing_data(user_layout: tuple[Path, Path], relationship: str) -> None:
    program, data = user_layout
    chosen = program if relationship == "program" else data if relationship == "same" else data.parent
    if relationship == "nonempty":
        chosen = data.parent / "existing"
        chosen.mkdir()
        (chosen / "preserve.txt").write_text("preserve me")
    with pytest.raises((ValueError, native_install.NativeInstallError)):
        native_user_install.configure(program, data, "development", 19080, {"artwork": str(chosen)})
    assert not (data / native_install.MARKER).exists()


@pytest.mark.parametrize(("status", "healthy", "expected"), [
    ({"paired": True, "state": "connected_through_relay", "updated_at": 99}, True, "Connected"),
    ({"paired": True, "state": "connected_through_relay", "updated_at": 50}, True, "Paired but Agent offline"),
    ({"paired": True, "state": "reconnecting", "updated_at": 99}, True, "Connecting"),
    ({"paired": False, "state": "disabled", "updated_at": 99}, True, "Not paired"),
    ({"paired": True, "state": "connected_through_relay", "updated_at": 99}, False, "Paired but Agent offline"),
])
def test_tray_connected_requires_fresh_authenticated_tunnel(status: dict, healthy: bool, expected: str) -> None:
    assert connection_label(status, healthy=healthy, now=100) == expected
    assert connection_label(status, healthy=healthy, now=100, paused=True) == "Paused"


@pytest.mark.parametrize("binding", ["matching", "older_generation", "older_pid", "missing_generation"])
def test_maintenance_stop_cannot_terminate_replacement_runtime(binding: str) -> None:
    command = {"action": "exit", "created_at": 99, "maintenance_id": "request", "target_pid": 123}
    if binding != "missing_generation":
        command["runtime_id"] = "current" if binding != "older_generation" else "previous"
    if binding == "older_pid":
        command["target_pid"] = 122
    assert command_action(command, runtime_id="current", pid=123, now=100) == (
        "exit" if binding == "matching" else None
    )


@pytest.mark.parametrize("action", ["exit", "pause", "restart", "reconnect"])
def test_ordinary_tray_commands_remain_compatible(action: str) -> None:
    assert command_action({"action": action, "created_at": 99}, runtime_id="current", pid=123, now=100) == action


@pytest.mark.parametrize("command", [
    [], None, {"action": []}, {"action": {}, "created_at": 99}, {"action": "unknown", "created_at": 99},
    {"action": "exit", "created_at": "bad"}, {"action": "exit", "created_at": 101},
    {"action": "exit", "created_at": 69},
])
def test_malformed_expired_and_future_commands_are_ignored(command: object) -> None:
    assert command_action(command, runtime_id="current", pid=123, now=100) is None


def test_folder_consent_requires_matching_native_response(user_layout: tuple[Path, Path]) -> None:
    program, data = user_layout
    native_user_install.configure(program, data, "development", 19080, {})
    config = load_configuration(load_installation(data))
    selected = data.parent / "Media Source"
    selected.mkdir()

    async def scenario() -> None:
        task = asyncio.create_task(native_consent.request_folder(config))
        await asyncio.sleep(0.01)
        request_path = next((data / "state/tray-requests").glob("request-*.json"))
        request = json.loads(request_path.read_text())
        assert request["expires_at"] > time.time()
        response = request_path.with_name("response-" + request["id"] + ".json")
        native_install.write_json(response, {"id": request["id"], "approved": True, "path": str(selected)})
        assert await task == selected
        assert not list((data / "state/tray-requests").glob("*.json"))
    asyncio.run(scenario())


def test_folder_denial_and_expiration_never_become_approval(user_layout: tuple[Path, Path]) -> None:
    program, data = user_layout
    native_user_install.configure(program, data, "development", 19080, {})
    config = load_configuration(load_installation(data))
    assert asyncio.run(native_consent._request(config, "confirmation", {"message": "test"}, timeout=0)) == {}
    assert not list((data / "state/tray-requests").glob("*.json"))
    with patch.object(native_consent, "_request", return_value={}):
        assert asyncio.run(native_consent.request_confirmation(config, "Confirm fingerprint")) is False


@pytest.mark.parametrize("missing_setting", [None, "DATABASE_URL", "APP_DATA_DIR", "ARTWORK_DIR", "TEMP_DIR"])
def test_legacy_adoption_preserves_storage_and_requires_new_user_pairing(
    user_layout: tuple[Path, Path], missing_setting: str | None,
) -> None:
    program, data = user_layout
    for name in native_install.STATE_DIRECTORIES:
        (data / name).mkdir(parents=True, exist_ok=True)
    # Synthetic legacy metadata avoids installing or touching any real service.
    record = native_user_install.configure(program, data, "development", 19080, {})
    record["runtime_mode"] = "legacy_service"
    native_install.write_json(data / "configuration/installation.json", record)
    recovery = data / "upgrade/synthetic-recovery"
    recovery.mkdir(parents=True)
    native_install.write_json(data / "state/user-migration.json", {
        "backup_validated": True, "drained_snapshot": True, "old_program": str(program), "recovery": str(recovery),
    })
    for name in (".env", "installation.json"):
        (recovery / name).write_bytes((data / "configuration" / name).read_bytes())
    (recovery / "app.db").write_bytes(b"preserved database sentinel")
    secret = dotenv_values(data / "configuration/.env")["APP_SECRET_KEY"]
    sentinel = data / "database/app.db"
    sentinel.write_bytes(b"preserved database sentinel")
    if missing_setting:
        configured = data / "configuration/.env"
        configured.write_text("\n".join(
            line for line in configured.read_text().splitlines() if not line.startswith(missing_setting + "=")),
            encoding="utf-8")
        before = configured.read_bytes()
        with pytest.raises(ValueError, match="required legacy storage setting"):
            native_user_install.adopt_legacy(program, data)
        assert configured.read_bytes() == before
        assert json.loads((data / "configuration/installation.json").read_text()) == record
        assert sentinel.read_bytes() == b"preserved database sentinel"
        return
    native_user_install.adopt_legacy(program, data)
    after = json.loads((data / "configuration/installation.json").read_text())
    assert after["requires_user_pairing"] is True
    assert after["storage"] == record["storage"]
    assert dotenv_values(data / "configuration/.env")["APP_SECRET_KEY"] == secret
    assert sentinel.read_bytes() == b"preserved database sentinel"
