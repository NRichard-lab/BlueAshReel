"""System-settings-only per-user configuration and safe retained-data migration."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app import native_install
from app.native_runtime import activate_configuration, load_configuration, load_installation
from app.remote.storage import protect_directory
from app.services.paths import assert_no_link_components
from app.services.process_supervisor import lock_file, unlock_file

STORAGE_DEFAULTS = {"database": "database", "artwork": "artwork", "temp": "temp", "backups": "backups",
                    "logs": "logs", "app_data": "data"}


def _filesystem_path(path: Path) -> Path:
    """Use extended Win32 paths internally after ordinary-path validation."""
    if os.name != "nt":
        return path
    value = str(path.absolute())
    return Path("\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value)


def _no_link_tree(path: Path) -> None:
    tree = _filesystem_path(path)
    assert_no_link_components(tree)
    for child in tree.rglob("*"):
        assert_no_link_components(child)


def validate_storage(program: Path, data: Path, requested: dict[str, str]) -> dict[str, str]:
    if set(requested) - set(STORAGE_DEFAULTS):
        raise ValueError("Unknown advanced storage setting")
    locations: dict[str, str] = {}
    for name, default in STORAGE_DEFAULTS.items():
        candidate = Path(requested.get(name) or data / default)
        _, path = native_install.validate_layout(program, candidate)
        if path == data or data.is_relative_to(path):
            raise ValueError("Choose a separate dedicated storage subdirectory")
        for reserved in (data / name for name in ("configuration", "state", "upgrade", "remote-control",
                                                   "remote-control-user", "remote-identity")):
            if path == reserved or path.is_relative_to(reserved) or reserved.is_relative_to(path):
                raise ValueError("Mutable storage cannot overlap private configuration or runtime state")
        for previous in locations.values():
            other = Path(previous)
            if path == other or path.is_relative_to(other) or other.is_relative_to(path):
                raise ValueError("Database, artwork, temp, backups and logs must use separate directories")
        locations[name] = str(path)
    return locations


def configure(program: Path, data: Path, instance: str, port: int,
              requested: dict[str, str], *, max_processes: int = 2, threads: int = 2) -> dict[str, Any]:
    program, data = native_install.validate_layout(program, data)
    existing = (data / native_install.MARKER).exists()
    if existing:
        record = native_install.read_installation(data)
        if record["instance"] != instance:
            raise ValueError("Existing data belongs to another installation channel")
        if record.get("runtime_mode") != "per_user":
            raise ValueError("Use the validated service-to-user migration before updating this existing installation")
        # All previous locations, secrets, identity and resource settings survive repair.
        return record
    storage = validate_storage(program, data, requested)
    if not 1 <= max_processes <= 8 or not 1 <= threads <= 16:
        raise ValueError("Resource limits are outside supported bounds")
    for value in storage.values():
        path = Path(value)
        if path.exists() and any(path.iterdir()):
            raise ValueError("New storage directories must be empty; no existing contents were changed")
    # Reject foreign contents before permission changes, marker creation, or any write.
    if data.exists() and any(
        child.name not in native_install.STATE_DIRECTORIES or not child.is_dir() or any(child.iterdir())
        for child in data.iterdir()
    ):
        raise ValueError("The new application-data directory contains existing or foreign data")
    protect_directory(data)
    record = native_install.configure(program, data, instance, port, "127.0.0.1", [], runtime_mode="per_user")
    for value in storage.values():
        protect_directory(Path(value))
    for name in ("state/tray-requests", "remote-control", "remote-identity"):
        protect_directory(data / name)
    values = dict(dotenv_values(data / "configuration/.env", interpolate=False))
    values.update({"APP_DATA_DIR": storage["app_data"], "ARTWORK_DIR": storage["artwork"],
                   "TEMP_DIR": storage["temp"],
                   "DATABASE_URL": "sqlite:///" + (Path(storage["database"]) / "app.db").as_posix(),
                   "REMOTE_CONTROL_DIR": str(data / "remote-control"), "TRANSCODE_MAX_PROCESSES": str(max_processes),
                   "TRANSCODE_THREADS": str(threads)})
    native_install._atomic_text(data / "configuration/.env", "".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}\n" for key, value in values.items() if value is not None))
    record.update({"schema_version": 2, "storage": storage, "runtime_mode": "per_user", "strict_local": True})
    native_install.write_json(data / "configuration/installation.json", record)
    return record


def migrate_database(data: Path) -> None:
    from alembic.config import Config

    from alembic import command

    installation = load_installation(data)
    config = load_configuration(installation)
    activate_configuration(installation, config)
    settings = Config(str(installation.program_dir / "backend/alembic.ini"))
    settings.set_main_option("script_location", str(installation.program_dir / "backend/alembic"))
    settings.set_main_option("sqlalchemy.url", config.database_url.replace("%", "%%"))
    command.upgrade(settings, "head")


def snapshot(data: Path, *, include_program: bool = False) -> Path:
    record = native_install.read_installation(data)
    installation = load_installation(data)
    config = load_configuration(installation)
    output = Path(record.get("storage", {}).get("backups", data / "backups"))
    stamp = datetime.now(UTC).strftime("before-user-update-%Y%m%dT%H%M%S%fZ")
    recovery = output / stamp
    assert_no_link_components(output)
    recovery.mkdir(parents=True)
    assert_no_link_components(recovery)
    database = Path(config.database_url.removeprefix("sqlite:///"))
    if database.exists():
        native_install._private_file(database)
        with (sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source,
              sqlite3.connect(recovery / "app.db") as target):
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("The Agent database backup failed validation")
    # Copy only private configuration; identity remains at its original location.
    for name in ("installation.json", ".env"):
        source = data / "configuration" / name
        assert_no_link_components(source)
        native_install._atomic_text(recovery / name, source.read_text(encoding="utf-8-sig"))
    approved_roots = config.app_data_dir / "remote-roots.json"
    if approved_roots.exists():
        native_install._private_file(approved_roots)
        native_install._atomic_text(recovery / "remote-roots.json", approved_roots.read_text(encoding="utf-8"))
    if include_program:
        _no_link_tree(installation.program_dir)
        shutil.copytree(_filesystem_path(installation.program_dir), _filesystem_path(recovery / "program"))
    native_install.write_json(data / "state/user-upgrade.json", {
        "backup": str(recovery), "validated": True, "program_retained": include_program,
    })
    return recovery


def restore_snapshot(data: Path) -> Path:
    """Restore only this stopped user's validated snapshot, including advanced DB paths."""
    record = native_install.read_installation(data)
    installation = load_installation(data)
    config = load_configuration(installation)
    ledger_file = data / "state/user-upgrade.json"
    native_install._private_file(ledger_file)
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    recovery = Path(ledger.get("backup", ""))
    backups = Path(record["storage"]["backups"])
    if (ledger.get("validated") is not True or not recovery.is_absolute()
        or recovery.parent != backups or not recovery.name.startswith("before-user-update-")):
        raise ValueError("The recovery snapshot does not belong to this installation")
    assert_no_link_components(recovery)
    for name in ("app.db", ".env", "installation.json"):
        native_install._private_file(recovery / name)
    saved = json.loads((recovery / "installation.json").read_text(encoding="utf-8-sig"))
    identity_fields = ("instance", "service_prefix", "program_dir", "data_dir", "storage")
    if any(saved.get(name) != record.get(name) for name in identity_fields):
        raise ValueError("The recovery snapshot would change this installation's identity or storage")
    if ledger.get("program_retained") is True:
        _no_link_tree(recovery / "program")
        _no_link_tree(installation.program_dir)
    database = Path(config.database_url.removeprefix("sqlite:///"))
    ownership = lock_file(installation.state_dir / "tray-runtime.lock")
    try:
        with sqlite3.connect(recovery / "app.db") as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("The recovery database failed validation")
            if database.exists():
                native_install._private_file(database)
                with (sqlite3.connect(database) as current,
                      sqlite3.connect(recovery / "failed-user-app.db") as preserved):
                    current.backup(preserved)
            with sqlite3.connect(database) as target:
                source.backup(target)
        for name in ("installation.json", ".env"):
            native_install._atomic_text(data / "configuration" / name,
                                       (recovery / name).read_text(encoding="utf-8-sig"))
        if (recovery / "remote-roots.json").exists():
            native_install._private_file(recovery / "remote-roots.json")
            native_install._atomic_text(config.app_data_dir / "remote-roots.json",
                                       (recovery / "remote-roots.json").read_text(encoding="utf-8"))
    finally:
        unlock_file(ownership)
    return recovery


def adopt_legacy(program: Path, data: Path, instance: str = "development") -> None:
    """Called after elevated helper validated/backed up and stopped old services.

    Preserve all configured storage and durable data. Service-scope DPAPI keys
    are retained for rollback; they are never silently transplanted between users.
    """
    program, data = native_install.validate_layout(program, data)
    if program != native_install._executing_program_dir():
        raise ValueError("Unexpected program identity")
    for path in (data / native_install.MARKER, data / "configuration/installation.json",
                 data / "configuration/.env", data / "state/user-migration.json"):
        native_install._private_file(path)
    record = json.loads((data / "configuration/installation.json").read_text(encoding="utf-8-sig"))
    expected = native_install.INSTANCES[instance][1]
    if (record["instance"] != instance or record["service_prefix"] != expected
        or (data / native_install.MARKER).read_text().rstrip("\r\n") != expected):
        raise ValueError("Legacy data belongs to another installation")
    if record.get("runtime_mode") == "per_user":
        return
    if Path(record["data_dir"]) != data or not (data / "state/user-migration.json").is_file():
        raise ValueError("Validated legacy migration preparation is missing")
    ledger = json.loads((data / "state/user-migration.json").read_text(encoding="utf-8-sig"))
    recovery = Path(ledger.get("recovery", ""))
    if (ledger.get("backup_validated") is not True or ledger.get("drained_snapshot") is not True
        or ledger.get("old_program") != record["program_dir"] or not recovery.is_relative_to(data / "upgrade")):
        raise ValueError("The validated migration recovery record does not match this instance")
    assert_no_link_components(recovery)
    for name in ("app.db", ".env", "installation.json"):
        native_install._private_file(recovery / name)
    values = dict(dotenv_values(data / "configuration/.env", interpolate=False))
    storage = {
        "database": str(Path(values["DATABASE_URL"].removeprefix("sqlite:///")).parent),
        "app_data": values["APP_DATA_DIR"], "artwork": values["ARTWORK_DIR"], "temp": values["TEMP_DIR"],
        "backups": str(data / "backups"), "logs": str(data / "logs"),
    }
    storage = validate_storage(program, data, storage)
    values.update({"NATIVE_PROGRAM_DIR": str(program), "PRODUCT_CONFIG_FILE": str(program / "config/product.json"),
                   "FFMPEG_PATH": str(program / "runtime/ffmpeg/ffmpeg.exe"),
                   "FFPROBE_PATH": str(program / "runtime/ffmpeg/ffprobe.exe"),
                   "REMOTE_CONTROL_DIR": str(data / "remote-control-user")})
    # Existing service identity is DPAPI-bound to its former service account.
    # Retain it untouched and require fresh confirmed Portal pairing for this user.
    record.update({"program_dir": str(program), "runtime_mode": "per_user", "schema_version": 2,
                   "bind_address": "127.0.0.1", "storage": storage, "requires_user_pairing": True})
    for name in ("state/tray-requests", "remote-control-user", "remote-identity"):
        path = data / name
        assert_no_link_components(path.parent)
        path.mkdir(parents=True, exist_ok=True)
        assert_no_link_components(path)
    native_install._atomic_text(data / "configuration/.env", "".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}\n" for key, value in values.items() if value is not None))
    native_install.write_json(data / "configuration/installation.json", record)


def main() -> int:
    parser = argparse.ArgumentParser(description="Blue Ash Reel per-user runtime installation")
    parser.add_argument("command", choices=("configure", "adopt-legacy", "migrate", "backup", "prepare-upgrade",
                                            "restore", "health"))
    parser.add_argument("--program-dir", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--instance", choices=("development", "stable"), default="development")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--max-processes", type=int, default=2)
    parser.add_argument("--threads", type=int, default=2)
    for name in STORAGE_DEFAULTS:
        parser.add_argument("--" + name.replace("_", "-") + "-dir")
    args = parser.parse_args()
    try:
        if args.command == "configure":
            if args.program_dir is None:
                raise ValueError("Program location is required")
            configure(args.program_dir, args.data_dir, args.instance, args.port,
                      {name: getattr(args, name + "_dir") for name in STORAGE_DEFAULTS if getattr(args, name + "_dir")},
                      max_processes=args.max_processes, threads=args.threads)
        elif args.command == "adopt-legacy":
            if args.program_dir is None:
                raise ValueError("Program location is required")
            adopt_legacy(args.program_dir, args.data_dir, args.instance)
        elif args.command == "migrate":
            migrate_database(args.data_dir)
        elif args.command == "backup":
            snapshot(args.data_dir)
        elif args.command == "prepare-upgrade":
            snapshot(args.data_dir, include_program=True)
        elif args.command == "restore":
            restore_snapshot(args.data_dir)
        elif not native_install.health(args.data_dir):
            raise ValueError("Agent did not become healthy")
        return 0
    except Exception:
        print("User-mode installation failed; existing Agent data and recovery backups were preserved.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
