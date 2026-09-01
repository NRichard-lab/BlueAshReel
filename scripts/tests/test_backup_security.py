"""Hostile-archive and state-path regression tests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import backup, restore_validate
from scripts.backup_format import (
    ARCHIVE_FORMAT,
    EXPECTED_ALEMBIC_HEAD,
    MANIFEST_VERSION,
    PHASE1_REVISION,
    PHASE1_TABLES,
    REQUIRED_APPLICATION_TABLES,
    _head_from_migration_directory,
    validate_database,
)

Validator = Callable[[Path], object]


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
