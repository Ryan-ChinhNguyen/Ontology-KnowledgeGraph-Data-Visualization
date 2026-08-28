from unittest.mock import AsyncMock, MagicMock

import pytest
from ontology_shared.messaging import (
    DEAD_QUEUE,
    RETRY_DELAYS_SECONDS,
    RETRY_QUEUE,
    JobMessage,
)

from app.errors import FileContentError
from app.messaging.consumer import JobConsumer

MAX_ATTEMPTS = 3


@pytest.fixture
def consumer() -> JobConsumer:
    instance = JobConsumer(max_attempts=MAX_ATTEMPTS)
    instance._channel = MagicMock()
    instance._channel.default_exchange.publish = AsyncMock()
    return instance


def incoming(job: JobMessage) -> AsyncMock:
    message = AsyncMock()
    message.body = job.encode()
    return message


def published(consumer: JobConsumer) -> tuple[JobMessage, str, object]:
    call = consumer._channel.default_exchange.publish.await_args
    sent = call.args[0]
    return JobMessage.decode(sent.body), call.kwargs["routing_key"], sent.expiration


@pytest.fixture
def processor(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    stub = AsyncMock()
    monkeypatch.setattr("app.messaging.consumer.process_job", stub)
    return stub


@pytest.fixture
def failing_processor(processor: AsyncMock) -> AsyncMock:
    processor.side_effect = ValueError("unreadable file")
    return processor


class TestSuccess:
    async def test_acknowledges_only_after_the_job_is_processed(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        message = incoming(job_message)

        await consumer._on_message(message)

        processor.assert_awaited_once()
        message.ack.assert_awaited_once()

    async def test_moves_the_message_nowhere(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message))

        consumer._channel.default_exchange.publish.assert_not_awaited()


class TestExponentialBackoff:
    async def test_first_failure_waits_the_shortest_delay(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message))

        _, destination, expiration = published(consumer)
        assert destination == RETRY_QUEUE
        assert expiration == RETRY_DELAYS_SECONDS[0]

    async def test_second_failure_waits_longer(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message.model_copy(update={"attempt": 1})))

        _, destination, expiration = published(consumer)
        assert destination == RETRY_QUEUE
        assert expiration == RETRY_DELAYS_SECONDS[1]

    async def test_delays_grow_rather_than_repeat(self) -> None:
        """A flat schedule would spend every attempt inside the same outage."""
        assert list(RETRY_DELAYS_SECONDS) == sorted(RETRY_DELAYS_SECONDS)
        assert len(set(RETRY_DELAYS_SECONDS)) == len(RETRY_DELAYS_SECONDS)

    async def test_carries_the_incremented_attempt(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message))

        retry, _, _ = published(consumer)
        assert retry.attempt == 1
        assert retry.job_id == job_message.job_id

    async def test_acknowledges_only_after_the_retry_is_confirmed(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_awaited_once()

    async def test_keeps_the_message_queued_when_the_retry_cannot_be_stored(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        """Without a confirmed retry there is nothing to take over from the
        original, so it must not be acknowledged."""
        consumer._channel.default_exchange.publish.side_effect = ConnectionError("broker gone")
        message = incoming(job_message)

        with pytest.raises(ConnectionError):
            await consumer._on_message(message)

        message.ack.assert_not_awaited()

    async def test_reports_the_attempt_to_the_processor(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message.model_copy(update={"attempt": 1})))

        assert failing_processor.await_args.kwargs["attempt"] == 2
        assert failing_processor.await_args.kwargs["is_final_attempt"] is False


class TestDeadLettering:
    async def test_parks_the_job_once_attempts_are_exhausted(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        exhausted = job_message.model_copy(update={"attempt": MAX_ATTEMPTS - 1})

        await consumer._on_message(incoming(exhausted))

        parked, destination, expiration = published(consumer)
        assert destination == DEAD_QUEUE
        assert expiration is None
        assert parked.job_id == job_message.job_id

    async def test_tells_the_processor_the_attempt_is_final(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(
            incoming(job_message.model_copy(update={"attempt": MAX_ATTEMPTS - 1}))
        )

        assert failing_processor.await_args.kwargs["is_final_attempt"] is True

    async def test_parks_a_malformed_body_unchanged(
        self, consumer: JobConsumer, processor: AsyncMock
    ) -> None:
        """The raw bytes are the only record of what arrived, so they are
        forwarded as they came rather than replaced."""
        message = AsyncMock()
        message.body = b"not json"

        await consumer._on_message(message)

        call = consumer._channel.default_exchange.publish.await_args
        assert call.args[0].body == b"not json"
        assert call.kwargs["routing_key"] == DEAD_QUEUE
        message.ack.assert_awaited_once()
        processor.assert_not_awaited()


class TestPermanentFailures:
    """Errors this service raises itself, where the cause rules out success."""

    @pytest.fixture
    def permanently_failing_processor(self, processor: AsyncMock) -> AsyncMock:
        processor.side_effect = FileContentError("JSON root must be an object or an array")
        return processor

    async def test_parks_without_spending_retries(
        self,
        consumer: JobConsumer,
        permanently_failing_processor: AsyncMock,
        job_message: JobMessage,
    ) -> None:
        message = incoming(job_message)

        await consumer._on_message(message)

        _, destination, _ = published(consumer)
        assert destination == DEAD_QUEUE
        message.ack.assert_awaited_once()

    async def test_does_not_wait_before_giving_up(
        self,
        consumer: JobConsumer,
        permanently_failing_processor: AsyncMock,
        job_message: JobMessage,
    ) -> None:
        """A delay would only postpone a failure that is already certain."""
        await consumer._on_message(incoming(job_message))

        _, destination, expiration = published(consumer)
        assert destination != RETRY_QUEUE
        assert expiration is None

    async def test_still_retries_an_unclassified_error(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        """Retrying stays the default: an unfamiliar error may well be
        transient, and a wasted attempt costs far less than abandoned work."""
        await consumer._on_message(incoming(job_message))

        _, destination, _ = published(consumer)
        assert destination == RETRY_QUEUE
