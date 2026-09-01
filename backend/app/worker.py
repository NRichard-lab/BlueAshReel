from __future__ import annotations

import logging
import signal
import time
from datetime import timedelta
from types import FrameType

from sqlalchemy import select

from app.config import get_config
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.models import PlaybackSession, utcnow
from app.services.jobs import claim_next_job, fail_job, recover_stale_jobs
from app.services.scanner import run_scan

logger = logging.getLogger("worker")
_stopping = False


def _request_stop(_signal_number: int, _frame: FrameType | None) -> None:
    global _stopping
    _stopping = True


def run_worker() -> None:
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
    while not _stopping:
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
                time.sleep(config.worker_poll_interval)
                continue
            job = claim_next_job(db, config)
            if job is None:
                time.sleep(config.worker_poll_interval)
                continue
            try:
                if job.job_type == "library_scan":
                    run_scan(db, job, config)
                else:
                    fail_job(db, job, "No local worker handler is registered for this job type")
            except Exception:
                db.rollback()
                current = db.get(type(job), job.id)
                if current is not None:
                    fail_job(db, current, "Local background processing did not complete")
                logger.warning("Background job attempt did not complete")
    logger.info("Worker stopped")


if __name__ == "__main__":
    run_worker()
