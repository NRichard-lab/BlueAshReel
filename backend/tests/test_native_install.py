from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from conftest import TestContext
from dotenv import dotenv_values

from app import native_install
from app.config import get_config


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    program, data, media = tmp_path / "Program Files Test", tmp_path / "Program Data Test", tmp_path / "Generated Media"
    for path in (
        program / "services", program / "config", program / "runtime/python", program / "runtime/ffmpeg", media,
    ):
        path.mkdir(parents=True)
    (program / "services/WinSW.exe").write_bytes(b"test wrapper placeholder, never executed")
    (program / "runtime/python/python.exe").write_bytes(b"test runtime placeholder, never executed")
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (program / "runtime/ffmpeg" / name).write_bytes(b"test media runtime placeholder, never executed")
    (program / "config/product.json").write_text(
        '{"name":"BlueReel Development","version":"0.1.0-development"}', encoding="utf-8"
    )
    monkeypatch.setattr(native_install, "_executing_program_dir", lambda: program)
    return program, data, media


@pytest.fixture
def configured(layout: tuple[Path, Path, Path]) -> dict:
    program, data, media = layout
    with patch.object(native_install, "validate_ports"):
        return native_install.configure(program, data, "development", 18080, "127.0.0.1", [str(media)])


@pytest.fixture
def migrated(configured: dict, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    program, data = Path(configured["program_dir"]), Path(configured["data_dir"])
    backend = Path(__file__).parents[1]
    (program / "backend").mkdir()
    shutil.copy2(backend / "alembic.ini", program / "backend/alembic.ini")
    shutil.copytree(backend / "alembic", program / "backend/alembic")
    monkeypatch.syspath_prepend(str(backend.parent))
    # Installer migration normally runs in its own process. Keep this test's
    # environment/cache changes scoped so no other deployment can be selected.
    with patch.dict(os.environ, dict(os.environ), clear=True):
        try:
            native_install.migrate(data)
            yield configured
        finally:
            get_config.cache_clear()


@pytest.mark.parametrize("relation", ["equal", "data_inside_program", "program_inside_data", "relative", "drive_root"])
def test_layout_rejects_overlap_relative_and_drive_roots(tmp_path: Path, relation: str) -> None:
    program, data = tmp_path / "program", tmp_path / "data"
    if relation == "equal":
        data = program
    elif relation == "data_inside_program":
        data = program / "data"
    elif relation == "program_inside_data":
        program = data / "program"
    elif relation == "relative":
        program = Path("relative-program")
    else:
        data = Path(tmp_path.anchor)
    with pytest.raises(native_install.NativeInstallError):
        native_install.validate_layout(program, data)


@pytest.mark.parametrize("offset", [0, 1, 2])
def test_port_validation_detects_public_and_adjacent_internal_conflicts(offset: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            held.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1] - offset
        with pytest.raises(native_install.NativeInstallError, match="unavailable"):
            native_install.validate_ports(port)
        assert held.fileno() >= 0


@pytest.mark.parametrize("port", [0, 80, 1023, 65534, 65535])
def test_port_bounds_reserve_two_internal_ports(port: int) -> None:
    with pytest.raises(native_install.NativeInstallError, match="1024"):
        native_install.validate_ports(port)


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "127.0.0.2", "169.254.1.2", "::1", "localhost"])  # noqa: S104 - rejection tests
def test_listener_rejects_wildcard_public_and_unapproved_addresses(address: str) -> None:
    with pytest.raises(native_install.NativeInstallError):
        native_install.validate_bind(address)


def test_configure_separates_mutable_data_and_exact_bundled_runtimes(configured: dict) -> None:
    program, data = Path(configured["program_dir"]), Path(configured["data_dir"])
    values = dotenv_values(data / "configuration/.env", interpolate=False)
    assert configured["instance"] == "development" and configured["port"] == 18080
    assert configured["service_prefix"] == "BlueReelDevelopment"
    assert configured["bind_address"] == "127.0.0.1" and configured["strict_local"]
    assert set(native_install.STATE_DIRECTORIES).issubset(path.name for path in data.iterdir())
    for key in ("APP_DATA_DIR", "TEMP_DIR", "ARTWORK_DIR", "NATIVE_DATA_DIR"):
        assert Path(values[key]).is_relative_to(data)
    assert Path(values["DATABASE_URL"].removeprefix("sqlite:///")).is_relative_to(data)
    for key in ("FFMPEG_PATH", "FFPROBE_PATH", "NATIVE_PROGRAM_DIR", "PRODUCT_CONFIG_FILE"):
        assert Path(values[key]).is_relative_to(program)
    assert values["TRANSCODE_MODE"] == "automatic" and values["TRANSCODE_ALLOW_4K"] == "false"
    assert values["OUTBOUND_INTEGRATIONS_ENABLED"] == "false"
    assert len(values["APP_SECRET_KEY"]) == 96
    assert not (program / "database").exists() and not (program / ".env").exists()
    assert native_install.read_installation(data) == configured


def test_configure_refuses_foreign_nonempty_data_without_writes(layout: tuple[Path, Path, Path]) -> None:
    program, data, media = layout
    data.mkdir()
    sentinel = data / "existing-household-data.bin"
    sentinel.write_bytes(b"foreign test data must be preserved")
    before = sentinel.read_bytes()
    with pytest.raises(native_install.NativeInstallError, match="not an empty"):
        native_install.configure(program, data, "development", 18080, "127.0.0.1", [str(media)])
    assert sentinel.read_bytes() == before and list(data.iterdir()) == [sentinel]
    assert not (data / native_install.MARKER).exists()


def test_configure_accepts_only_empty_precreated_state_directories(layout: tuple[Path, Path, Path]) -> None:
    program, data, _media = layout
    for name in native_install.STATE_DIRECTORIES:
        (data / name).mkdir(parents=True)
    with patch.object(native_install, "validate_ports"):
        native_install.configure(program, data, "development", 18080, "127.0.0.1", [])
    assert (data / native_install.MARKER).exists()


def test_independent_instances_have_distinct_secrets_and_cookie_namespaces(configured: dict, tmp_path: Path) -> None:
    program, data = Path(configured["program_dir"]), Path(configured["data_dir"])
    second_program, second_data = tmp_path / "Other Program", tmp_path / "Other Data"
    shutil.copytree(program, second_program)
    with (
        patch.object(native_install, "validate_ports"),
        patch.object(native_install, "_executing_program_dir", return_value=second_program),
    ):
        other = native_install.configure(second_program, second_data, "stable", 8080, "127.0.0.1", [])
    first = dotenv_values(data / "configuration/.env", interpolate=False)
    second = dotenv_values(second_data / "configuration/.env", interpolate=False)
    for key in ("APP_SECRET_KEY", "SESSION_COOKIE_NAME", "CSRF_COOKIE_NAME", "SETUP_COOKIE_NAME", "DATABASE_URL"):
        assert first[key] != second[key]
    assert configured["service_prefix"] != other["service_prefix"]
    assert first["CSRF_COOKIE_NAME"] == "csrf_token_18080" and second["CSRF_COOKIE_NAME"] == "csrf_token_8080"


def test_repair_preserves_private_configuration_roots_and_database(configured: dict) -> None:
    program, data = Path(configured["program_dir"]), Path(configured["data_dir"])
    environment = data / "configuration/.env"
    initial = environment.read_bytes()
    database = data / "database/app.db"
    database.write_bytes(b"test database preservation sentinel; not migrated")
    (program / "services/WinSW.exe").write_bytes(b"new wrapper bytes for repair regression")
    with patch.object(native_install, "validate_ports") as check:
        repaired = native_install.configure(program, data, "development", 28080, "192.168.1.2", ["ignored-repair-root"])
    check.assert_called_once_with(18080, "127.0.0.1")
    assert repaired == configured and environment.read_bytes() == initial
    assert database.read_bytes() == b"test database preservation sentinel; not migrated"
    assert repaired["port"] == 18080 and repaired["bind_address"] == "127.0.0.1"
    for suffix in native_install.ROLES.values():
        assert (program / "services" / f"BlueReelDevelopment{suffix}.exe").read_bytes() == (
            program / "services/WinSW.exe"
        ).read_bytes()
    with pytest.raises(native_install.NativeInstallError, match="different installation"):
        native_install.configure(program, data, "stable", 8080, "127.0.0.1", [])
    assert environment.read_bytes() == initial


def test_winsw_service_xml_is_localservice_bounded_delayed_and_secret_free(configured: dict) -> None:
    program, data = Path(configured["program_dir"]), Path(configured["data_dir"])
    secret = dotenv_values(data / "configuration/.env", interpolate=False)["APP_SECRET_KEY"]
    for role, suffix in native_install.ROLES.items():
        path = program / "services" / f"BlueReelDevelopment{suffix}.xml"
        document = ET.parse(path).getroot()  # noqa: S314 - trusted XML generated immediately above
        assert document.findtext("serviceaccount/domain") == "NT AUTHORITY"
        assert document.findtext("serviceaccount/user") == "LocalService"
        assert "LocalSystem" not in path.read_text(encoding="utf-8")
        assert document.findtext("startmode") == "Automatic"
        assert document.findtext("delayedAutoStart") == "true"
        assert [node.attrib["action"] for node in document.findall("onfailure")] == ["restart", "restart", "none"]
        assert document.findtext("workingdirectory") == str(data)
        assert document.findtext("logpath") == str(data / "logs")
        assert document.findtext("executable") == str(program / "runtime/python/python.exe")
        assert f"--role {role}" in document.findtext("arguments")
        assert "--stop" in document.findtext("stoparguments")
        common = shlex.split(document.findtext("arguments"))
        for operation in ("start", "stop"):
            command = shlex.split(document.findtext(operation + "arguments")) + common
            assert command[:4] == ["-I", "-B", "-m", "app.native_runtime"]
            assert command.count("-m") == 1
            assert command[4:] == (["--stop"] if operation == "stop" else []) + [
                "--role", role, "--data-dir", str(data),
            ]
        assert secret not in path.read_text(encoding="utf-8")
        assert ".env" not in document.findtext("arguments")
        assert (program / "services" / f"BlueReelDevelopment{suffix}.exe").read_bytes() == (
            program / "services/WinSW.exe"
        ).read_bytes()


def test_caddy_is_loopback_same_origin_admin_off_and_no_access_log(configured: dict) -> None:
    data = Path(configured["data_dir"])
    source = (data / "configuration/Caddyfile").read_text(encoding="utf-8")
    assert "http://127.0.0.1:18080" in source
    assert "    bind 127.0.0.1\n" in source
    assert "reverse_proxy 127.0.0.1:18081" in source and "reverse_proxy 127.0.0.1:18082" in source
    assert "admin off" in source and "auto_https off" in source and "persist_config off" in source
    assert "0.0.0.0" not in source and "output file" not in source  # noqa: S104 - no wildcard listener
    assert "default-src 'self'" in source and "connect-src 'self'" in source
    native_install.render_proxy({**configured, "bind_address": "192.168.1.42"})
    lan = (data / "configuration/Caddyfile").read_text(encoding="utf-8")
    assert "http://127.0.0.1:18080, http://192.168.1.42:18080" in lan
    assert "    bind 127.0.0.1 192.168.1.42\n" in lan
    with pytest.raises(native_install.NativeInstallError):
        native_install.render_proxy({**configured, "bind_address": "0.0.0.0"})  # noqa: S104 - rejection test


def test_real_caddy_adapter_accepts_configuration_and_binds_only_requested_interfaces(configured: dict) -> None:
    executable = os.getenv("TEST_CADDY_PATH") or shutil.which("caddy")
    if not executable:
        pytest.skip("Bundled Caddy required; set TEST_CADDY_PATH")
    source = Path(configured["data_dir"]) / "configuration/Caddyfile"
    for address, expected in (
        ("127.0.0.1", {"127.0.0.1:18080"}),
        ("192.168.1.42", {"127.0.0.1:18080", "192.168.1.42:18080"}),
    ):
        native_install.render_proxy({**configured, "bind_address": address})
        result = subprocess.run(
            [executable, "adapt", "--config", str(source), "--adapter", "caddyfile"],
            capture_output=True,
            check=False,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        converted = json.loads(result.stdout)
        listeners = {
            address for server in converted["apps"]["http"]["servers"].values() for address in server["listen"]
        }
        assert listeners == expected
        assert converted["admin"]["disabled"] is True


def run_shared_backup(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
    from scripts import backup

    assert command[1:5] == ["-I", "-B", "-m", "scripts.backup"]
    with patch.object(sys, "argv", ["backup", *command[5:]]):
        code = backup.main()
    return subprocess.CompletedProcess(command, code, b"", b"")


def test_shared_migration_backup_dry_restore_and_upgrade_copy_are_isolated(migrated: dict) -> None:
    from scripts.backup_format import installed_alembic_head
    from scripts.restore_validate import validate

    program, data = Path(migrated["program_dir"]), Path(migrated["data_dir"])
    database = data / "database/app.db"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == installed_alembic_head()
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='playback_sessions'").fetchone()
    (data / "data/unit-test-state.txt").write_text("private unit test state", encoding="utf-8")
    (data / "artwork/unit-test-cache.dat").write_bytes(b"local test cache")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    private_env = (data / "configuration/.env").read_bytes()
    installation_metadata = (data / "configuration/installation.json").read_bytes()
    proxy_configuration = (data / "configuration/Caddyfile").read_bytes()
    (data / "configuration/do-not-include.log").write_text("private diagnostic omitted", encoding="utf-8")
    with patch.object(native_install.subprocess, "run", side_effect=run_shared_backup) as runner:
        archive = native_install.backup(data)
    assert runner.call_args.args[0][0] == str(program / "runtime/python/python.exe")
    assert dotenv_values(data / "configuration/.env")["APP_SECRET_KEY"] not in " ".join(runner.call_args.args[0])
    assert archive.is_relative_to(data / "backups")
    summary = validate(archive)
    assert summary["has_environment"] and summary["has_application_data"] and summary["has_artwork"]
    assert summary["native_configuration"]["complete"] is True
    assert summary["native_configuration"]["restore_mode"] == "manual_offline_only"
    with zipfile.ZipFile(archive) as contents:
        assert contents.read("configuration/native/installation.json") == installation_metadata
        assert contents.read("configuration/native/Caddyfile") == proxy_configuration
        assert not any(name.endswith("do-not-include.log") for name in contents.namelist())
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert (data / "configuration/.env").read_bytes() == private_env
    assert (data / "configuration/installation.json").read_bytes() == installation_metadata
    assert (data / "configuration/Caddyfile").read_bytes() == proxy_configuration
    assert json.loads((data / "state/last-backup.json").read_text())["validated"]
    with patch.object(native_install.subprocess, "run", side_effect=run_shared_backup):
        native_install.prepare_upgrade(data)
    recovery_record = json.loads((data / "state/upgrade.json").read_text())
    assert recovery_record["automatic_rollback"] is False
    recovery = data / "upgrade" / recovery_record["recovery_directory"]
    assert (recovery / "program/services/WinSW.exe").read_bytes() == (program / "services/WinSW.exe").read_bytes()
    assert (recovery / "configuration/.env").read_bytes() == private_env
    assert (data / "state/maintenance").exists()
    assert database.exists() and (data / "configuration/.env").read_bytes() == private_env


def test_failed_backup_aborts_upgrade_and_removes_maintenance_flag(migrated: dict) -> None:
    data = Path(migrated["data_dir"])
    before = (data / "database/app.db").read_bytes()
    with (
        patch.object(native_install, "backup", side_effect=native_install.NativeInstallError("Backup failed")),
        pytest.raises(native_install.NativeInstallError, match="Backup failed"),
    ):
        native_install.prepare_upgrade(data)
    assert not (data / "state/maintenance").exists()
    assert not (data / "state/upgrade.json").exists()
    assert (data / "database/app.db").read_bytes() == before


def test_health_does_not_use_environment_proxy_and_clears_only_this_instances_maintenance(configured: dict) -> None:
    data = Path(configured["data_dir"])
    maintenance = data / "state/maintenance"
    maintenance.write_text("maintenance")
    response = Mock(status=200)
    response.read.return_value = b'{"status":"ready"}'
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    with patch.object(native_install.urllib.request, "build_opener", return_value=opener) as build:
        assert native_install.health(data, seconds=1)
    assert build.call_args.args[0].proxies == {}
    opener.open.assert_called_once_with("http://127.0.0.1:18080/api/v1/health/ready", timeout=2)
    assert not maintenance.exists()


def test_native_setup_login_and_logout_preserve_other_deployments_cookie_namespace(context: TestContext) -> None:
    context.config.session_cookie_name = "bluereeldevelopment_session_18080"
    context.config.csrf_cookie_name = "csrf_token_18080"
    context.config.setup_cookie_name = "bluereeldevelopment_setup_18080"
    unrelated = {
        "media_session": "existing-docker-session-sentinel",
        "csrf_token": "existing-docker-csrf-sentinel",
        "bluereel_setup": "existing-docker-setup-sentinel",
    }
    for name, value in unrelated.items():
        context.client.cookies.set(name, value, domain="testserver.local", path="/")
    setup = context.client.post("/api/v1/setup/session")
    assert setup.status_code == 200
    assert set(setup.cookies) == {context.config.csrf_cookie_name, context.config.setup_cookie_name}
    token = setup.json()["csrf_token"]
    owner = context.client.post(
        "/api/v1/setup/owner",
        headers={"X-CSRF-Token": token},
        json={"username": "owner", "password": "correct horse battery staple"},
    )
    assert owner.status_code == 201, owner.text
    assert context.client.get("/api/v1/auth/me").status_code == 200
    assert context.config.session_cookie_name in owner.cookies
    assert context.client.cookies.get(context.config.setup_cookie_name) is None
    for name, value in unrelated.items():
        assert context.client.cookies[name] == value
    logged_out = context.client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": owner.json()["csrf_token"]})
    assert logged_out.status_code == 204
    deleted = {value.partition("=")[0] for value in logged_out.headers.get_list("set-cookie")}
    assert deleted == {context.config.session_cookie_name, context.config.csrf_cookie_name}
    assert context.client.get("/api/v1/auth/me").status_code == 401
    for name, value in unrelated.items():
        assert context.client.cookies[name] == value
    login = context.client.post(
        "/api/v1/auth/login", json={"username": "owner", "password": "correct horse battery staple"}
    )
    assert login.status_code == 200
    assert set(login.cookies) == {context.config.session_cookie_name, context.config.csrf_cookie_name}
    assert login.cookies[context.config.csrf_cookie_name] == login.json()["csrf_token"]
    assert context.client.get("/api/v1/auth/me").status_code == 200


def test_changing_media_preserves_secret_and_storage_and_rejects_unsafe_roots(configured: dict) -> None:
    data = Path(configured["data_dir"])
    before = dict(dotenv_values(data / "configuration/.env"))
    next_root = data.parent / "Another generated media root"
    next_root.mkdir()
    native_install.change_media(data, [str(next_root)])
    after = dict(dotenv_values(data / "configuration/.env"))
    assert {key: value for key, value in after.items() if key != "MEDIA_ROOT_DEFINITIONS"} == {
        key: value for key, value in before.items() if key != "MEDIA_ROOT_DEFINITIONS"
    }
    assert json.loads(after["MEDIA_ROOT_DEFINITIONS"])[0]["path"] == str(next_root)
    intact = (data / "configuration/.env").read_bytes()
    for unsafe in (["relative-root"], [str(data)], [str(next_root), str(next_root)], [str(next_root / "../escape")]):
        with pytest.raises(native_install.NativeInstallError):
            native_install.change_media(data, unsafe)
        assert (data / "configuration/.env").read_bytes() == intact


def test_network_maintenance_is_explicit_private_and_preserves_port_and_secret(configured: dict) -> None:
    data = Path(configured["data_dir"])
    original_env = (data / "configuration/.env").read_bytes()
    with patch.object(native_install, "validate_ports") as probe:
        native_install.change_network(data, "192.168.50.10")
        probe.assert_called_once_with(18080, "192.168.50.10")
    assert "bind 127.0.0.1 192.168.50.10" in (data / "configuration/Caddyfile").read_text()
    with patch.object(native_install, "validate_ports"):
        native_install.change_network(data, "127.0.0.1")
    assert "192.168.50.10" not in (data / "configuration/Caddyfile").read_text()
    assert (data / "configuration/.env").read_bytes() == original_env
    with pytest.raises(native_install.NativeInstallError):
        native_install.change_network(data, "0.0.0.0")  # noqa: S104 - reject wildcard exposure


@pytest.mark.parametrize("operation", ["backup", "migrate", "prepare_upgrade"])
def test_elevated_maintenance_rejects_metadata_selected_executable_before_work(
    configured: dict, operation: str,
) -> None:
    data = Path(configured["data_dir"])
    hostile_program = data.parent / "service-controlled-program"
    hostile_program.mkdir()
    metadata = {**configured, "program_dir": str(hostile_program)}
    native_install.write_json(data / "configuration/installation.json", metadata)
    with (
        patch.object(native_install.subprocess, "run") as spawn,
        patch("alembic.command.upgrade") as migration,
        patch.object(native_install.shutil, "copytree") as copy,
        pytest.raises(native_install.NativeInstallError, match="identity"),
    ):
        getattr(native_install, operation)(data)
    spawn.assert_not_called()
    migration.assert_not_called()
    copy.assert_not_called()
    assert not (data / "state/maintenance").exists()
    assert list(hostile_program.iterdir()) == []


@pytest.mark.parametrize("operation", ["backup", "migrate", "prepare_upgrade"])
@pytest.mark.parametrize("field", [
    "DATABASE_URL", "APP_DATA_DIR", "TEMP_DIR", "ARTWORK_DIR", "NATIVE_PROGRAM_DIR",
    "FFMPEG_PATH", "FFPROBE_PATH", "PRODUCT_CONFIG_FILE", "OUTBOUND_INTEGRATIONS_ENABLED",
])
def test_elevated_maintenance_rejects_redirected_storage_or_runtime_before_work(
    configured: dict, operation: str, field: str,
) -> None:
    data = Path(configured["data_dir"])
    external = data.parent / "external-do-not-touch"
    external.mkdir()
    sentinel = external / "app.db"
    sentinel.write_bytes(b"external synthetic contents preserved")
    value = str(external)
    if field == "DATABASE_URL":
        value = f"sqlite:///{sentinel.as_posix()}"
    elif field == "OUTBOUND_INTEGRATIONS_ENABLED":
        value = "true"
    environment = data / "configuration/.env"
    with environment.open("a", encoding="utf-8") as output:
        output.write(f"{field}={json.dumps(value)}\n")
    with (
        patch.object(native_install.subprocess, "run") as spawn,
        patch("alembic.command.upgrade") as migration,
        patch.object(native_install.shutil, "copytree") as copy,
        pytest.raises(native_install.NativeInstallError, match="unsafe"),
    ):
        getattr(native_install, operation)(data)
    spawn.assert_not_called()
    migration.assert_not_called()
    copy.assert_not_called()
    assert sentinel.read_bytes() == b"external synthetic contents preserved"
    assert list(external.iterdir()) == [sentinel]
    assert not (data / "state/maintenance").exists()


@pytest.mark.parametrize("relative", [
    ".bluereel-native-instance", "configuration/installation.json", "configuration/.env", "database/app.db",
    "database/app.db-wal", "database/app.db-shm", "database/app.db-journal",
])
def test_elevated_maintenance_refuses_hardlinked_private_inputs(configured: dict, relative: str) -> None:
    data = Path(configured["data_dir"])
    target = data / relative
    if not target.exists():
        target.write_bytes(b"test database")
    external = data.parent / "external-hardlink-target"
    external.write_bytes(target.read_bytes())
    target.unlink()
    target.hardlink_to(external)
    before = external.read_bytes()
    with (
        patch.object(native_install.subprocess, "run") as spawn,
        pytest.raises(native_install.NativeInstallError),
    ):
        native_install.backup(data)
    spawn.assert_not_called()
    assert external.read_bytes() == before


def test_configure_refuses_program_not_providing_this_installer(layout: tuple[Path, Path, Path]) -> None:
    _program, data, _media = layout
    other = data.parent / "foreign-program"
    other.mkdir()
    with pytest.raises(native_install.NativeInstallError, match="executing protected"):
        native_install.configure(other, data, "development", 18080, "127.0.0.1", [])
    assert not data.exists()


def test_elevated_state_writes_replace_links_without_modifying_external_targets(configured: dict) -> None:
    data = Path(configured["data_dir"])
    external = data.parent / "external-write-sentinel"
    external.write_bytes(b"external contents stay unchanged")
    for relative in ("state/last-backup.json", "state/last-backup.json.new", "state/maintenance"):
        (data / relative).hardlink_to(external)
    native_install.write_json(data / "state/last-backup.json", {"validated": True})
    native_install._atomic_text(data / "state/maintenance", "maintenance\n")
    assert external.read_bytes() == b"external contents stay unchanged"
    assert json.loads((data / "state/last-backup.json").read_text()) == {"validated": True}
    assert (data / "state/maintenance").read_text() == "maintenance\n"
    assert not list((data / "state").glob(".bluereel-*"))
