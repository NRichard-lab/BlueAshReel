from __future__ import annotations

import socket
from datetime import timedelta

from sqlalchemy import CursorResult, and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import AppConfig
from app.models import (
    BackgroundJob,
    BackgroundJobEvent,
    ScanJob,
    ScanLock,
    utcnow,
)


class ScanAlreadyRunning(RuntimeError):
    def __init__(self, job_id: str) -> None:
        super().__init__("A scan is already active for this library")
        self.job_id = job_id


def enqueue_scan(db: Session, library_id: str, mode: str) -> BackgroundJob:
    existing_lock = db.get(ScanLock, library_id)
    if existing_lock:
        existing_job = db.get(BackgroundJob, existing_lock.job_id)
        if existing_job and existing_job.status in {"queued", "running", "retry_wait"}:
            raise ScanAlreadyRunning(existing_job.id)
        db.delete(existing_lock)
        db.flush()

    job = BackgroundJob(job_type="library_scan", payload={"library_id": library_id, "mode": mode})
    db.add(job)
    db.flush()
    scan = ScanJob(job_id=job.id, library_id=library_id, scan_mode=mode)
    db.add(scan)
    db.add(ScanLock(library_id=library_id, job_id=job.id))
    db.add(
        BackgroundJobEvent(
            job_id=job.id,
            event_type="queued",
            message="Library scan queued",
        )
    )
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        active = db.get(ScanLock, library_id)
        raise ScanAlreadyRunning(active.job_id if active else "unknown") from exc
    return job


def release_scan_lock(db: Session, job_id: str) -> None:
    db.execute(delete(ScanLock).where(ScanLock.job_id == job_id))


def recover_stale_jobs(db: Session, config: AppConfig) -> tuple[int, int]:
    now = utcnow()
    stale_before = now - timedelta(minutes=config.job_stale_minutes)
    jobs = db.scalars(
        select(BackgroundJob).where(
            BackgroundJob.status == "running",
            or_(
                BackgroundJob.lease_expires_at < now,
                and_(
                    BackgroundJob.lease_expires_at.is_(None),
                    or_(
                        BackgroundJob.heartbeat_at < stale_before,
                        BackgroundJob.heartbeat_at.is_(None),
                    ),
                ),
            ),
        )
    ).all()
    recovered = failed = 0
    for job in jobs:
        job.locked_by = None
        job.lease_expires_at = None
        if job.cancel_requested:
            job.status = "cancelled"
            job.completed_at = now
            release_scan_lock(db, job.id)
            continue
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.completed_at = now
            job.error_summary = "Job did not complete before its worker stopped"
            release_scan_lock(db, job.id)
            failed += 1
        else:
            job.status = "queued"
            job.available_at = now
            job.error_summary = "Previous worker stopped; job safely queued to resume"
            recovered += 1
        db.add(
            BackgroundJobEvent(
                job_id=job.id,
                level="warning",
                event_type="recovered" if job.status == "queued" else "failed",
                message="Interrupted job recovered" if job.status == "queued" else "Interrupted job failed",
            )
        )
    db.commit()
    return recovered, failed


def claim_next_job(db: Session, config: AppConfig, worker_id: str | None = None) -> BackgroundJob | None:
    # This runs on every bounded worker poll, not only process startup. A replacement
    # worker that starts before an abandoned lease expires will therefore recover the
    # job as soon as the lease later becomes stale without requiring another restart.
    recover_stale_jobs(db, config)
    now = utcnow()
    worker = worker_id or f"{socket.gethostname()}:{id(db)}"
    lease_seconds = max(config.job_stale_minutes * 60, config.ffprobe_timeout_seconds * 2 + 30)
    candidate_ids = db.scalars(
        select(BackgroundJob.id)
        .where(
            BackgroundJob.status.in_(("queued", "retry_wait")),
            BackgroundJob.available_at <= now,
            BackgroundJob.cancel_requested.is_(False),
            BackgroundJob.attempts < BackgroundJob.max_attempts,
        )
        .order_by(BackgroundJob.priority.asc(), BackgroundJob.created_at.asc())
        .limit(10)
    ).all()
    for candidate_id in candidate_ids:
        result = db.execute(
            update(BackgroundJob)
            .where(
                BackgroundJob.id == candidate_id,
                BackgroundJob.status.in_(("queued", "retry_wait")),
                BackgroundJob.cancel_requested.is_(False),
            )
            .values(
                status="running",
                started_at=now,
                heartbeat_at=now,
                locked_by=worker,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                attempts=BackgroundJob.attempts + 1,
            )
        )
        if isinstance(result, CursorResult) and result.rowcount == 1:
            db.commit()
            job = db.get(BackgroundJob, candidate_id)
            if job:
                db.add(
                    BackgroundJobEvent(
                        job_id=job.id,
                        event_type="started",
                        message="Background job started",
                    )
                )
                db.commit()
            return job
        db.rollback()
    return None


def fail_job(db: Session, job: BackgroundJob, summary: str) -> None:
    now = utcnow()
    job.locked_by = None
    job.lease_expires_at = None
    job.error_summary = summary[:500]
    if job.attempts < job.max_attempts and not job.cancel_requested:
        job.status = "retry_wait"
        job.available_at = now + timedelta(seconds=min(300, 2**job.attempts))
        event_type = "retry_scheduled"
        message = "Job will retry after a local processing error"
    else:
        job.status = "cancelled" if job.cancel_requested else "failed"
        job.completed_at = now
        release_scan_lock(db, job.id)
        event_type = job.status
        message = "Job cancelled" if job.cancel_requested else "Job failed"
    db.add(
        BackgroundJobEvent(
            job_id=job.id,
            level="warning" if job.status == "retry_wait" else "error",
            event_type=event_type,
            message=message,
        )
    )
    db.commit()
