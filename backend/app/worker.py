from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import timedelta
from types import FrameType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_config
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.models import BackgroundJob, BackgroundJobEvent, PlaybackSession, utcnow
from app.services.jobs import claim_next_job, fail_job, recover_stale_jobs
from app.services.scanner import ScanInterrupted, run_scan

logger = logging.getLogger("worker")
_stopping = False


def _request_stop(_signal_number: int, _frame: FrameType | None) -> None:
    global _stopping
    _stopping = True


def requeue_interrupted(db: Session, job: BackgroundJob) -> None:
    db.rollback()
    current = db.get(BackgroundJob, job.id)
    if current is None:
        return
    current.status = "queued"
    current.locked_by = None
    current.lease_expires_at = None
    current.available_at = utcnow()
    current.attempts = max(0, current.attempts - 1)
    db.add(BackgroundJobEvent(
        job_id=current.id, level="info", event_type="service_stopped",
        message="Interrupted scan retained for worker restart",
    ))
    db.commit()


def run_worker(stop_event: threading.Event | None = None) -> None:
    global _stopping
    _stopping = False
    config = get_config()
    configure_logging(config.log_level)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    with SessionLocal() as db:
        recovered, failed = recover_stale_jobs(db, config)
        logger.info(
            "Worker started",
            extra={"fields": {"recovered_jobs": recovered, "failed_jobs": failed}},
        )
    def stopping() -> bool:
        return _stopping or stop_event is not None and stop_event.is_set()

    def pause() -> None:
        if stop_event is not None:
            stop_event.wait(config.worker_poll_interval)
        else:
            time.sleep(config.worker_poll_interval)

    while not stopping():
        with SessionLocal() as db:
            # New background scans yield to household conversion sessions.
            if db.scalar(
                select(PlaybackSession.id)
                .where(
                    PlaybackSession.state == "active",
                    PlaybackSession.method != "direct",
                    PlaybackSession.last_seen_at
                    > utcnow() - timedelta(seconds=config.playback_session_timeout_seconds),
                )
                .limit(1)
            ):
                pause()
                continue
            job = claim_next_job(db, config)
            if job is None:
                pause()
                continue
            try:
                if job.job_type == "library_scan":
                    if stop_event is None:
                        run_scan(db, job, config)
                    else:
                        run_scan(db, job, config, stop_requested=stopping)
                elif job.job_type == "metadata_enrich":
                    from app.metadata.service import run_metadata_job

                    run_metadata_job(db, job, config, stop_requested=stopping)
                else:
                    fail_job(db, job, "No local worker handler is registered for this job type")
            except ScanInterrupted:
                requeue_interrupted(db, job)
                logger.info("Background scan retained for worker restart")
            except Exception:
                db.rollback()
                current = db.get(type(job), job.id)
                if current is not None:
                    fail_job(db, current, "Local background processing did not complete")
                logger.warning("Background job attempt did not complete")
    logger.info("Worker stopped")


if __name__ == "__main__":
    run_worker()
