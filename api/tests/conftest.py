import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.db import FileFormat, JobStatus, SessionStatus


@pytest.fixture
def session_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def job_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_session(session_id: uuid.UUID) -> MagicMock:
    s = MagicMock()
    s.session_id = session_id
    s.format = FileFormat.csv
    s.status = SessionStatus.queued
    s.total_files = 1
    s.total_size_bytes = 17
    s.created_at = datetime.now(timezone.utc)
    s.updated_at = datetime.now(timezone.utc)
    return s


@pytest.fixture
def mock_job(job_id: uuid.UUID) -> MagicMock:
    j = MagicMock()
    j.job_id = job_id
    j.status = JobStatus.queued
    j.error_message = None
    j.queued_at = datetime.now(timezone.utc)
    return j


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
