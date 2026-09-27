"""Claiming, leasing, and recording job outcomes against a real PostgreSQL."""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from ontology_shared.messaging import JobMessage
from ontology_shared.models import FileFormat, JobStatus, SessionStatus
from sqlalchemy import func

from app.core.config import settings
from app.core.database import engine, session_factory
from app.errors import FileContentError, JobNotFoundError
from app.parsers.base import NormalizedData, Table
from app.services import job_processor
from app.services.job_processor import Outcome, claim_job, claim_statement, process_job, settle_job
from tests.conftest import force_job, job_row, session_status

LEASE = timedelta(seconds=settings.job_lease_seconds)


@pytest.fixture
def parser(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """A stand-in parser, so these tests exercise job bookkeeping only."""
    stub = MagicMock()
    stub.parse.return_value = NormalizedData(tables=[Table(name="people")])
    monkeypatch.setattr(job_processor, "parser_for", lambda _format: stub)
    return stub


class TestClaiming:
    async def test_runs_a_queued_job_and_records_success(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        result = await process_job(stored_job, attempt=1, is_final_attempt=False)

        assert result.outcome is Outcome.PROCESSED
        assert [table.name for table in result.data.tables] == ["people"]
        row = await job_row(stored_job.job_id)
        assert row.status is JobStatus.done
        assert row.attempt_count == 1
        assert row.completed_at is not None
        assert row.error_message is None
        assert await session_status(stored_job.session_id) is SessionStatus.ready

    async def test_only_one_of_two_racing_claims_succeeds(self, stored_job: JobMessage) -> None:
        """The second UPDATE waits on the row lock, then re-evaluates its WHERE
        clause against the committed row and no longer matches."""
        statement = claim_statement(stored_job.job_id, 1, LEASE)

        async with engine.connect() as first, engine.connect() as second:
            assert (await first.execute(statement)).one_or_none() is not None

            racing = asyncio.create_task(second.execute(statement))
            await asyncio.sleep(0.5)
            assert not racing.done(), "the second claim should be waiting on the row lock"

            await first.commit()
            assert (await racing).one_or_none() is None
            await second.rollback()

    async def test_leaves_a_job_another_worker_holds(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        await force_job(stored_job.job_id, status=JobStatus.processing, started_at=func.now())

        result = await process_job(stored_job, attempt=2, is_final_attempt=False)

        assert result.outcome is Outcome.IN_PROGRESS
        parser.parse.assert_not_called()
        row = await job_row(stored_job.job_id)
        assert row.status is JobStatus.processing
        assert row.attempt_count == 0

    async def test_takes_over_a_job_whose_lease_has_expired(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        """A worker that died mid-job leaves it in `processing`; the lease is
        what lets another worker recover it."""
        await force_job(
            stored_job.job_id,
            status=JobStatus.processing,
            started_at=func.now() - LEASE - timedelta(minutes=1),
        )

        result = await process_job(stored_job, attempt=2, is_final_attempt=False)

        assert result.outcome is Outcome.PROCESSED
        assert (await job_row(stored_job.job_id)).status is JobStatus.done

    @pytest.mark.parametrize("status", [JobStatus.done, JobStatus.failed])
    async def test_does_not_rerun_a_settled_job(
        self, stored_job: JobMessage, parser: MagicMock, status: JobStatus
    ) -> None:
        await force_job(stored_job.job_id, status=status)

        result = await process_job(stored_job, attempt=1, is_final_attempt=False)

        assert result.outcome is Outcome.ALREADY_SETTLED
        parser.parse.assert_not_called()
        assert (await job_row(stored_job.job_id)).status is status

    async def test_raises_when_the_job_does_not_exist(
        self, database: None, job_message: JobMessage, parser: MagicMock
    ) -> None:
        with pytest.raises(JobNotFoundError):
            await process_job(job_message, attempt=1, is_final_attempt=False)


class TestFencing:
    async def test_a_superseded_worker_cannot_record_its_result(
        self, stored_job: JobMessage
    ) -> None:
        async with session_factory() as db:
            stale = await claim_job(db, stored_job.job_id, attempt=1)
        # Another worker takes the job over, giving it a new start time.
        await force_job(stored_job.job_id, started_at=func.now() + timedelta(seconds=1))

        async with session_factory() as db:
            recorded = await settle_job(
                db,
                stale,
                job_values={"status": JobStatus.done},
                session_status=SessionStatus.ready,
            )

        assert recorded is False
        assert (await job_row(stored_job.job_id)).status is JobStatus.processing
        assert await session_status(stored_job.session_id) is SessionStatus.processing

    async def test_reports_being_superseded_instead_of_overwriting(
        self, stored_job: JobMessage, parser: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def load_then_lose_lease(db, session_id):
            await force_job(stored_job.job_id, started_at=func.now() + timedelta(seconds=1))
            return FileFormat.csv, ["/uploads/people.csv"]

        monkeypatch.setattr(job_processor, "_load_work", load_then_lose_lease)

        result = await process_job(stored_job, attempt=1, is_final_attempt=False)

        assert result.outcome is Outcome.SUPERSEDED
        assert (await job_row(stored_job.job_id)).status is JobStatus.processing


class TestFailure:
    @pytest.fixture
    def failing_parser(self, parser: MagicMock) -> MagicMock:
        parser.parse.side_effect = ValueError("column count mismatch on row 42")
        return parser

    async def test_returns_the_job_to_the_queue_when_retries_remain(
        self, stored_job: JobMessage, failing_parser: MagicMock
    ) -> None:
        with pytest.raises(ValueError, match="column count mismatch"):
            await process_job(stored_job, attempt=1, is_final_attempt=False)

        row = await job_row(stored_job.job_id)
        assert row.status is JobStatus.queued
        assert row.error_message == "column count mismatch on row 42"
        assert await session_status(stored_job.session_id) is SessionStatus.processing

    async def test_fails_the_session_on_the_final_attempt(
        self, stored_job: JobMessage, failing_parser: MagicMock
    ) -> None:
        with pytest.raises(ValueError):
            await process_job(stored_job, attempt=3, is_final_attempt=True)

        row = await job_row(stored_job.job_id)
        assert row.status is JobStatus.failed
        assert row.completed_at is not None
        assert await session_status(stored_job.session_id) is SessionStatus.failed

    async def test_fails_at_once_when_retrying_cannot_help(
        self, stored_job: JobMessage, parser: MagicMock
    ) -> None:
        parser.parse.side_effect = FileContentError("JSON root must be an object or an array")

        with pytest.raises(FileContentError):
            await process_job(stored_job, attempt=1, is_final_attempt=False)

        assert (await job_row(stored_job.job_id)).status is JobStatus.failed
        assert await session_status(stored_job.session_id) is SessionStatus.failed

    async def test_a_retried_job_can_be_claimed_again(
        self, stored_job: JobMessage, failing_parser: MagicMock
    ) -> None:
        with pytest.raises(ValueError):
            await process_job(stored_job, attempt=1, is_final_attempt=False)
        failing_parser.parse.side_effect = None

        result = await process_job(stored_job, attempt=2, is_final_attempt=False)

        assert result.outcome is Outcome.PROCESSED
        assert (await job_row(stored_job.job_id)).attempt_count == 2
