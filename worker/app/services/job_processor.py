"""Runs one parse job and records its outcome.

Transport concerns — acknowledgement, retry, dead-lettering — belong to the
consumer. This module decides whether this worker may run the job, runs it,
and records what happened.

RabbitMQ delivers at least once and may hand the same job to two workers, so
checking the job's status and then acting on it is not enough: both workers
could pass the check. Instead a worker *claims* the job with one conditional
UPDATE. When two such updates race on the same row, PostgreSQL makes the
second wait for the first to commit and then re-evaluates its WHERE clause
against the updated row, so exactly one claim succeeds.

A claim is a lease. A worker that dies mid-job leaves the job in
``processing``; once the lease has expired another worker may claim it again.
Because that takeover can happen while the original worker is merely slow,
every write after the claim is conditioned on the claim still being held —
matched on the claim's start time — so a worker that has been superseded
cannot overwrite the result of the one that took over.
"""

import enum
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from ontology_shared.messaging import JobMessage
from ontology_shared.models import File, FileFormat, Job, JobStatus, Session, SessionStatus
from sqlalchemy import Update, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import session_factory
from app.errors import JobNotFoundError, PermanentJobError
from app.parsers.base import NormalizedData
from app.parsers.registry import parser_for

log = logging.getLogger(__name__)

SETTLED_STATUSES = frozenset({JobStatus.done, JobStatus.failed})


class Outcome(enum.Enum):
    #: This worker claimed the job and it parsed successfully.
    PROCESSED = "processed"
    #: The job had already finished, successfully or not; nothing to do.
    ALREADY_SETTLED = "already_settled"
    #: Another worker holds the job and its lease has not expired.
    IN_PROGRESS = "in_progress"
    #: This worker's lease was taken over before it could record a result.
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class JobResult:
    outcome: Outcome
    data: NormalizedData | None = None


@dataclass(frozen=True)
class Claim:
    job_id: uuid.UUID
    session_id: uuid.UUID
    #: Set by the claim itself, so it identifies this particular hold on the job.
    started_at: datetime


def claim_statement(job_id: uuid.UUID, attempt: int, lease: timedelta) -> Update:
    """The UPDATE that claims a job.

    A job can be claimed while it waits in the queue, or when it is recorded as
    in progress but its lease has run out. Times come from the database clock,
    so workers on hosts whose clocks disagree still agree on whether a lease
    has expired.
    """
    lease_expired = and_(
        Job.status == JobStatus.processing,
        Job.started_at < func.now() - lease,
    )
    return (
        update(Job)
        .where(Job.job_id == job_id, or_(Job.status == JobStatus.queued, lease_expired))
        .values(status=JobStatus.processing, started_at=func.now(), attempt_count=attempt)
        .returning(Job.session_id, Job.started_at)
        .execution_options(synchronize_session=False)
    )


async def claim_job(db: AsyncSession, job_id: uuid.UUID, attempt: int) -> Claim | None:
    """Claim a job and mark its session as processing, in one transaction."""
    lease = timedelta(seconds=settings.job_lease_seconds)
    row = (await db.execute(claim_statement(job_id, attempt, lease))).one_or_none()
    if row is None:
        await db.rollback()
        return None

    await db.execute(
        update(Session)
        .where(Session.session_id == row.session_id)
        .values(status=SessionStatus.processing)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return Claim(job_id=job_id, session_id=row.session_id, started_at=row.started_at)


async def settle_job(
    db: AsyncSession,
    claim: Claim,
    *,
    job_values: dict,
    session_status: SessionStatus | None,
) -> bool:
    """Record a result, provided the claim is still held.

    Returns ``False`` without writing anything when another worker has taken
    the job over.
    """
    written = await db.execute(
        update(Job)
        .where(
            Job.job_id == claim.job_id,
            Job.status == JobStatus.processing,
            Job.started_at == claim.started_at,
        )
        .values(**job_values)
        .execution_options(synchronize_session=False)
    )
    if written.rowcount == 0:
        await db.rollback()
        return False

    if session_status is not None:
        await db.execute(
            update(Session)
            .where(Session.session_id == claim.session_id)
            .values(status=session_status)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    return True


async def _unclaimed_outcome(db: AsyncSession, job_id: uuid.UUID) -> Outcome:
    status = (await db.execute(select(Job.status).where(Job.job_id == job_id))).scalar_one_or_none()
    await db.rollback()

    if status is None:
        raise JobNotFoundError(f"Job '{job_id}' does not exist")
    if status in SETTLED_STATUSES:
        return Outcome.ALREADY_SETTLED
    return Outcome.IN_PROGRESS


async def _load_work(db: AsyncSession, session_id: uuid.UUID) -> tuple[FileFormat, list[str]]:
    file_format = (
        await db.execute(select(Session.format).where(Session.session_id == session_id))
    ).scalar_one()
    paths = (
        await db.execute(
            select(File.stored_path)
            .where(File.session_id == session_id)
            .order_by(File.original_filename)
        )
    ).scalars()
    work = file_format, list(paths)
    # End the read transaction before parsing, so a connection is not held
    # idle in a transaction for as long as the parse takes.
    await db.rollback()
    return work


def _failure_values(error: Exception, *, final: bool) -> dict:
    if final:
        return {"status": JobStatus.failed, "error_message": str(error), "completed_at": func.now()}
    return {"status": JobStatus.queued, "error_message": str(error)}


async def process_job(message: JobMessage, *, attempt: int, is_final_attempt: bool) -> JobResult:
    """Claim and parse the files of one session.

    Re-raises a parsing error once it has been recorded, leaving the retry
    decision to the caller. A failure that retrying cannot fix ends the job
    whatever attempt it is on.
    """
    async with session_factory() as db:
        claim = await claim_job(db, message.job_id, attempt)
        if claim is None:
            outcome = await _unclaimed_outcome(db, message.job_id)
            log.info("Job not claimed (%s): job_id=%s", outcome.value, message.job_id)
            return JobResult(outcome)

        try:
            file_format, paths = await _load_work(db, claim.session_id)
            normalized = parser_for(file_format).parse(paths)
        except Exception as error:
            final = is_final_attempt or isinstance(error, PermanentJobError)
            recorded = await settle_job(
                db,
                claim,
                job_values=_failure_values(error, final=final),
                session_status=SessionStatus.failed if final else None,
            )
            if not recorded:
                log.warning("Job taken over before its failure was recorded: job_id=%s", claim.job_id)
                return JobResult(Outcome.SUPERSEDED)
            raise

        recorded = await settle_job(
            db,
            claim,
            job_values={"status": JobStatus.done, "error_message": None, "completed_at": func.now()},
            session_status=SessionStatus.ready,
        )
        if not recorded:
            log.warning("Job taken over before its result was recorded: job_id=%s", claim.job_id)
            return JobResult(Outcome.SUPERSEDED)

        log.info("Job parsed: job_id=%s tables=%d", claim.job_id, len(normalized.tables))
        return JobResult(Outcome.PROCESSED, normalized)
