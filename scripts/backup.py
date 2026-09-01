#!/usr/bin/env python3
"""Create and validate a privacy-preserving application online backup."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.backup_format import (
        ARCHIVE_FORMAT,
        MANIFEST_VERSION,
        BackupFormatError,
        archive_prefix_from_name,
        installed_alembic_head,
        validate_backup_archive,
        validate_database,
    )
elif __package__:
    from .backup_format import (
        ARCHIVE_FORMAT,
        MANIFEST_VERSION,
        BackupFormatError,
        archive_prefix_from_name,
        installed_alembic_head,
        validate_backup_archive,
        validate_database,
    )
else:
    from backup_format import (
        ARCHIVE_FORMAT,
        MANIFEST_VERSION,
        BackupFormatError,
        archive_prefix_from_name,
        installed_alembic_head,
        validate_backup_archive,
        validate_database,
    )


class BackupError(RuntimeError):
    """A safe, user-facing backup failure."""


def parse_dotenv(path: Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is None or not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def resolve_setting(
    explicit: str | None,
    environment_key: str,
    dotenv: dict[str, str],
    default: str,
    base: Path,
) -> Path:
    raw = (
        explicit
        or os.environ.get(environment_key)
        or dotenv.get(environment_key)
        or default
    )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def database_from_url(url: str) -> Path | None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw_path = url[len(prefix) :]
    if raw_path.startswith("/"):
        return Path("/" + raw_path.lstrip("/"))
    return Path(raw_path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sqlite_check(path: Path, expected_revision: str) -> None:
    try:
        validate_database(path, expected_revision)
    except BackupFormatError as error:
        raise BackupError(str(error)) from error


def online_sqlite_backup(
    source: Path, destination: Path, expected_revision: str
) -> None:
    if not source.is_file():
        raise BackupError(f"SQLite database does not exist: {source}")
    source_connection = sqlite3.connect(
        f"file:{source.as_posix()}?mode=ro", uri=True, timeout=30
    )
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection, pages=256, sleep=0.050)
    finally:
        destination_connection.close()
        source_connection.close()
    sqlite_check(destination, expected_revision)


def copy_tree(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise BackupError(f"Refusing to follow a symlink backup source: {source}")
    if source.is_dir():
        symlink = next((path for path in source.rglob("*") if path.is_symlink()), None)
        if symlink is not None:
            raise BackupError(
                f"Refusing to follow a symlink in backup source: {symlink}"
            )
        shutil.copytree(source, destination, symlinks=False)


def read_product_name(config_directory: Path) -> str:
    try:
        payload = json.loads(
            (config_directory / "product.json").read_text(encoding="utf-8")
        )
        name = payload.get("name")
    except (OSError, json.JSONDecodeError, AttributeError) as error:
        raise BackupError("Product configuration is missing or invalid") from error
    if not isinstance(name, str) or not name.strip():
        raise BackupError("Product configuration must contain a non-empty name")
    return name.strip()


def contained_by(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def assert_isolated_paths(paths: dict[str, Path]) -> None:
    """Reject equal or nested backup/state paths before reading or writing them."""

    resolved = {label: path.resolve() for label, path in paths.items()}
    labels = list(resolved)
    for index, left_label in enumerate(labels):
        left = resolved[left_label]
        for right_label in labels[index + 1 :]:
            right = resolved[right_label]
            if contained_by(left, right) or contained_by(right, left):
                raise BackupError(
                    f"{left_label} and {right_label} paths must not overlap: {left} ; {right}"
                )


def write_manifest(
    stage: Path, created_at: str, database_revision: str
) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for file_path in sorted(path for path in stage.rglob("*") if path.is_file()):
        relative = file_path.relative_to(stage).as_posix()
        if relative == "manifest.json":
            continue
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(file_path),
                "size": file_path.stat().st_size,
            }
        )
    manifest: dict[str, object] = {
        "format": ARCHIVE_FORMAT,
        "format_version": MANIFEST_VERSION,
        "database_revision": database_revision,
        "created_at": created_at,
        "files": files,
    }
    (stage / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def validate_archive(path: Path, expected_revision: str | None = None) -> None:
    expected_revision = expected_revision or installed_alembic_head()
    try:
        validate_backup_archive(path, expected_revision)
    except BackupFormatError as error:
        raise BackupError(str(error)) from error


def apply_retention(
    directory: Path, retention_days: int, keep: Path, archive_prefix: str
) -> list[Path]:
    if retention_days < 1:
        raise BackupError("Retention days must be at least 1")
    cutoff = dt.datetime.now(dt.UTC).timestamp() - retention_days * 86400
    removed: list[Path] = []
    for candidate in directory.glob(f"{archive_prefix}*.zip"):
        resolved = candidate.resolve()
        if resolved == keep or not contained_by(resolved, directory):
            continue
        if candidate.is_file() and candidate.stat().st_mtime < cutoff:
            try:
                validate_archive(candidate)
            except (BackupError, OSError, sqlite3.Error, zipfile.BadZipFile):
                continue
            candidate.unlink()
            removed.append(candidate)
    return removed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an online SQLite backup plus local configuration and cache."
    )
    parser.add_argument("--env-file", help="Deployment .env file to include and read")
    parser.add_argument("--database", help="Path to the active SQLite database")
    parser.add_argument("--data", help="Application-data directory")
    parser.add_argument("--artwork", help="Artwork/cache directory")
    parser.add_argument("--temporary", help="Disposable temporary-work directory")
    parser.add_argument("--config", help="Product configuration directory")
    parser.add_argument("--output", help="Directory that receives backup archives")
    parser.add_argument("--retention-days", type=int, default=None)
    parser.add_argument("--skip-artwork", action="store_true")
    parser.add_argument(
        "--validate-only",
        metavar="ARCHIVE",
        help="Validate an existing archive without creating one",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    script_root = Path(__file__).resolve().parent
    repository_root = script_root.parent
    env_path_raw = arguments.env_file or os.environ.get("BACKUP_ENV_FILE")
    env_path = (
        Path(env_path_raw).resolve() if env_path_raw else repository_root / ".env"
    )
    dotenv = parse_dotenv(env_path)

    if arguments.validate_only:
        validate_archive(Path(arguments.validate_only).expanduser().resolve())
        print(f"Backup is valid: {Path(arguments.validate_only).name}")
        return 0

    database_explicit = arguments.database
    if database_explicit is None:
        database_url = os.environ.get("DATABASE_URL") or dotenv.get("DATABASE_URL", "")
        from_url = database_from_url(database_url) if database_url else None
        database_explicit = str(from_url) if from_url else None

    database_directory = resolve_setting(
        None, "DATABASE_PATH", dotenv, "runtime/database", repository_root
    )
    database = (
        Path(database_explicit).expanduser().resolve()
        if database_explicit
        else database_directory / "app.db"
    )
    data = resolve_setting(
        arguments.data, "APP_DATA_DIR", dotenv, "runtime/data", repository_root
    )
    artwork = resolve_setting(
        arguments.artwork, "ARTWORK_DIR", dotenv, "runtime/artwork", repository_root
    )
    temporary_work = resolve_setting(
        arguments.temporary,
        "BACKUP_TEMP_DIR",
        dotenv,
        dotenv.get("TEMP_PATH", "runtime/temp"),
        repository_root,
    )
    config = resolve_setting(
        arguments.config, "BACKUP_CONFIG_DIR", dotenv, "config", repository_root
    )
    product_name = read_product_name(config)
    archive_prefix = archive_prefix_from_name(product_name)
    expected_revision = installed_alembic_head()
    output = resolve_setting(
        arguments.output, "BACKUP_PATH", dotenv, "backups", repository_root
    )
    retention = arguments.retention_days
    if retention is None:
        retention = int(
            os.environ.get("BACKUP_RETENTION_DAYS")
            or dotenv.get("BACKUP_RETENTION_DAYS", "30")
        )

    assert_isolated_paths(
        {
            "database": database.parent,
            "application data": data,
            "artwork": artwork,
            "temporary work": temporary_work,
            "product configuration": config,
            "backup output": output,
        }
    )
    output.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.UTC)
    created_at = now.isoformat().replace("+00:00", "Z")
    archive_path = output / f"{archive_prefix}{now.strftime('%Y%m%dT%H%M%SZ')}.zip"
    if archive_path.exists():
        archive_path = (
            output
            / f"{archive_prefix}{now.strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}.zip"
        )

    with tempfile.TemporaryDirectory(
        prefix=".application-backup-stage-", dir=output
    ) as stage_directory:
        stage = Path(stage_directory)
        (stage / "database").mkdir()
        online_sqlite_backup(database, stage / "database" / "app.db", expected_revision)
        copy_tree(data, stage / "application-data")
        if not arguments.skip_artwork:
            copy_tree(artwork, stage / "artwork")
        copy_tree(config, stage / "configuration" / "product")
        if env_path.is_file():
            (stage / "configuration").mkdir(parents=True, exist_ok=True)
            shutil.copy2(env_path, stage / "configuration" / ".env")
        write_manifest(stage, created_at, expected_revision)
        temporary_archive = output / f".{archive_path.name}.partial"
        try:
            with zipfile.ZipFile(
                temporary_archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as archive:
                for file_path in sorted(
                    path for path in stage.rglob("*") if path.is_file()
                ):
                    archive.write(file_path, file_path.relative_to(stage).as_posix())
            validate_archive(temporary_archive, expected_revision)
            temporary_archive.replace(archive_path)
        finally:
            temporary_archive.unlink(missing_ok=True)

    with contextlib.suppress(OSError):
        os.chmod(archive_path, 0o600)
    removed = apply_retention(output, retention, archive_path, archive_prefix)
    print(f"{product_name} backup created and validated: {archive_path}")
    print(f"Retention removed {len(removed)} expired backup(s).")
    print(
        "Treat this archive as sensitive: it contains local configuration and an application secret."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        BackupError,
        BackupFormatError,
        OSError,
        sqlite3.Error,
        ValueError,
        zipfile.BadZipFile,
    ) as error:
        print(f"Backup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
