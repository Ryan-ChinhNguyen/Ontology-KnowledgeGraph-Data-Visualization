from unittest.mock import AsyncMock, MagicMock

import pytest
from ontology_shared.messaging import JobMessage
from ontology_shared.models import JobStatus, SessionStatus

from app.parsers.base import NormalizedData, Table
from app.errors import JobNotFoundError
from app.services.job_processor import process_job


@pytest.fixture
def parser(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A stand-in parser, so these tests exercise job bookkeeping only."""
    stub = MagicMock()
    stub.parse.return_value = NormalizedData(tables=[Table(name="people")])
    monkeypatch.setattr("app.services.job_processor.parser_for", lambda _format: stub)
    return stub


class TestSuccessfulRun:
    async def test_marks_the_job_done_and_the_session_ready(
        self,
        session_factory: AsyncMock,
        parser: MagicMock,
        job_message: JobMessage,
        job: MagicMock,
        session: MagicMock,
    ) -> None:
        await process_job(job_message, attempt=1, is_final_attempt=False)

        assert job.status is JobStatus.done
        assert job.completed_at is not None
        assert job.error_message is None
        assert session.status is SessionStatus.ready

    async def test_returns_the_parsed_tables(
        self, session_factory: AsyncMock, parser: MagicMock, job_message: JobMessage
    ) -> None:
        result = await process_job(job_message, attempt=1, is_final_attempt=False)

        assert result is not None
        assert [table.name for table in result.tables] == ["people"]

    async def test_records_the_attempt_number(
        self,
        session_factory: AsyncMock,
        parser: MagicMock,
        job_message: JobMessage,
        job: MagicMock,
    ) -> None:
        await process_job(job_message, attempt=2, is_final_attempt=True)

        assert job.attempt_count == 2

    async def test_passes_the_session_files_to_the_parser(
        self, session_factory: AsyncMock, parser: MagicMock, job_message: JobMessage
    ) -> None:
        await process_job(job_message, attempt=1, is_final_attempt=False)

        parser.parse.assert_called_once_with(["/uploads/people.csv"])


class TestIdempotency:
    async def test_skips_a_job_that_already_completed(
        self,
        session_factory: AsyncMock,
        parser: MagicMock,
        job_message: JobMessage,
        job: MagicMock,
    ) -> None:
        job.status = JobStatus.done

        assert await process_job(job_message, attempt=1, is_final_attempt=False) is None
        parser.parse.assert_not_called()

    async def test_reprocesses_a_job_that_previously_failed(
        self,
        session_factory: AsyncMock,
        parser: MagicMock,
        job_message: JobMessage,
        job: MagicMock,
    ) -> None:
        job.status = JobStatus.failed

        await process_job(job_message, attempt=1, is_final_attempt=False)

        assert job.status is JobStatus.done
        parser.parse.assert_called_once()


class TestFailure:
    @pytest.fixture
    def failing_parser(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        stub = MagicMock()
        stub.parse.side_effect = ValueError("column count mismatch on row 42")
        monkeypatch.setattr("app.services.job_processor.parser_for", lambda _format: stub)
        return stub

    async def test_reraises_so_the_consumer_can_decide(
        self, session_factory: AsyncMock, failing_parser: MagicMock, job_message: JobMessage
    ) -> None:
        with pytest.raises(ValueError, match="column count mismatch"):
            await process_job(job_message, attempt=1, is_final_attempt=False)

    async def test_keeps_the_job_queued_when_retries_remain(
        self,
        session_factory: AsyncMock,
        failing_parser: MagicMock,
        job_message: JobMessage,
        job: MagicMock,
        session: MagicMock,
    ) -> None:
        with pytest.raises(ValueError):
            await process_job(job_message, attempt=1, is_final_attempt=False)

        assert job.status is JobStatus.queued
        assert job.error_message == "column count mismatch on row 42"
        assert session.status is SessionStatus.processing

    async def test_fails_the_session_on_the_final_attempt(
        self,
        session_factory: AsyncMock,
        failing_parser: MagicMock,
        job_message: JobMessage,
        job: MagicMock,
        session: MagicMock,
    ) -> None:
        with pytest.raises(ValueError):
            await process_job(job_message, attempt=1, is_final_attempt=True)

        assert job.status is JobStatus.failed
        assert job.completed_at is not None
        assert session.status is SessionStatus.failed

    async def test_raises_when_the_job_row_is_missing(
        self, session_factory: AsyncMock, db: AsyncMock, job_message: JobMessage
    ) -> None:
        db.get = AsyncMock(return_value=None)

        with pytest.raises(JobNotFoundError):
            await process_job(job_message, attempt=1, is_final_attempt=False)
