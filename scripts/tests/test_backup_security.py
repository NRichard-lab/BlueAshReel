"""Hostile-archive and state-path regression tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup, restore_validate
from scripts.backup_format import (
    ARCHIVE_FORMAT,
    EXPECTED_ALEMBIC_HEAD,
    MANIFEST_VERSION,
    NATIVE_CONFIG_MEMBERS,
    PHASE1_REVISION,
    PHASE1_TABLES,
    REQUIRED_APPLICATION_TABLES,
    BackupFormatError,
    _head_from_migration_directory,
    validate_database,
)

Validator = Callable[[Path], object]


@pytest.mark.parametrize("name", ["state with spaces.db", "state #fragment.db", "state %20 literal.db", "state ?query.db"])
def test_sqlite_backup_and_validation_escape_uri_metacharacters(tmp_path: Path, name: str) -> None:
    if os.name == "nt" and "?" in name:
        pytest.skip("Question marks are illegal Windows filename characters")
    source = tmp_path / name
    source.write_bytes(_database_bytes(tmp_path))
    destination = tmp_path / ("backup # escaped " + name)
    original = source.read_bytes()
    assert validate_database(source).alembic_revision == EXPECTED_ALEMBIC_HEAD
    backup.online_sqlite_backup(source, destination, EXPECTED_ALEMBIC_HEAD)
    assert validate_database(destination).alembic_revision == EXPECTED_ALEMBIC_HEAD
    assert source.read_bytes() == original
    assert "%23" in (tmp_path / "#").resolve().as_uri()


def test_backup_tree_rejects_reparse_attribute_before_copying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination = tmp_path / "source", tmp_path / "copied"
    source.mkdir()
    hidden_link = source / "linked-child"
    hidden_link.mkdir()
    original_lstat = Path.lstat

    def attributes(path: Path, *args, **kwargs):
        if path == hidden_link:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", attributes)
    with pytest.raises(backup.BackupError, match="reparse"):
        backup.copy_tree(source, destination)
    assert not destination.exists()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows junction regression")
def test_backup_refuses_junction_source_children_and_ancestors_without_source_changes(tmp_path: Path) -> None:
    source, target, destination = tmp_path / "source", tmp_path / "external-test-data", tmp_path / "output"
    source.mkdir()
    (target / "nested").mkdir(parents=True)
    sentinel = target / "nested/private.txt"
    sentinel.write_bytes(b"synthetic external data must not be copied or modified")
    linked = source / "junction"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(linked), str(target)],
        capture_output=True, timeout=10, check=False,
    )
    if result.returncode:
        pytest.skip("This Windows host cannot create test junctions")
    for selected in (source, linked, linked / "nested"):
        with pytest.raises(backup.BackupError, match="reparse"):
            backup.copy_tree(selected, destination)
        assert not destination.exists()
    database = target / "nested/database.db"
    database.write_bytes(_database_bytes(tmp_path))
    with pytest.raises(BackupFormatError, match="reparse"):
        validate_database(linked / "nested/database.db")
    with pytest.raises(backup.BackupError, match="reparse"):
        backup.resolve_setting(str(linked / "nested"), "UNUSED_TEST_SETTING", {}, "", tmp_path)
    assert sentinel.read_bytes() == b"synthetic external data must not be copied or modified"


def test_backup_copies_regular_local_tree_without_changing_sources(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "copy"
    (source / "nested").mkdir(parents=True)
    original = source / "nested/state.txt"
    original.write_bytes(b"local state")
    backup.copy_tree(source, destination)
    assert (destination / "nested/state.txt").read_bytes() == original.read_bytes() == b"local state"


def test_partial_backup_is_private_from_creation_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "example.txt").write_text("synthetic configuration", encoding="utf-8")
    destination = tmp_path / ".archive.partial"
    original_open = os.open
    opened: list[tuple[int, int]] = []

    def checked_open(path, flags, mode=0o777):
        opened.append((flags, mode))
        return original_open(path, flags, mode)

    monkeypatch.setattr(backup.os, "open", checked_open)
    backup.write_private_archive(stage, destination)
    assert opened == [(os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)]
    if os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600
    content = destination.read_bytes()
    with pytest.raises(FileExistsError):
        backup.write_private_archive(stage, destination)
    assert destination.read_bytes() == content


def test_new_backup_tool_accepts_exact_installed_phase1_schema(tmp_path: Path) -> None:
    database = tmp_path / "old-installed.db"
    with sqlite3.connect(database) as connection:
        for table in sorted(PHASE1_TABLES):
            if table == "alembic_version":
                connection.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO alembic_version VALUES (?)", (PHASE1_REVISION,))
            else:
                connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
    assert validate_database(database, PHASE1_REVISION).alembic_revision == PHASE1_REVISION
    with pytest.raises(RuntimeError):
        validate_database(database)


def _database_bytes(
    temporary_path: Path,
    *,
    revision: str = EXPECTED_ALEMBIC_HEAD,
    omitted_table: str | None = None,
) -> bytes:
    database = temporary_path / "fixture.db"
    connection = sqlite3.connect(database)
    try:
        for table in sorted(REQUIRED_APPLICATION_TABLES):
            if table == omitted_table:
                continue
            if table == "alembic_version":
                connection.execute(
                    "CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"
                )
                connection.execute(
                    "INSERT INTO alembic_version (version_num) VALUES (?)", (revision,)
                )
            else:
                connection.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
        connection.commit()
    finally:
        connection.close()
    return database.read_bytes()


def _manifest_record(name: str, payload: bytes) -> dict[str, object]:
    return {
        "path": name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _archive(
    path: Path,
    database: bytes,
    *,
    extra_members: list[tuple[str, bytes]] | None = None,
    duplicate_record: bool = False,
    manifest_revision: str = EXPECTED_ALEMBIC_HEAD,
) -> Path:
    product = b'{"name":"Test Product"}\n'
    members = [
        ("database/app.db", database),
        ("configuration/product/product.json", product),
    ]
    records = [_manifest_record(name, payload) for name, payload in members]
    if duplicate_record:
        records.append(records[0].copy())
    manifest = {
        "format": ARCHIVE_FORMAT,
        "format_version": MANIFEST_VERSION,
        "database_revision": manifest_revision,
        "created_at": "2026-09-01T00:00:00Z",
        "files": records,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
        for name, payload in extra_members or []:
            archive.writestr(name, payload)
        archive.writestr("manifest.json", json.dumps(manifest).encode())
    return path


@pytest.fixture(params=[backup.validate_archive, restore_validate.validate])
def validator(request: pytest.FixtureRequest) -> Validator:
    return request.param


def test_valid_archive_passes_both_entrypoints(
    tmp_path: Path, validator: Validator
) -> None:
    archive = _archive(tmp_path / "valid.zip", _database_bytes(tmp_path))
    validator(archive)


def test_duplicate_zip_member_is_rejected(tmp_path: Path, validator: Validator) -> None:
    archive_path = _archive(
        tmp_path / "duplicate-member.zip", _database_bytes(tmp_path)
    )
    with zipfile.ZipFile(archive_path, "a") as archive, warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        archive.writestr("database/app.db", b"duplicate")
    with pytest.raises(RuntimeError, match="duplicate ZIP members"):
        validator(archive_path)


def test_unsafe_member_path_is_rejected(tmp_path: Path, validator: Validator) -> None:
    archive = _archive(
        tmp_path / "unsafe.zip",
        _database_bytes(tmp_path),
        extra_members=[("../escape", b"no")],
    )
    with pytest.raises(RuntimeError, match="unsafe member path"):
        validator(archive)


def test_unmanifested_member_is_rejected(tmp_path: Path, validator: Validator) -> None:
    archive = _archive(
        tmp_path / "extra.zip",
        _database_bytes(tmp_path),
        extra_members=[("unexpected.txt", b"no")],
    )
    with pytest.raises(RuntimeError, match="unmanifested member"):
        validator(archive)


def test_duplicate_manifest_record_is_rejected(
    tmp_path: Path, validator: Validator
) -> None:
    archive = _archive(
        tmp_path / "duplicate-record.zip",
        _database_bytes(tmp_path),
        duplicate_record=True,
    )
    with pytest.raises(RuntimeError, match="duplicate file record"):
        validator(archive)


def test_missing_core_table_is_rejected(tmp_path: Path, validator: Validator) -> None:
    archive = _archive(
        tmp_path / "missing-table.zip",
        _database_bytes(tmp_path, omitted_table="users"),
    )
    with pytest.raises(
        RuntimeError, match="missing required application tables: users"
    ):
        validator(archive)


def test_wrong_database_revision_is_rejected(
    tmp_path: Path, validator: Validator
) -> None:
    archive = _archive(
        tmp_path / "wrong-head.zip",
        _database_bytes(tmp_path, revision="unexpected"),
    )
    with pytest.raises(RuntimeError, match=f"must be {EXPECTED_ALEMBIC_HEAD}"):
        validator(archive)


def test_wrong_manifest_revision_is_rejected(
    tmp_path: Path, validator: Validator
) -> None:
    archive = _archive(
        tmp_path / "wrong-manifest-head.zip",
        _database_bytes(tmp_path),
        manifest_revision="unexpected",
    )
    with pytest.raises(RuntimeError, match=f"Alembic head {EXPECTED_ALEMBIC_HEAD}"):
        validator(archive)


def test_archive_prefix_is_safe_and_product_derived() -> None:
    assert (
        backup.archive_prefix_from_name("M\u00fd Fancy / App") == "my-fancy-app-backup-"
    )
    assert backup.archive_prefix_from_name("\u96ea") == "application-backup-"


def test_nested_state_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(backup.BackupError, match="must not overlap"):
        backup.assert_isolated_paths(
            {
                "application data": tmp_path / "data",
                "database": tmp_path / "data" / "database",
            }
        )


def test_backup_main_rejects_nested_output_before_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "product.json").write_text('{"name":"Test Product"}\n', encoding="utf-8")
    data = tmp_path / "data"
    output = data / "backups"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backup.py",
            "--database",
            str(tmp_path / "database" / "app.db"),
            "--data",
            str(data),
            "--artwork",
            str(tmp_path / "artwork"),
            "--temporary",
            str(tmp_path / "temporary"),
            "--config",
            str(config),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(backup.BackupError, match="must not overlap"):
        backup.main()
    assert not output.exists()


def test_sibling_state_paths_are_allowed(tmp_path: Path) -> None:
    backup.assert_isolated_paths(
        {
            "application data": tmp_path / "data",
            "database": tmp_path / "database",
            "backup output": tmp_path / "backups",
        }
    )


def test_backend_entrypoint_disables_forwarded_header_trust() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    entrypoint = (repository_root / "docker" / "backend-entrypoint.sh").read_text(
        encoding="utf-8"
    )
    assert "--no-proxy-headers" in entrypoint
    assert "--forwarded-allow-ips" not in entrypoint


def test_expected_revision_matches_the_migration_head() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    revisions: set[str] = set()
    parents: set[str] = set()
    for migration in (repository_root / "backend" / "alembic" / "versions").glob(
        "*.py"
    ):
        source = migration.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = "([0-9a-f]+)"$', source, re.MULTILINE)
        parent = re.search(
            r'^down_revision: str \| None = (?:"([0-9a-f]+)"|None)$',
            source,
            re.MULTILINE,
        )
        assert revision is not None
        assert parent is not None
        revisions.add(revision.group(1))
        if parent.group(1):
            parents.add(parent.group(1))
    assert revisions.difference(parents) == {EXPECTED_ALEMBIC_HEAD}


def test_installed_migration_head_discovery_follows_the_chain(tmp_path: Path) -> None:
    migrations = tmp_path / "versions"
    migrations.mkdir()
    (migrations / "first.py").write_text(
        'revision: str = "first"\ndown_revision: str | None = None\n',
        encoding="utf-8",
    )
    (migrations / "second.py").write_text(
        'revision: str = "second"\ndown_revision: str | None = "first"\n',
        encoding="utf-8",
    )
    assert _head_from_migration_directory(migrations) == "second"


def test_both_bootstrap_variants_contain_nested_path_guards() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    powershell = (repository_root / "scripts" / "bootstrap.ps1").read_text(
        encoding="utf-8"
    )
    posix = (repository_root / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "$leftPath.StartsWith($rightPrefix" in powershell
    assert "$rightPath.StartsWith($leftPrefix" in powershell
    assert 'case "$resolved/" in "$existing_path/"*' in posix
    assert 'case "$existing_path/" in "$resolved/"*' in posix


def _native_metadata() -> dict[str, object]:
    return {
        "schema_version": 1, "instance": "development", "service_prefix": "BlueReelDevelopment",
        "program_dir": "C:/Program Files/BlueReel Development", "data_dir": "C:/ProgramData/BlueReel-Development",
        "bind_address": "192.168.50.10", "port": 18080, "api_port": 18081, "web_port": 18082,
    }


@pytest.mark.parametrize("include_native", [False, True])
def test_shared_backup_native_extension_is_opt_in_and_dry_validation_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], include_native: bool,
) -> None:
    product, database, native, output = [tmp_path / name for name in ("product", "database", "configuration", "backups")]
    for directory in (product, database, native, tmp_path / "data", tmp_path / "artwork", tmp_path / "temporary"):
        directory.mkdir()
    original_database = _database_bytes(tmp_path)
    (database / "app.db").write_bytes(original_database)
    (product / "product.json").write_text('{"name":"Test Product"}')
    environment = b"APP_SECRET_KEY=private-synthetic-secret-not-for-output\n"
    (native / ".env").write_bytes(environment)
    metadata = json.dumps(_native_metadata()).encode()
    proxy = b"http://127.0.0.1:18080 {\n reverse_proxy 127.0.0.1:18081\n}\n"
    (native / "installation.json").write_bytes(metadata)
    (native / "Caddyfile").write_bytes(proxy)
    (native / "diagnostic.log").write_text("not recovery configuration")
    (native / "runtime-state").mkdir()
    (native / "runtime-state/should-not-copy.txt").write_text("temporary state excluded")
    arguments = [
        "backup", "--env-file", str(native / ".env"), "--database", str(database / "app.db"),
        "--data", str(tmp_path / "data"), "--artwork", str(tmp_path / "artwork"),
        "--temporary", str(tmp_path / "temporary"), "--config", str(product), "--output", str(output),
    ]
    if include_native:
        arguments.extend(["--native-config", str(native)])
    monkeypatch.setattr(sys, "argv", arguments)
    assert backup.main() == 0
    archive_path = next(output.glob("*.zip"))
    before = archive_path.read_bytes()
    summary = restore_validate.validate(archive_path)
    backup.validate_archive(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == ARCHIVE_FORMAT and manifest["format_version"] == 1
        assert archive.read("configuration/.env") == environment
        if include_native:
            assert archive.read(NATIVE_CONFIG_MEMBERS[0]) == metadata
            assert archive.read(NATIVE_CONFIG_MEMBERS[1]) == proxy
            assert summary["native_configuration"]["complete"] is True
            assert summary["native_configuration"]["restore_mode"] == "manual_offline_only"
        else:
            assert not any(name.startswith("configuration/native/") for name in archive.namelist())
            assert "native_configuration" not in summary
        assert not any("diagnostic" in name or "runtime-state" in name for name in archive.namelist())
    assert archive_path.read_bytes() == before
    assert (database / "app.db").read_bytes() == original_database
    assert (native / ".env").read_bytes() == environment
    assert (native / "installation.json").read_bytes() == metadata
    assert (native / "Caddyfile").read_bytes() == proxy
    assert "private-synthetic-secret" not in capsys.readouterr().out
    assert "192.168.50.10" not in json.dumps(summary)


@pytest.mark.parametrize("missing", ["installation.json", "Caddyfile"])
def test_native_configuration_copy_fails_closed_on_missing_recovery_file(tmp_path: Path, missing: str) -> None:
    native = tmp_path / "configuration"
    native.mkdir()
    for name in ("installation.json", "Caddyfile"):
        if name != missing:
            (native / name).write_text("synthetic fixture")
    stage = tmp_path / "stage"
    with pytest.raises(FileNotFoundError):
        backup.copy_native_configuration(native, stage)
    assert not stage.exists()


def test_native_configuration_copy_rejects_hardlinks_before_copying(tmp_path: Path) -> None:
    native = tmp_path / "configuration"
    native.mkdir()
    original = native / "installation.json"
    original.write_text("synthetic shared metadata")
    os.link(original, tmp_path / "external-metadata.json")
    (native / "Caddyfile").write_text("synthetic proxy config")
    with pytest.raises(backup.BackupError, match="unlinked"):
        backup.copy_native_configuration(native, tmp_path / "stage")
    assert not (tmp_path / "stage").exists()


@pytest.mark.parametrize("members", [
    [NATIVE_CONFIG_MEMBERS[0], "configuration/.env"],
    [NATIVE_CONFIG_MEMBERS[1], "configuration/.env"],
    list(NATIVE_CONFIG_MEMBERS),
])
def test_native_optional_extension_requires_both_files_and_environment(tmp_path: Path, members: list[str]) -> None:
    path = tmp_path / "partial-native.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in members:
            archive.writestr(name, "synthetic configuration")
    with zipfile.ZipFile(path) as archive, pytest.raises(BackupFormatError, match="must include"):
        backup_format_validate_native(archive, set(members))


def backup_format_validate_native(archive: zipfile.ZipFile, members: set[str]) -> None:
    from scripts.backup_format import validate_native_configuration

    validate_native_configuration(archive, members)


@pytest.mark.parametrize("metadata", [b"not JSON", b"[]", b'{"schema_version": 1}', b'{"schema_version": 2}'])
def test_native_extension_rejects_invalid_metadata_without_exposing_it(tmp_path: Path, metadata: bytes) -> None:
    path = tmp_path / "invalid-native.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(NATIVE_CONFIG_MEMBERS[0], metadata)
        archive.writestr(NATIVE_CONFIG_MEMBERS[1], b"proxy config")
        archive.writestr("configuration/.env", b"secret")
    with zipfile.ZipFile(path) as archive, pytest.raises(BackupFormatError):
        backup_format_validate_native(archive, set(archive.namelist()))


def test_native_option_cannot_accidentally_read_default_docker_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BACKUP_ENV_FILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["backup", "--native-config", str(tmp_path)])
    monkeypatch.setattr(backup, "parse_dotenv", lambda _: pytest.fail("Must reject before reading default environment"))
    with pytest.raises(backup.BackupError, match="matching explicit environment"):
        backup.main()
