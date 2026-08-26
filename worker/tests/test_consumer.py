from unittest.mock import AsyncMock, MagicMock

import pytest
from ontology_shared.messaging import JOB_QUEUE, JobMessage

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


@pytest.fixture
def processor(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    stub = AsyncMock()
    monkeypatch.setattr("app.messaging.consumer.process_job", stub)
    return stub


class TestSuccess:
    async def test_acknowledges_the_message(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_awaited_once()
        message.reject.assert_not_awaited()

    async def test_does_not_republish(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message))

        consumer._channel.default_exchange.publish.assert_not_awaited()


class TestRetry:
    async def test_republishes_with_the_attempt_incremented(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        processor.side_effect = ValueError("unreadable file")

        await consumer._on_message(incoming(job_message))

        publish = consumer._channel.default_exchange.publish
        publish.assert_awaited_once()
        republished = JobMessage.decode(publish.await_args.args[0].body)
        assert republished.attempt == 1
        assert republished.job_id == job_message.job_id
        assert publish.await_args.kwargs["routing_key"] == JOB_QUEUE

    async def test_acknowledges_the_original_after_republishing(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        processor.side_effect = ValueError("unreadable file")
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_awaited_once()
        message.reject.assert_not_awaited()

    async def test_tells_the_processor_the_attempt_is_not_final(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        processor.side_effect = ValueError("unreadable file")

        await consumer._on_message(incoming(job_message))

        assert processor.await_args.kwargs["is_final_attempt"] is False


class TestDeadLettering:
    async def test_rejects_once_attempts_are_exhausted(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        processor.side_effect = ValueError("unreadable file")
        exhausted = job_message.model_copy(update={"attempt": MAX_ATTEMPTS - 1})
        message = incoming(exhausted)

        await consumer._on_message(message)

        message.reject.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_awaited()
        consumer._channel.default_exchange.publish.assert_not_awaited()

    async def test_tells_the_processor_the_attempt_is_final(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        processor.side_effect = ValueError("unreadable file")
        exhausted = job_message.model_copy(update={"attempt": MAX_ATTEMPTS - 1})

        await consumer._on_message(incoming(exhausted))

        assert processor.await_args.kwargs["is_final_attempt"] is True

    async def test_rejects_a_malformed_body_without_retrying(
        self, consumer: JobConsumer, processor: AsyncMock
    ) -> None:
        message = AsyncMock()
        message.body = b"not json"

        await consumer._on_message(message)

        message.reject.assert_awaited_once_with(requeue=False)
        processor.assert_not_awaited()
