#!/usr/bin/env python3
"""Validate an application backup and print a non-destructive restore plan."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.backup_format import BackupFormatError, validate_backup_archive
elif __package__:
    from .backup_format import BackupFormatError, validate_backup_archive
else:
    from backup_format import BackupFormatError, validate_backup_archive


class ValidationError(RuntimeError):
    """A safe, user-facing validation failure."""


def validate(archive_path: Path) -> dict[str, object]:
    try:
        validated = validate_backup_archive(archive_path)
    except BackupFormatError as error:
        raise ValidationError(str(error)) from error
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = list(validated.members)
        manifest = validated.manifest
        records = manifest["files"]
        try:
            product_payload = json.loads(
                archive.read("configuration/product/product.json")
            )
            product_name = product_payload.get("name", "Application")
        except (KeyError, json.JSONDecodeError, AttributeError):
            product_name = "Application"
        if not isinstance(product_name, str) or not product_name.strip():
            product_name = "Application"
        return {
            "archive": archive_path.name,
            "created_at": manifest.get("created_at"),
            "database_bytes": validated.database.bytes,
            "database_pages": validated.database.pages,
            "database_revision": validated.database.alembic_revision,
            "file_count": len(records),
            "has_application_data": any(
                name.startswith("application-data/") for name in names
            ),
            "has_artwork": any(name.startswith("artwork/") for name in names),
            "has_environment": "configuration/.env" in names,
            "product_name": product_name,
            "table_count": len(validated.database.tables),
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-validate an application backup. This command never restores or overwrites files."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--json", action="store_true", help="Emit the validation summary as JSON"
    )
    arguments = parser.parse_args()
    summary = validate(arguments.archive.expanduser().resolve())
    if arguments.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"{summary['product_name']} backup is valid: {summary['archive']}")
        print(
            f"SQLite integrity: ok ({summary['database_pages']} pages, {summary['table_count']} tables)"
        )
        print(f"Database schema: exact Alembic head {summary['database_revision']}")
        print(f"Manifest checksums: ok ({summary['file_count']} files)")
        print(
            "Dry run only: no configuration, database, artwork, or application data was changed."
        )
        print(
            "Follow docs/backup-and-restore.md for the explicit, offline restore procedure."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        print(f"Restore validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
