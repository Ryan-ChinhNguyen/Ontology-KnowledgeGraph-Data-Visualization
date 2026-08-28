import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from ontology_shared.messaging import JobMessage
from ontology_shared.models import FileFormat, JobStatus, SessionStatus


@pytest.fixture
def job_message() -> JobMessage:
    return JobMessage(job_id=uuid.uuid4(), session_id=uuid.uuid4())


@pytest.fixture
def job() -> MagicMock:
    record = MagicMock()
    record.status = JobStatus.queued
    record.attempt_count = 0
    record.error_message = None
    return record


@pytest.fixture
def session() -> MagicMock:
    record = MagicMock()
    record.session_id = uuid.uuid4()
    record.format = FileFormat.csv
    record.status = SessionStatus.queued
    return record


@pytest.fixture
def db(job: MagicMock, session: MagicMock) -> AsyncMock:
    """An AsyncSession stand-in that returns the job and session fixtures."""
    result = MagicMock()
    result.scalars.return_value = ["/uploads/people.csv"]

    database = AsyncMock()
    database.execute = AsyncMock(return_value=result)
    database.get = AsyncMock(side_effect=lambda model, _id: job if model.__name__ == "Job" else session)
    return database


@pytest.fixture
def session_factory(db: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace the module-level session factory with one yielding ``db``."""

    @asynccontextmanager
    async def factory():
        yield db

    monkeypatch.setattr("app.services.job_processor.session_factory", factory)
    return db
