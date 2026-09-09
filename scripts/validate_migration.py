"""Read-only verification of a private Windows migration directory and offline restore.

This format supplements the standard ZIP backup; it never restores, decrypts an
identity, reads media, changes configuration, or contacts the Portal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.backup_format import EXPECTED_ALEMBIC_HEAD, SCHEMAS_BY_REVISION
elif __package__:
    from .backup_format import EXPECTED_ALEMBIC_HEAD, SCHEMAS_BY_REVISION
else:
    from backup_format import EXPECTED_ALEMBIC_HEAD, SCHEMAS_BY_REVISION

FORMAT = "blue-ash-reel-migration-snapshot"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*\Z")
RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?\Z", re.IGNORECASE)
MAX_JSON_BYTES = 16 * 1024 * 1024


class MigrationValidationError(RuntimeError):
    """Only fixed, non-sensitive error codes are exposed by the CLI."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise MigrationValidationError(code)


def ordinary(path: Path, *, directory: bool = False) -> None:
    absolute = path.absolute()
    for component in (*reversed(absolute.parents), absolute):
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "linked_path_rejected")
    info = absolute.stat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "non_regular_path")


def relative_name(value: object) -> str:
    require(isinstance(value, str) and 0 < len(value) <= 4096, "unsafe_manifest_path")
    assert isinstance(value, str)
    parts = value.split("/")
    require(not PureWindowsPath(value).drive and not PurePosixPath(value).is_absolute(), "unsafe_manifest_path")
    require(all(part not in {"", ".", ".."} and not part.endswith((".", " ")) and not RESERVED.fullmatch(part)
                and not any(ord(char) < 32 or char in '\\:*?"<>|' for char in part) for part in parts),
            "unsafe_manifest_path")
    return value


def file_digest(path: Path) -> str:
    ordinary(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def read_document(path: Path) -> dict[str, Any]:
    ordinary(path)
    require(path.stat().st_size <= MAX_JSON_BYTES, "oversized_json_document")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    require(isinstance(value, dict), "invalid_json_document")
    assert isinstance(value, dict)
    return value


def inventory(root: Path) -> set[str]:
    ordinary(root, directory=True)
    names: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            info = path.lstat()
            # Check before following either a symlink or a Windows directory junction.
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "linked_path_rejected")
            name = relative_name(path.relative_to(root).as_posix())
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            else:
                require(stat.S_ISREG(info.st_mode), "non_regular_path")
                names.add(name)
    return names


def open_offline_database(path: Path) -> sqlite3.Connection:
    ordinary(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            ordinary(sidecar)
            # Read-only inspection of a WAL-mode online backup can leave an
            # empty WAL and shared-memory file. Neither carries committed data.
            require(suffix == "-shm" or sidecar.stat().st_size == 0,
                    "database_has_uncheckpointed_sidecar")
    # immutable=1 avoids even SQLite lock/shared-memory writes. Only an offline,
    # standalone backup/restore copy is accepted; a live database is not a target.
    connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def database_summary(path: Path, expected_revision: str = EXPECTED_ALEMBIC_HEAD) -> dict[str, Any]:
    require(expected_revision in SCHEMAS_BY_REVISION, "unknown_schema_revision")
    connection = open_offline_database(path)
    try:
        require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "database_integrity_failed")
        require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "database_foreign_keys_failed")
        require(connection.execute("SELECT version_num FROM alembic_version").fetchall() == [(expected_revision,)],
                "database_schema_mismatch")
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        require(SCHEMAS_BY_REVISION[expected_revision].issubset(tables), "database_required_tables_missing")
        columns, hashes = {}, {}
        for table in tables:
            if table == "alembic_version":
                continue
            require(bool(IDENTIFIER.fullmatch(table)), "database_identifier_rejected")
            fields = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            require(bool(fields) and all(IDENTIFIER.fullmatch(field) for field in fields),
                    "database_identifier_rejected")
            columns[table] = fields
            selected = ",".join('"' + field + '"' for field in fields)
            # Both identifiers were restricted to ASCII letters/digits/underscore above.
            rows = connection.execute(f'SELECT {selected} FROM "{table}"').fetchall()  # noqa: S608
            # Matches the audited online snapshot's canonical representation.
            encoded = sorted(json.dumps(list(row), ensure_ascii=False, separators=(",", ":"),
                                        default=lambda value: {"bytes_hex": value.hex()}).encode() for row in rows)
            combined = hashlib.sha256()
            for row in encoded:
                combined.update(len(row).to_bytes(8, "big"))
                combined.update(row)
            hashes[table] = {"count": len(rows), "sha256": combined.hexdigest()}
        return {"schema": expected_revision, "columns": columns, "tables": hashes}
    finally:
        connection.close()


def database_schema_digest(path: Path) -> str:
    """Include indexes, constraints, views and triggers when comparing a restore."""
    connection = open_offline_database(path)
    try:
        rows = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name,tbl_name,sql"
        ).fetchall()
        encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
    finally:
        connection.close()


def cache_relative(value: str, original_root: str | None) -> str:
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute() or posix.is_absolute():
        require(original_root is not None, "absolute_artwork_requires_original_root")
        assert original_root is not None
        try:
            if windows.is_absolute():
                value = windows.relative_to(PureWindowsPath(original_root)).as_posix()
            else:
                value = posix.relative_to(PurePosixPath(original_root)).as_posix()
        except ValueError:
            raise MigrationValidationError("artwork_outside_original_root") from None
    return relative_name(value)


def validate_artwork(database: Path, root: Path, original_root: str | None = None) -> dict[str, int]:
    ordinary(root, directory=True)
    connection = open_offline_database(database)
    try:
        rows = connection.execute("SELECT cached_path,fingerprint,provider FROM local_artwork").fetchall()
    finally:
        connection.close()
    checked: dict[str, str] = {}
    cached, source_only, absolute = 0, 0, 0
    for value, fingerprint, provider in rows:
        if not value:
            # Scanner sidecars are references to source media, excluded from this
            # snapshot; their fingerprint describes path/stat, not content SHA256.
            require(provider is None, "provider_artwork_cache_missing")
            source_only += 1
            continue
        require(isinstance(value, str) and isinstance(fingerprint, str) and bool(SHA256.fullmatch(fingerprint)),
                "invalid_artwork_reference")
        absolute += int(PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute())
        relative = cache_relative(value, original_root)
        if relative not in checked:
            checked[relative] = file_digest(root / relative)
        require(checked[relative] == fingerprint, "artwork_digest_mismatch")
        cached += 1
    return {"references": len(rows), "cached_references_verified": cached, "unique_cached_files_verified": len(checked),
            "source_only_references_not_copied": source_only, "absolute_references_mapped_readonly": absolute}


def validate_snapshot(root: Path, manifest_sha256: str, *, database: Path | None = None,
                      artwork_dir: Path | None = None, original_artwork_root: str | None = None,
                      expected_revision: str = EXPECTED_ALEMBIC_HEAD) -> dict[str, Any]:
    root = root.absolute()
    require(bool(SHA256.fullmatch(manifest_sha256)), "invalid_expected_manifest_hash")
    manifest_path = root / "manifest.json"
    require(file_digest(manifest_path) == manifest_sha256, "manifest_hash_mismatch")
    manifest = read_document(manifest_path)
    require(manifest.get("format") == FORMAT and type(manifest.get("format_version")) is int
            and manifest["format_version"] == 1, "unsupported_snapshot_format")
    entries = manifest.get("files")
    require(isinstance(entries, list) and 0 < len(entries) <= 100000, "invalid_manifest_files")
    assert isinstance(entries, list)
    names, folded = set(), set()
    total = 0
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {"path", "bytes", "sha256"}, "invalid_manifest_entry")
        name = relative_name(entry["path"])
        require(name.casefold() != "manifest.json" and name.casefold() not in folded, "duplicate_manifest_path")
        names.add(name)
        folded.add(name.casefold())
        require(type(entry["bytes"]) is int and entry["bytes"] >= 0
                and isinstance(entry["sha256"], str) and bool(SHA256.fullmatch(entry["sha256"])),
                "invalid_manifest_digest_or_size")
        path = root / name
        ordinary(path)
        require(path.stat().st_size == entry["bytes"] and file_digest(path) == entry["sha256"], "file_hash_mismatch")
        total += entry["bytes"]
    require({"database/app.db", "database-summary.json"}.issubset(names), "required_snapshot_file_missing")
    require(inventory(root) == names | {"manifest.json"}, "unmanifested_snapshot_file")
    expected = read_document(root / "database-summary.json")
    actual = database_summary(root / "database/app.db", expected_revision)
    require(actual == expected, "snapshot_database_summary_mismatch")
    schema_digest = database_schema_digest(root / "database/app.db")
    artwork = validate_artwork(root / "database/app.db", root / "artwork", original_artwork_root)
    result: dict[str, Any] = {"verified": True, "file_count": len(names), "bytes": total,
                              "manifest_sha256": manifest_sha256, "schema": expected_revision,
                              "integrity_check": "ok", "foreign_key_violations": 0,
                              "sqlite_schema_sha256": schema_digest,
                              "tables": actual["tables"], "artwork": artwork,
                              "identity_decrypted": False, "targets_modified": False}
    require(database is not None or artwork_dir is None, "restore_database_required")
    if database is not None:
        restored = database_summary(database, expected_revision)
        changed = sorted(table for table in set(expected["tables"]) | set(restored["tables"])
                         if expected["tables"].get(table) != restored["tables"].get(table)
                         or expected["columns"].get(table) != restored["columns"].get(table))
        result["schema_matches"] = database_schema_digest(database) == schema_digest
        result["restored_database_matches"] = not changed and result["schema_matches"]
        result["changed_tables"] = changed
        result["restored_tables"] = restored["tables"]
        if artwork_dir is not None:
            result["restored_artwork"] = validate_artwork(database, artwork_dir, original_artwork_root)
        result["verified"] = result["restored_database_matches"]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--manifest-sha256", required=True,
                        help="SHA-256 recorded separately when the backup was created")
    parser.add_argument("--database", type=Path, help="Optional stopped, standalone restored database to compare")
    parser.add_argument("--artwork-dir", type=Path, help="Optional restored artwork root; requires --database")
    parser.add_argument("--original-artwork-root",
                        help="Old artwork root for in-memory mapping of absolute cache references")
    parser.add_argument("--expected-revision", default=EXPECTED_ALEMBIC_HEAD)
    args = parser.parse_args(argv)
    try:
        result = validate_snapshot(args.snapshot, args.manifest_sha256, database=args.database,
                                   artwork_dir=args.artwork_dir, original_artwork_root=args.original_artwork_root,
                                   expected_revision=args.expected_revision)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["verified"] else 2
    except (MigrationValidationError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        code = str(error) if isinstance(error, MigrationValidationError) else "snapshot_read_or_format_failed"
        print(json.dumps({"verified": False, "error": code, "targets_modified": False}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
