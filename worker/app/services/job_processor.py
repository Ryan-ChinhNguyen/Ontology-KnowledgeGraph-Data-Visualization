"""Runs one parse job and records its outcome.

Transport concerns — acknowledgement, retry, dead-lettering — belong to the
consumer. This module only decides what the job does and what the database
should say about it afterwards.
"""

import logging
import uuid

from ontology_shared.clock import utc_now
from ontology_shared.messaging import JobMessage
from ontology_shared.models import File, Job, JobStatus, Session, SessionStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_factory
from app.errors import JobNotFoundError, PermanentJobError
from app.parsers.base import NormalizedData
from app.parsers.registry import parser_for

log = logging.getLogger(__name__)


async def _load_file_paths(db: AsyncSession, session_id: uuid.UUID) -> list[str]:
    result = await db.execute(
        select(File.stored_path)
        .where(File.session_id == session_id)
        .order_by(File.original_filename)
    )
    return list(result.scalars())


async def _mark_started(db: AsyncSession, job: Job, session: Session, attempt: int) -> None:
    job.status = JobStatus.processing
    job.started_at = utc_now()
    job.attempt_count = attempt
    session.status = SessionStatus.processing
    await db.commit()


async def _mark_succeeded(db: AsyncSession, job: Job, session: Session) -> None:
    job.status = JobStatus.done
    job.completed_at = utc_now()
    job.error_message = None
    session.status = SessionStatus.ready
    await db.commit()


async def _mark_failed(
    db: AsyncSession, job: Job, session: Session, error: Exception, *, final: bool
) -> None:
    """Record a failure.

    A non-final failure keeps the job queued so a retry can pick it up; only
    an exhausted job moves the session to ``failed``.
    """
    job.error_message = str(error)
    if final:
        job.status = JobStatus.failed
        job.completed_at = utc_now()
        session.status = SessionStatus.failed
    else:
        job.status = JobStatus.queued
    await db.commit()


async def process_job(
    message: JobMessage, *, attempt: int, is_final_attempt: bool
) -> NormalizedData | None:
    """Parse the files of one session.

    ``attempt`` is recorded against the job so the database reflects how many
    deliveries it took; the count itself is kept by the broker, which is the
    only party that sees every delivery.

    Returns ``None`` when the job was already completed by an earlier delivery.
    Re-raises any parsing error after recording it, leaving the retry decision
    to the caller.
    """
    async with session_factory() as db:
        job = await db.get(Job, message.job_id)
        if job is None:
            raise JobNotFoundError(f"Job '{message.job_id}' does not exist")

        # Idempotency: a redelivered message for finished work is a no-op.
        if job.status is JobStatus.done:
            log.info("Job already completed, skipping: job_id=%s", message.job_id)
            return None

        session = await db.get(Session, message.session_id)
        await _mark_started(db, job, session, attempt)

        try:
            parser = parser_for(session.format)
            normalized = parser.parse(await _load_file_paths(db, session.session_id))
        except Exception as error:
            # A failure that retrying cannot fix ends the job now, whatever
            # attempt it is on, so the session reports what actually happened
            # instead of sitting in `processing` through retries that are
            # certain to fail the same way.
            settled = is_final_attempt or isinstance(error, PermanentJobError)
            await _mark_failed(db, job, session, error, final=settled)
            raise

        await _mark_succeeded(db, job, session)
        log.info(
            "Job parsed: job_id=%s tables=%d",
            message.job_id,
            len(normalized.tables),
        )
        return normalized
