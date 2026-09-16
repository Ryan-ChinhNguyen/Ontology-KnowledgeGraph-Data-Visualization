import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from ontology_shared.messaging import JobMessage
from ontology_shared.models import File, FileFormat, Job, JobStatus, Session, SessionStatus
from sqlalchemy import delete, select, text, update

from app.core.database import engine, session_factory


@pytest.fixture
def job_message() -> JobMessage:
    return JobMessage(job_id=uuid.uuid4(), session_id=uuid.uuid4())


@pytest.fixture
async def database() -> AsyncIterator[None]:
    """A reachable PostgreSQL with the schema applied, or a skipped test.

    Claiming relies on how PostgreSQL resolves concurrent updates to one row,
    which a mock cannot show, so those tests run against the real database.

    The engine is disposed afterwards because each test runs on its own event
    loop, and pooled connections cannot be carried from one loop to the next.
    """
    try:
        async with session_factory() as db:
            await db.execute(text("SELECT 1 FROM jobs LIMIT 1"))
    except Exception as error:
        await engine.dispose()
        pytest.skip(f"PostgreSQL with the schema applied is not reachable: {error}")

    yield
    await engine.dispose()


@pytest.fixture
async def stored_job(database: None) -> AsyncIterator[JobMessage]:
    """A queued job with its session and one file, removed after the test."""
    session_id, job_id = uuid.uuid4(), uuid.uuid4()

    async with session_factory() as db:
        db.add(
            Session(
                session_id=session_id,
                format=FileFormat.csv,
                total_files=1,
                total_size_bytes=16,
                status=SessionStatus.queued,
            )
        )
        await db.flush()
        db.add(
            File(
                session_id=session_id,
                original_filename="people.csv",
                sha256_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                size_bytes=16,
                stored_path="/uploads/people.csv",
            )
        )
        db.add(Job(job_id=job_id, session_id=session_id, status=JobStatus.queued))
        await db.commit()

    yield JobMessage(job_id=job_id, session_id=session_id)

    async with session_factory() as db:
        await db.execute(delete(Session).where(Session.session_id == session_id))
        await db.commit()


async def job_row(job_id: uuid.UUID) -> Any:
    async with session_factory() as db:
        return (await db.execute(select(Job.__table__).where(Job.job_id == job_id))).one()


async def session_status(session_id: uuid.UUID) -> SessionStatus:
    async with session_factory() as db:
        return (
            await db.execute(select(Session.status).where(Session.session_id == session_id))
        ).scalar_one()


async def force_job(job_id: uuid.UUID, **values: Any) -> None:
    async with session_factory() as db:
        await db.execute(update(Job).where(Job.job_id == job_id).values(**values))
        await db.commit()
