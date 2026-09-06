"""Explicit per-user uninstall cleanup, restricted to the validated installation."""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from app import native_install
from app.native_runtime import load_configuration, load_installation
from app.native_user_install import _filesystem_path, _no_link_tree, validate_storage
from app.services.paths import assert_no_link_components
from app.services.process_supervisor import lock_file, unlock_file


def overlaps(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def removal_plan(program: Path, data: Path, instance: str) -> list[Path]:
    """Validate every target and source-media boundary before the first deletion."""
    program, data = native_install.validate_layout(program, data)
    record = native_install.read_installation(data)
    if (record.get("runtime_mode") != "per_user" or record.get("instance") != instance
        or Path(record["program_dir"]) != program or "storage" not in record):
        raise ValueError("The selected data does not belong to this per-user installation")
    storage = validate_storage(program, data, record["storage"])
    if storage != record["storage"]:
        raise ValueError("The recorded storage locations are not canonical")
    installation = load_installation(data)
    configuration = load_configuration(installation)
    database = Path(configuration.database_url.removeprefix("sqlite:///"))
    configured = {"database": database.parent, "app_data": configuration.app_data_dir,
                  "artwork": configuration.artwork_dir, "temp": configuration.temp_dir}
    if any(Path(storage[key]) != value for key, value in configured.items()):
        raise ValueError("The application configuration differs from its recorded storage")
    roots = [Path(value) for value in storage.values() if not Path(value).is_relative_to(data)] + [data]
    # A corrupt or manually edited record must never authorize deleting a user
    # profile, Windows folder, or an ancestor containing unrelated installations.
    protected = [program, Path.home()]
    protected.extend(Path(value) for key in ("WINDIR", "ProgramFiles", "ProgramFiles(x86)",
                                             "ProgramData", "LOCALAPPDATA", "APPDATA")
                     if (value := os.environ.get(key)))
    if any(root == folder or folder.is_relative_to(root) for root in roots for folder in protected):
        raise ValueError("Uninstall cannot remove a shared system or user directory")
    media = [root.path.resolve(strict=False) for root in configuration.approved_media_roots]
    if database.exists():
        native_install._private_file(database)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("The application database could not be validated")
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_paths'").fetchone():
                media.extend(Path(row[0]).resolve(strict=False)
                             for row in connection.execute("SELECT canonical_path FROM library_paths"))
    if any(overlaps(root, source) for root in roots for source in media):
        raise ValueError("Application data overlaps a media folder; automatic removal was refused")
    for root in roots:
        assert_no_link_components(root)
        if root.exists():
            if not root.is_dir():
                raise ValueError("Application storage is not a directory")
            _no_link_tree(root)
    return roots


def remove_user_data(program: Path, data: Path, instance: str, *, confirmed: bool) -> None:
    if not confirmed:
        raise ValueError("Explicit data deletion confirmation is required")
    roots = removal_plan(program, data, instance)
    lock_path = data / "state/tray-runtime.lock"
    ownership = lock_file(lock_path)
    try:
        # The supervisor lock prevents cleanup of a live Agent. Keep the state
        # directory until its lock is released; every other root was preflighted.
        for root in roots:
            if not root.exists():
                continue
            children = list(root.iterdir()) if root == data else [root]
            for child in children:
                if child == data / "state":
                    continue
                assert_no_link_components(child)
                if child.is_dir():
                    _no_link_tree(child)
                    shutil.rmtree(_filesystem_path(child))
                else:
                    child.unlink()
    finally:
        unlock_file(ownership)
    state = data / "state"
    _no_link_tree(state)
    shutil.rmtree(_filesystem_path(state))
    assert_no_link_components(data)
    data.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--instance", choices=("development", "stable"), required=True)
    parser.add_argument("--confirm-delete-data", action="store_true")
    args = parser.parse_args()
    try:
        remove_user_data(args.program_dir, args.data_dir, args.instance, confirmed=args.confirm_delete_data)
    except Exception:  # noqa: BLE001 -- Uninstall boundary must redact all failure details.
        # Never export database content, identity bytes or configured paths.
        print("Local data removal could not complete safely. Retained data requires recovery review.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
