import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from ontology_shared.models import FileFormat, JobStatus, SessionStatus

from app.core.database import get_db
from app.dependencies import get_storage
from app.main import app
from app.services.storage import FileStorage


class InMemoryStorage(FileStorage):
    """Records what would have been written, so upload tests never touch disk."""

    def __init__(self) -> None:
        self.saved: dict[str, bytes] = {}

    def save(self, session_id: uuid.UUID, filename: str, content: bytes) -> str:
        location = f"memory://{session_id}/{filename}"
        self.saved[location] = content
        return location


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def db() -> AsyncMock:
    """An AsyncSession stand-in.

    ``execute`` is awaited but its Result is not, so the result is a plain
    MagicMock. It is pre-wired to return no rows, letting duplicate-hash
    lookups pass by default; tests that need a hit override ``scalars``.
    """
    result = MagicMock()
    result.scalars.return_value = []
    result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()  # sync in SQLAlchemy, unlike the rest of the session
    return session


@pytest.fixture
def client(db: AsyncMock, storage: InMemoryStorage) -> Iterator[AsyncClient]:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    try:
        yield AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def publisher() -> Iterator[AsyncMock]:
    """Stops tests from reaching RabbitMQ."""
    with patch("app.routers.upload.publish_job", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def stored_session() -> MagicMock:
    session = MagicMock()
    session.session_id = uuid.uuid4()
    session.format = FileFormat.csv
    session.status = SessionStatus.ready
    session.total_files = 2
    session.total_size_bytes = 4096
    session.created_at = datetime.now(timezone.utc)
    session.updated_at = datetime.now(timezone.utc)
    return session


@pytest.fixture
def stored_job() -> MagicMock:
    job = MagicMock()
    job.job_id = uuid.uuid4()
    job.status = JobStatus.done
    job.error_message = None
    return job
