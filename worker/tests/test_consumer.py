import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aio_pika.exceptions import DeliveryError
from ontology_shared.messaging import (
    DEAD_QUEUE,
    JOB_QUEUE,
    RETRY_DELAYS_SECONDS,
    RETRY_QUEUE,
    JobMessage,
)

from app.core.config import settings
from app.errors import FileContentError
from app.messaging.consumer import JobConsumer
from app.services.job_processor import JobResult, Outcome

MAX_ATTEMPTS = 3
CONSUMER_TAG = "worker-tag"


@pytest.fixture
def consumer() -> JobConsumer:
    instance = JobConsumer(max_attempts=MAX_ATTEMPTS)
    instance._channel = MagicMock()
    instance._channel.default_exchange.publish = AsyncMock()
    instance._channel.ready = AsyncMock()
    instance._queues = {
        name: MagicMock(declare=AsyncMock(), consume=AsyncMock())
        for name in (DEAD_QUEUE, RETRY_QUEUE, JOB_QUEUE)
    }
    instance._consumer_tag = CONSUMER_TAG
    return instance


def subscribed_to(consumer: JobConsumer, *tags: str) -> None:
    """Make the channel report these subscriptions as active."""
    underlay = MagicMock(consumers={tag: object() for tag in tags})
    consumer._channel.get_underlay_channel = AsyncMock(return_value=underlay)


def incoming(job: JobMessage) -> AsyncMock:
    message = AsyncMock()
    message.body = job.encode()
    return message


def published(consumer: JobConsumer) -> tuple[JobMessage, str, object]:
    call = consumer._channel.default_exchange.publish.await_args
    sent = call.args[0]
    return JobMessage.decode(sent.body), call.kwargs["routing_key"], sent.expiration


def publish_calls(consumer: JobConsumer) -> int:
    return consumer._channel.default_exchange.publish.await_count


@pytest.fixture
def processor(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    stub = AsyncMock(return_value=JobResult(Outcome.PROCESSED))
    monkeypatch.setattr("app.messaging.consumer.process_job", stub)
    return stub


@pytest.fixture
def failing_processor(processor: AsyncMock) -> AsyncMock:
    processor.side_effect = ValueError("unreadable file")
    return processor


class TestOutcomes:
    async def test_acknowledges_a_processed_job(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_awaited_once()
        assert publish_calls(consumer) == 0

    @pytest.mark.parametrize("outcome", [Outcome.ALREADY_SETTLED, Outcome.SUPERSEDED])
    async def test_acknowledges_a_delivery_with_nothing_left_to_do(
        self,
        consumer: JobConsumer,
        processor: AsyncMock,
        job_message: JobMessage,
        outcome: Outcome,
    ) -> None:
        processor.return_value = JobResult(outcome)
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_awaited_once()
        assert publish_calls(consumer) == 0


class TestJobHeldElsewhere:
    @pytest.fixture
    def held(self, processor: AsyncMock) -> AsyncMock:
        processor.return_value = JobResult(Outcome.IN_PROGRESS)
        return processor

    async def test_checks_again_later_instead_of_dropping_it(
        self, consumer: JobConsumer, held: AsyncMock, job_message: JobMessage
    ) -> None:
        """Acknowledging would lose the job if the worker holding it dies."""
        message = incoming(job_message)

        await consumer._on_message(message)

        _, destination, expiration = published(consumer)
        assert destination == RETRY_QUEUE
        assert expiration == settings.in_progress_recheck_seconds
        message.ack.assert_awaited_once()

    async def test_does_not_spend_an_attempt(
        self, consumer: JobConsumer, held: AsyncMock, job_message: JobMessage
    ) -> None:
        await consumer._on_message(incoming(job_message))

        recheck, _, _ = published(consumer)
        assert recheck.attempt == job_message.attempt


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

    async def test_parks_a_permanent_failure_without_spending_retries(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        processor.side_effect = FileContentError("JSON root must be an object or an array")

        await consumer._on_message(incoming(job_message))

        _, destination, expiration = published(consumer)
        assert destination == DEAD_QUEUE
        assert expiration is None


class TestHandOff:
    async def test_publishes_as_mandatory(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        """Without it a missing queue drops the message, and the broker still
        confirms it."""
        await consumer._on_message(incoming(job_message))

        assert consumer._channel.default_exchange.publish.await_args.kwargs["mandatory"] is True

    async def test_restores_a_missing_queue_and_retries_the_hand_off(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        consumer._channel.default_exchange.publish.side_effect = [DeliveryError(None, None), None]
        message = incoming(job_message)

        await consumer._on_message(message)

        for queue in consumer._queues.values():
            queue.declare.assert_awaited_once()
        assert publish_calls(consumer) == 2
        message.ack.assert_awaited_once()

    async def test_returns_the_message_when_the_hand_off_keeps_failing(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        """Acknowledging would lose the job; leaving it neither acknowledged
        nor rejected would hold this worker's only prefetch slot forever."""
        consumer._channel.default_exchange.publish.side_effect = DeliveryError(None, None)
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)

    async def test_returns_the_message_when_the_broker_is_unreachable(
        self, consumer: JobConsumer, failing_processor: AsyncMock, job_message: JobMessage
    ) -> None:
        consumer._channel.default_exchange.publish.side_effect = ConnectionError("broker gone")
        message = incoming(job_message)

        await consumer._on_message(message)

        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)

    async def test_no_error_escapes_when_the_channel_is_already_gone(
        self, consumer: JobConsumer, processor: AsyncMock, job_message: JobMessage
    ) -> None:
        message = incoming(job_message)
        message.ack.side_effect = ConnectionError("channel closed")

        await consumer._on_message(message)


class TestSubscriptionWatch:
    async def test_leaves_an_active_subscription_alone(self, consumer: JobConsumer) -> None:
        subscribed_to(consumer, CONSUMER_TAG)

        await consumer._ensure_subscribed()

        consumer._queues[JOB_QUEUE].consume.assert_not_awaited()
        for queue in consumer._queues.values():
            queue.declare.assert_not_awaited()

    async def test_resubscribes_after_the_broker_cancels_it(self, consumer: JobConsumer) -> None:
        """Deleting the queue cancels the subscription without the library
        reinstating it, so the worker would otherwise stop receiving work."""
        subscribed_to(consumer)

        await consumer._ensure_subscribed()

        for queue in consumer._queues.values():
            queue.declare.assert_awaited_once()
        consumer._queues[JOB_QUEUE].consume.assert_awaited_once_with(
            consumer._on_message, consumer_tag=CONSUMER_TAG
        )

    async def test_keeps_the_original_consumer_tag(self, consumer: JobConsumer) -> None:
        """After a reconnect the library resubscribes under the stored tag; a
        second subscription under a new tag would double this worker's intake."""
        subscribed_to(consumer, "some-other-tag")

        await consumer._ensure_subscribed()

        assert consumer._queues[JOB_QUEUE].consume.await_args.kwargs["consumer_tag"] == CONSUMER_TAG

    async def test_waits_for_the_channel_to_be_ready_first(self, consumer: JobConsumer) -> None:
        """Checking mid-reconnect would race the library's own restore, which
        reuses the same tag — and the broker closes a channel that does that."""
        order: list[str] = []
        consumer._channel.ready = AsyncMock(side_effect=lambda: order.append("ready"))
        underlay = MagicMock(consumers={CONSUMER_TAG: object()})

        async def underlay_channel():
            order.append("check")
            return underlay

        consumer._channel.get_underlay_channel = underlay_channel

        await consumer._ensure_subscribed()

        assert order == ["ready", "check"]

    async def test_keeps_watching_after_a_failed_check(
        self, consumer: JobConsumer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "subscription_check_seconds", 0)
        consumer._ensure_subscribed = AsyncMock(
            side_effect=[RuntimeError("broker busy"), None, asyncio.CancelledError()]
        )

        with pytest.raises(asyncio.CancelledError):
            await consumer._watch_subscription()

        assert consumer._ensure_subscribed.await_count == 3
