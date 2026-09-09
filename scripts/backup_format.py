"""Shared, product-neutral backup archive validation primitives."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import stat
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ARCHIVE_FORMAT = "home-media-backup"
MANIFEST_VERSION = 1
PHASE1_REVISION = "773863f5a6aa"
EXPECTED_ALEMBIC_HEAD = "2d0100000001"
MANIFEST_MEMBER = "manifest.json"
DATABASE_MEMBER = "database/app.db"
PRODUCT_CONFIG_MEMBER = "configuration/product/product.json"
NATIVE_CONFIG_MEMBERS = (
    "configuration/native/installation.json",
    "configuration/native/Caddyfile",
)

# A backup from this application version must contain the complete phase-one
# schema. Alembic's exact head check guards migrations, while this set also
# rejects unrelated SQLite files that merely copied the version table.
PHASE1_TABLES = frozenset(
    {
        "alembic_version",
        "application_settings",
        "audio_streams",
        "audit_events",
        "background_job_events",
        "background_jobs",
        "episodes",
        "external_metadata_identifiers",
        "libraries",
        "library_paths",
        "local_artwork",
        "media_files",
        "media_items",
        "movies",
        "roles",
        "scan_jobs",
        "scan_locks",
        "seasons",
        "series",
        "subtitle_streams",
        "user_roles",
        "user_sessions",
        "users",
        "video_streams",
    }
)
SCHEMAS_BY_REVISION = {
    PHASE1_REVISION: PHASE1_TABLES,
    "2a0100000001": PHASE1_TABLES | {"user_libraries", "user_preferences", "watch_progress", "media_search"},
    "2b0100000001": PHASE1_TABLES | {
        "user_libraries", "user_preferences", "watch_progress", "media_search", "playback_sessions",
    },
    "2c0100000001": PHASE1_TABLES | {
        "user_libraries", "user_preferences", "watch_progress", "media_search", "playback_sessions",
        "portal_grants", "remote_objects",
    },
    "2d0100000001": PHASE1_TABLES | {
        "user_libraries", "user_preferences", "watch_progress", "media_search", "playback_sessions",
        "portal_grants", "remote_objects", "metadata_records",
    },
}
REQUIRED_APPLICATION_TABLES = SCHEMAS_BY_REVISION[EXPECTED_ALEMBIC_HEAD]
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_REVISION_PATTERN = re.compile(r'^revision[^=]*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_REVISION_PATTERN = re.compile(r"^down_revision[^=]*=\s*(.+)$", re.MULTILINE)
_QUOTED_REVISION_PATTERN = re.compile(r'["\']([^"\']+)["\']')


class BackupFormatError(RuntimeError):
    """A safe, user-facing archive or database validation failure."""


@dataclass(frozen=True)
class DatabaseSummary:
    """Validated SQLite metadata used by restore reporting."""

    bytes: int
    pages: int
    tables: tuple[str, ...]
    alembic_revision: str


@dataclass(frozen=True)
class ArchiveSummary:
    """Validated archive metadata used by restore reporting."""

    manifest: dict[str, Any]
    members: tuple[str, ...]
    database: DatabaseSummary


def archive_prefix_from_name(product_name: str) -> str:
    """Return a filesystem-safe archive prefix derived from product identity."""

    normalized = unicodedata.normalize("NFKD", product_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_PATTERN.sub("-", ascii_name).strip("-")[:48].rstrip("-")
    if not slug:
        slug = "application"
    return f"{slug}-backup-"


def _head_from_migration_directory(directory: Path) -> str | None:
    revisions: set[str] = set()
    parent_revisions: set[str] = set()
    for migration in directory.glob("*.py"):
        try:
            source = migration.read_text(encoding="utf-8")
        except OSError:
            return None
        revision = _REVISION_PATTERN.search(source)
        down_revision = _DOWN_REVISION_PATTERN.search(source)
        if revision is None or down_revision is None:
            continue
        revisions.add(revision.group(1))
        parent_revisions.update(
            _QUOTED_REVISION_PATTERN.findall(down_revision.group(1))
        )
    heads = revisions.difference(parent_revisions)
    return next(iter(heads)) if len(heads) == 1 else None


def installed_alembic_head() -> str:
    """Discover the exact head in the installed image used by the backup tool.

    During an upgrade, the scripts are bind-mounted from the new checkout while
    the one-shot backup still runs in the previously installed backend image.
    Checking that image first lets the pre-migration backup validate its exact
    installed schema rather than incorrectly requiring the not-yet-applied head.
    """

    candidates = (
        Path("/app/alembic/versions"),
        Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions",
    )
    for directory in candidates:
        if directory.is_dir():
            if head := _head_from_migration_directory(directory):
                return head
            raise BackupFormatError("Installed migrations do not have one unambiguous head")
    return EXPECTED_ALEMBIC_HEAD


def safe_member(name: str) -> bool:
    """Return whether a ZIP member is a canonical, relative POSIX file path."""

    if not name or "\\" in name or "\x00" in name or name.endswith("/"):
        return False
    path = PurePosixPath(name)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and "." not in path.parts
        and ".." not in path.parts
        and ":" not in path.parts[0]
        and path.as_posix() == name
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BackupFormatError(f"Manifest contains a duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_manifest(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupFormatError("Backup manifest is invalid JSON") from error
    if not isinstance(payload, dict):
        raise BackupFormatError("Backup manifest must be a JSON object")
    return payload


def _regular_member(info: zipfile.ZipInfo) -> bool:
    if info.is_dir() or info.flag_bits & 0x1:
        return False
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    member_type = stat.S_IFMT(unix_mode)
    return member_type in (0, stat.S_IFREG)


def _sha256_member(archive: zipfile.ZipFile, member: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with archive.open(member, "r") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(block)
            digest.update(block)
    return digest.hexdigest(), total


def validate_database(
    path: Path, expected_revision: str = EXPECTED_ALEMBIC_HEAD
) -> DatabaseSummary:
    """Require an intact application database at this code's exact schema head."""

    if expected_revision not in SCHEMAS_BY_REVISION:
        raise BackupFormatError("Unknown application schema revision")

    assert_no_link_components(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_key_error = connection.execute("PRAGMA foreign_key_check").fetchone()
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        )
        pages = int(connection.execute("PRAGMA page_count").fetchone()[0])
        revisions = tuple(
            row[0]
            for row in connection.execute("SELECT version_num FROM alembic_version")
        )
    except sqlite3.Error as error:
        raise BackupFormatError(f"SQLite schema validation failed: {error}") from error
    finally:
        connection.close()

    if integrity is None or integrity[0] != "ok":
        detail = "no result" if integrity is None else str(integrity[0])
        raise BackupFormatError(f"SQLite integrity validation failed: {detail}")
    if foreign_key_error is not None:
        raise BackupFormatError("SQLite foreign-key validation failed")
    missing_tables = sorted(SCHEMAS_BY_REVISION[expected_revision].difference(tables))
    if missing_tables:
        raise BackupFormatError(
            "Database is missing required application tables: "
            + ", ".join(missing_tables)
        )
    if revisions != (expected_revision,):
        found = ", ".join(str(revision) for revision in revisions) or "none"
        raise BackupFormatError(
            f"Database Alembic revision must be {expected_revision}; found {found}"
        )
    return DatabaseSummary(
        bytes=path.stat().st_size,
        pages=pages,
        tables=tables,
        alembic_revision=expected_revision,
    )


def assert_no_link_components(path: Path, *, allow_missing: bool = False) -> None:
    """Refuse symlinks and every Windows reparse type, including ancestor junctions."""
    absolute = path.absolute()
    for component in (*reversed(absolute.parents), absolute):
        try:
            information = component.lstat()
        except FileNotFoundError:
            if allow_missing:
                continue
            raise
        if stat.S_ISLNK(information.st_mode) or getattr(information, "st_file_attributes", 0) & 0x400:
            raise BackupFormatError("Backup sources and destinations cannot contain symbolic links or reparse points")


def validate_native_configuration(archive: zipfile.ZipFile, members: set[str]) -> None:
    """Validate the optional v1 native extension as data, never as executable configuration.

    Original v1 archives, including Docker backups, have no native members and
    retain their existing validation behavior. Complete native snapshots remain
    ordinary checksum-manifested configuration files in that same shared format.
    """
    present = set(NATIVE_CONFIG_MEMBERS).intersection(members)
    if not present:
        return
    if present != set(NATIVE_CONFIG_MEMBERS) or "configuration/.env" not in members:
        raise BackupFormatError(
            "Native recovery configuration must include installation metadata, Caddyfile and environment"
        )
    metadata_name, proxy_name = NATIVE_CONFIG_MEMBERS
    if not 0 < archive.getinfo(metadata_name).file_size <= 65536:
        raise BackupFormatError("Native installation metadata has an invalid size")
    if not 0 < archive.getinfo(proxy_name).file_size <= 262144:
        raise BackupFormatError("Native proxy configuration has an invalid size")
    try:
        metadata = json.loads(archive.read(metadata_name), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupFormatError("Native installation metadata is invalid JSON") from error
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise BackupFormatError("Native installation metadata has an unsupported schema")
    text_fields = ("instance", "service_prefix", "program_dir", "data_dir", "bind_address")
    if any(not isinstance(metadata.get(key), str) or not metadata[key].strip() for key in text_fields):
        raise BackupFormatError("Native installation metadata is missing recovery identity or paths")
    for key in ("port", "api_port", "web_port"):
        value = metadata.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise BackupFormatError("Native installation metadata has invalid recovery ports")


def validate_backup_archive(
    path: Path, expected_revision: str = EXPECTED_ALEMBIC_HEAD
) -> ArchiveSummary:
    """Validate an archive without extracting members into application paths."""

    if not path.is_file():
        raise BackupFormatError(f"Backup archive does not exist: {path}")
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise BackupFormatError("Backup archive contains duplicate ZIP members")
        for info in infos:
            if not safe_member(info.filename):
                raise BackupFormatError(
                    f"Backup archive contains an unsafe member path: {info.filename}"
                )
            if not _regular_member(info):
                raise BackupFormatError(
                    f"Backup archive contains a non-regular or encrypted member: {info.filename}"
                )
        corrupt = archive.testzip()
        if corrupt is not None:
            raise BackupFormatError(f"ZIP validation failed for member: {corrupt}")
        try:
            manifest = _load_manifest(archive.read(MANIFEST_MEMBER))
        except KeyError as error:
            raise BackupFormatError("Backup manifest is missing") from error

        if (
            manifest.get("format") != ARCHIVE_FORMAT
            or manifest.get("format_version") != MANIFEST_VERSION
        ):
            raise BackupFormatError("Backup manifest has an unsupported format")
        if manifest.get("database_revision") != expected_revision:
            raise BackupFormatError(
                f"Backup manifest does not target Alembic head {expected_revision}"
            )
        records = manifest.get("files")
        if not isinstance(records, list):
            raise BackupFormatError("Backup manifest file list is invalid")

        recorded_names: set[str] = set()
        normalized_records: list[tuple[str, str, int]] = []
        for record in records:
            if not isinstance(record, dict):
                raise BackupFormatError(
                    "Backup manifest contains an invalid file record"
                )
            member = record.get("path")
            expected_hash = record.get("sha256")
            expected_size = record.get("size")
            if not isinstance(member, str) or not safe_member(member):
                raise BackupFormatError("Backup manifest contains an unsafe file path")
            if member == MANIFEST_MEMBER:
                raise BackupFormatError(
                    "Backup manifest must not list itself as a file record"
                )
            if member in recorded_names:
                raise BackupFormatError(
                    f"Backup manifest contains a duplicate file record: {member}"
                )
            if (
                not isinstance(expected_hash, str)
                or _SHA256_PATTERN.fullmatch(expected_hash) is None
            ):
                raise BackupFormatError(
                    f"Backup manifest contains an invalid SHA-256 for {member}"
                )
            if (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size < 0
            ):
                raise BackupFormatError(
                    f"Backup manifest contains an invalid size for {member}"
                )
            recorded_names.add(member)
            normalized_records.append((member, expected_hash, expected_size))

        actual_names = set(names)
        expected_names = recorded_names | {MANIFEST_MEMBER}
        missing = sorted(expected_names.difference(actual_names))
        extra = sorted(actual_names.difference(expected_names))
        if missing:
            raise BackupFormatError(
                f"Backup archive is missing a manifested member: {missing[0]}"
            )
        if extra:
            raise BackupFormatError(
                f"Backup archive contains an unmanifested member: {extra[0]}"
            )
        for required_member in (DATABASE_MEMBER, PRODUCT_CONFIG_MEMBER):
            if required_member not in recorded_names:
                raise BackupFormatError(f"Backup does not contain {required_member}")

        info_by_name = {info.filename: info for info in infos}
        for member, expected_hash, expected_size in normalized_records:
            if info_by_name[member].file_size != expected_size:
                raise BackupFormatError(f"Size validation failed for {member}")
            actual_hash, actual_size = _sha256_member(archive, member)
            if actual_size != expected_size:
                raise BackupFormatError(f"Size validation failed for {member}")
            if actual_hash != expected_hash:
                raise BackupFormatError(f"Checksum validation failed for {member}")

        validate_native_configuration(archive, recorded_names)

        with tempfile.TemporaryDirectory(
            prefix="application-backup-check-"
        ) as temporary:
            database = Path(temporary) / "app.db"
            with (
                archive.open(DATABASE_MEMBER, "r") as source,
                database.open("wb") as target,
            ):
                shutil.copyfileobj(source, target, length=1024 * 1024)
            database_summary = validate_database(database, expected_revision)

    return ArchiveSummary(
        manifest=manifest,
        members=tuple(names),
        database=database_summary,
    )
