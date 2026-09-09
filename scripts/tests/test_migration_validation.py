"""Migration verification must detect loss without restoring or revealing data."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import validate_migration as migration


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def manifest(root: Path) -> str:
    entries = [{"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                "sha256": migration.file_digest(path)}
               for path in sorted(root.rglob("*")) if path.is_file() and path.name != "manifest.json"]
    write_json(root / "manifest.json", {"format": migration.FORMAT, "format_version": 1, "files": entries})
    return migration.file_digest(root / "manifest.json")


@pytest.fixture
def snapshot(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "private snapshot #1"
    (root / "database").mkdir(parents=True)
    (root / "artwork/metadata/ab").mkdir(parents=True)
    image = b"\xff\xd8\xffprivate-synthetic-artwork"
    digest = hashlib.sha256(image).hexdigest()
    (root / "artwork/metadata/ab/image.jpg").write_bytes(image)
    with sqlite3.connect(root / "database/app.db") as db:
        for table in migration.SCHEMAS_BY_REVISION[migration.EXPECTED_ALEMBIC_HEAD]:
            if table == "alembic_version":
                db.execute("CREATE TABLE alembic_version(version_num TEXT)")
                db.execute("INSERT INTO alembic_version VALUES (?)", (migration.EXPECTED_ALEMBIC_HEAD,))
            elif table == "local_artwork":
                db.execute("CREATE TABLE local_artwork(id TEXT, cached_path TEXT, fingerprint TEXT, provider TEXT)")
                db.executemany("INSERT INTO local_artwork VALUES (?,?,?,?)", [
                    ("art1", "metadata/ab/image.jpg", digest, "tmdb"),
                    ("art2", "metadata/ab/image.jpg", digest, "tmdb"),
                    ("sidecar", None, "c" * 64, None),
                ])
            else:
                db.execute(f'CREATE TABLE "{table}"(id TEXT, value BLOB)')
        db.executemany("INSERT INTO watch_progress VALUES (?,?)", [("viewer", b"private-checkpoint"), ("owner", 27)])
    write_json(root / "database-summary.json", migration.database_summary(root / "database/app.db"))
    (root / "configuration").mkdir()
    (root / "configuration/private-token.txt").write_bytes(b"DO-NOT-PRINT-ME")
    return root, manifest(root)


def resummarize(root: Path) -> str:
    write_json(root / "database-summary.json", migration.database_summary(root / "database/app.db"))
    return manifest(root)


def test_snapshot_and_restored_copy_verify_without_writes(snapshot, tmp_path):
    root, expected = snapshot
    restored = tmp_path / "restored"
    shutil.copytree(root, restored)
    before = {str(path): (path.stat().st_mtime_ns, path.read_bytes())
              for tree in (root, restored) for path in tree.rglob("*") if path.is_file()}
    result = migration.validate_snapshot(root, expected, database=restored / "database/app.db",
                                         artwork_dir=restored / "artwork")
    assert result["verified"] and result["restored_database_matches"]
    assert result["artwork"] == {"references": 3, "cached_references_verified": 2, "unique_cached_files_verified": 1,
                                 "source_only_references_not_copied": 1, "absolute_references_mapped_readonly": 0}
    assert all((Path(path).stat().st_mtime_ns, Path(path).read_bytes()) == previous
               for path, previous in before.items())
    assert not list(root.rglob("*-wal")) and not list(restored.rglob("*-wal"))


def test_row_hash_is_order_independent_and_retains_blob_values(snapshot, tmp_path):
    root, _ = snapshot
    copy = tmp_path / "reordered.db"
    shutil.copy2(root / "database/app.db", copy)
    with sqlite3.connect(copy) as db:
        rows = db.execute("SELECT * FROM watch_progress").fetchall()
        db.execute("DELETE FROM watch_progress")
        db.executemany("INSERT INTO watch_progress VALUES (?,?)", reversed(rows))
    assert migration.database_summary(copy) == migration.database_summary(root / "database/app.db")


def test_changed_watch_progress_reports_table_without_private_value(snapshot, tmp_path, capsys):
    root, expected = snapshot
    copy = tmp_path / "changed.db"
    shutil.copy2(root / "database/app.db", copy)
    with sqlite3.connect(copy) as db:
        db.execute("UPDATE watch_progress SET value='DO-NOT-PRINT-NEW-VALUE'")
    assert migration.main([str(root), "--manifest-sha256", expected, "--database", str(copy)]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["changed_tables"] == ["watch_progress"]
    assert "DO-NOT-PRINT" not in output and str(copy) not in output


@pytest.mark.parametrize("statement", [
    "CREATE INDEX added_index ON watch_progress(id)",
    "CREATE VIEW added_view AS SELECT id FROM watch_progress",
    "CREATE TRIGGER added_trigger AFTER INSERT ON watch_progress BEGIN SELECT 1; END",
])
def test_changed_schema_rejected_even_when_columns_and_rows_match(snapshot, tmp_path, statement):
    root, expected = snapshot
    copy = tmp_path / "schema.db"
    shutil.copy2(root / "database/app.db", copy)
    with sqlite3.connect(copy) as db:
        db.execute(statement)
    result = migration.validate_snapshot(root, expected, database=copy)
    assert result["changed_tables"] == []
    assert result["schema_matches"] is False
    assert result["verified"] is False and result["restored_database_matches"] is False


@pytest.mark.parametrize("failure", ["hash", "size", "extra", "missing", "summary", "artwork", "schema"])
def test_corruption_rejected(snapshot, failure):
    root, expected = snapshot
    if failure == "hash":
        expected = "0" * 64
    elif failure == "size":
        (root / "configuration/private-token.txt").write_bytes(b"changed")
    elif failure == "extra":
        (root / "unmanifested.txt").write_text("extra")
    elif failure == "missing":
        (root / "configuration/private-token.txt").unlink()
    elif failure == "summary":
        data = migration.read_document(root / "database-summary.json")
        data["tables"]["watch_progress"]["count"] += 1
        write_json(root / "database-summary.json", data)
        expected = manifest(root)
    elif failure == "artwork":
        (root / "artwork/metadata/ab/image.jpg").write_bytes(b"corrupt")
        expected = manifest(root)  # Independent DB artwork fingerprint must still reject it.
    else:
        with sqlite3.connect(root / "database/app.db") as db:
            db.execute("UPDATE alembic_version SET version_num='unrelated'")
        expected = manifest(root)
    with pytest.raises((migration.MigrationValidationError, OSError)):
        migration.validate_snapshot(root, expected)


@pytest.mark.parametrize("unsafe", ["../outside", "/absolute", "C:/private", "C:private", "a\\b", "a:b", "a/../b",
                                   "a//b", "NUL", "a/COM1.txt", "a/trailing.", "a/trailing ", "a\nsecret"])
def test_reject_unsafe_cross_platform_paths(unsafe):
    with pytest.raises(migration.MigrationValidationError, match="unsafe_manifest_path"):
        migration.relative_name(unsafe)


@pytest.mark.parametrize("failure", ["duplicate_json", "duplicate_file", "case_alias", "self_alias", "boolean_size"])
def test_manifest_ambiguity_rejected(snapshot, failure):
    root, _ = snapshot
    path = root / "manifest.json"
    data = migration.read_document(path)
    if failure == "duplicate_json":
        path.write_text('{"files": [], "files": []}', encoding="utf-8")
    else:
        entry = dict(data["files"][0])
        if failure == "case_alias":
            entry["path"] = entry["path"].upper()
        elif failure == "self_alias":
            entry["path"] = "MANIFEST.JSON"
        if failure == "boolean_size":
            data["files"][0]["bytes"] = True
        else:
            data["files"].append(entry)
        write_json(path, data)
    with pytest.raises(migration.MigrationValidationError):
        migration.validate_snapshot(root, migration.file_digest(path))


def test_junction_attribute_rejected_before_traversal(snapshot, monkeypatch):
    root, expected = snapshot
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == root / "artwork":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(migration.MigrationValidationError, match="linked_path_rejected"):
        migration.validate_snapshot(root, expected)


@pytest.mark.parametrize("root_text", ["C:/Old Agent/artwork", "/old-agent/artwork"])
def test_absolute_artwork_maps_to_copy_in_memory_only(snapshot, root_text):
    root, _ = snapshot
    with sqlite3.connect(root / "database/app.db") as db:
        db.execute("UPDATE local_artwork SET cached_path=? WHERE provider='tmdb'",
                   (root_text + "/metadata/ab/image.jpg",))
    expected = resummarize(root)
    before = (root / "database/app.db").read_bytes()
    with pytest.raises(migration.MigrationValidationError, match="absolute_artwork_requires_original_root"):
        migration.validate_snapshot(root, expected)
    result = migration.validate_snapshot(root, expected, original_artwork_root=root_text)
    assert result["artwork"]["absolute_references_mapped_readonly"] == 2
    assert (root / "database/app.db").read_bytes() == before
    with pytest.raises(migration.MigrationValidationError, match="artwork_outside_original_root"):
        migration.validate_snapshot(root, expected, original_artwork_root=root_text + "/other")


def test_nonempty_wal_rejected_but_empty_wal_and_shm_are_ignored_readonly(snapshot):
    root, expected = snapshot
    database = root / "database/app.db"
    wal, shm = Path(str(database) + "-wal"), Path(str(database) + "-shm")
    wal.write_bytes(b"")
    shm.write_bytes(b"reference-only-shared-memory")
    expected = manifest(root)
    assert migration.validate_snapshot(root, expected)["verified"]
    wal.write_bytes(b"uncheckpointed-data")
    expected = manifest(root)
    with pytest.raises(migration.MigrationValidationError, match="database_has_uncheckpointed_sidecar"):
        migration.validate_snapshot(root, expected)


def test_cli_errors_never_print_secret_path_or_raw_sqlite_error(tmp_path, capsys):
    secret = tmp_path / "PRIVATE-PATH"
    assert migration.main([str(secret), "--manifest-sha256", "a" * 64]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE-PATH" not in output and "FileNotFoundError" not in output
    assert json.loads(output)["targets_modified"] is False


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation needs separate Windows privileges")
def test_actual_symbolic_link_is_rejected(snapshot, tmp_path):
    root, expected = snapshot
    outside = tmp_path / "private-outside"
    outside.write_bytes(b"secret")
    (root / "link").symlink_to(outside)
    with pytest.raises(migration.MigrationValidationError, match="linked_path_rejected"):
        migration.validate_snapshot(root, expected)
