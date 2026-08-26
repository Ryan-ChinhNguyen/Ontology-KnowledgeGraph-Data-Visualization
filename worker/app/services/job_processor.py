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
from app.parsers.base import NormalizedData
from app.parsers.registry import parser_for

log = logging.getLogger(__name__)


class JobNotFoundError(RuntimeError):
    def __init__(self, job_id: uuid.UUID) -> None:
        super().__init__(f"Job '{job_id}' does not exist")


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


async def process_job(message: JobMessage, *, is_final_attempt: bool) -> NormalizedData | None:
    """Parse the files of one session.

    Returns ``None`` when the job was already completed by an earlier delivery.
    Re-raises any parsing error after recording it, leaving the retry decision
    to the caller.
    """
    async with session_factory() as db:
        job = await db.get(Job, message.job_id)
        if job is None:
            raise JobNotFoundError(message.job_id)

        # Idempotency: a redelivered message for finished work is a no-op.
        if job.status is JobStatus.done:
            log.info("Job already completed, skipping: job_id=%s", message.job_id)
            return None

        session = await db.get(Session, message.session_id)
        await _mark_started(db, job, session, message.attempt)

        try:
            parser = parser_for(session.format)
            normalized = parser.parse(await _load_file_paths(db, session.session_id))
        except Exception as error:
            await _mark_failed(db, job, session, error, final=is_final_attempt)
            raise

        await _mark_succeeded(db, job, session)
        log.info(
            "Job parsed: job_id=%s tables=%d",
            message.job_id,
            len(normalized.tables),
        )
        return normalized
