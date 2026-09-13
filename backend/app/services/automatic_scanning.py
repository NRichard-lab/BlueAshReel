"""One bounded coordinator feeds the existing scan queue; never probes media."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import ApplicationSetting, Library, LibraryPath, utcnow
from app.services.general_settings import read_general
from app.services.jobs import ScanAlreadyRunning, enqueue_scan
from app.services.library_settings import read_library_settings
from app.services.paths import validate_media_directory

logger = logging.getLogger("automatic_scanning")


def scheduled_slot(schedule: Any, now: datetime) -> str | None:
    if schedule.frequency == "off" or now.strftime("%H:%M") < schedule.time:
        return None
    if schedule.frequency == "weekly" and now.weekday() != schedule.weekday:
        return None
    # Local date prevents duplicate work in the repeated hour at DST fall-back.
    # A skipped spring-forward time runs on the first tick after that time.
    return f"{now.date()}:{schedule.frequency}:{schedule.time}:{schedule.weekday}"


def tick(factory: Any, config: Any, pending: dict[str, float], now: datetime | None = None) -> tuple[str, ...]:
    with factory() as db:
        policy = read_library_settings(db).scan_policy
        if not policy.automatic_scanning:
            pending.clear()
            return ()
        local = (now or utcnow()).astimezone(ZoneInfo(read_general(db, config).language_region.timezone))
        slot = scheduled_slot(policy.schedule, local)
        rows = db.execute(
            select(LibraryPath.library_id, LibraryPath.canonical_path)
            .join(Library)
            .where(Library.enabled.is_(True), LibraryPath.enabled.is_(True))
        ).all()
        roots: dict[str, list[str]] = {}
        for library_id, path in rows:
            roots.setdefault(library_id, []).append(path)
        for library_id in list(pending):
            if library_id not in roots or not policy.scan_on_change:
                pending.pop(library_id, None)
        for library_id in roots:
            key = f"scanner.schedule.{library_id}"
            previous = db.get(ApplicationSetting, key)
            due = bool(slot and (previous is None or previous.value != slot))
            changed = policy.scan_on_change and pending.get(library_id, float("inf")) <= time.monotonic()
            if not due and not changed:
                continue
            try:
                job = enqueue_scan(db, library_id, "changed")
            except ScanAlreadyRunning:
                continue
            job.payload = {**job.payload, "trigger": "schedule" if due else "watcher"}
            if due:
                db.merge(ApplicationSetting(key=key, value=slot))
            db.commit()
            pending.pop(library_id, None)
        if not policy.scan_on_change:
            return ()
        safe_roots = []
        for paths in roots.values():
            for path in paths:
                try:
                    safe_roots.append(str(validate_media_directory(path, config)))
                except (OSError, ValueError):
                    # Offline roots are retained in configuration and retried later.
                    continue
        return tuple(sorted(set(safe_roots)))


def record_changes(factory: Any, changes: set[Any], pending: dict[str, float]) -> None:
    with factory() as db:
        roots = db.execute(
            select(LibraryPath.library_id, LibraryPath.canonical_path)
            .join(Library)
            .where(Library.enabled.is_(True), LibraryPath.enabled.is_(True))
        ).all()
        for library_id, root in roots:
            if any(Path(path).is_relative_to(Path(root)) for _, path in changes):
                # One pending item per library, including activity during an active scan.
                pending[library_id] = time.monotonic() + 30


def run_automatic_scanning(factory: Any, config: Any, stop: threading.Event) -> None:
    from watchfiles import watch

    pending: dict[str, float] = {}
    while not stop.is_set():
        try:
            roots = tick(factory, config, pending)
            if not roots:
                stop.wait(5)
                continue
            for changes in watch(
                *roots,
                stop_event=stop,
                debounce=5000,
                step=1000,
                rust_timeout=5000,
                yield_on_timeout=True,
                force_polling=False,
            ):
                if changes:
                    record_changes(factory, changes, pending)
                if tick(factory, config, pending) != roots:
                    break
        except Exception:
            # Never emit path-bearing watcher exceptions or allow a retry storm.
            logger.warning("Automatic scan coordination unavailable; retrying in 30 seconds")
            stop.wait(30)
