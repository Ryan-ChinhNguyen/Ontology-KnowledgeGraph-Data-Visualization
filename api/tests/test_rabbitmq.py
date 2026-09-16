import uuid
from unittest.mock import AsyncMock, MagicMock

import aio_pika
import pytest
from aio_pika.exceptions import DeliveryError

from app.core.rabbitmq import RabbitMQBroker
from app.exceptions import QueueUnavailableError
from app.services import queue_service


def message() -> aio_pika.Message:
    return aio_pika.Message(body=b"{}")


class TestPublish:
    async def test_publishes_once_when_the_queue_exists(self) -> None:
        broker = RabbitMQBroker()
        broker._publish_once = AsyncMock()

        await broker.publish(message(), "job_queue")

        broker._publish_once.assert_awaited_once()

    async def test_redeclares_and_retries_a_returned_message(self) -> None:
        """A returned message means the queue went missing after it was
        declared; forgetting that it was declared makes the retry restore it."""
        broker = RabbitMQBroker()
        broker._queues_declared = True
        broker._publish_once = AsyncMock(side_effect=[DeliveryError(None, None), None])

        await broker.publish(message(), "job_queue")

        assert broker._publish_once.await_count == 2
        assert broker._queues_declared is False

    async def test_gives_up_after_one_retry(self) -> None:
        broker = RabbitMQBroker()
        broker._publish_once = AsyncMock(side_effect=DeliveryError(None, None))

        with pytest.raises(DeliveryError):
            await broker.publish(message(), "job_queue")

        assert broker._publish_once.await_count == 2


class TestQueueDeclaration:
    async def test_declares_every_queue_when_not_yet_declared(self) -> None:
        broker = RabbitMQBroker()
        channel = MagicMock()
        channel.declare_queue = AsyncMock()

        await broker._ensure_queues(channel)

        assert channel.declare_queue.await_count == 3
        assert broker._queues_declared is True

    async def test_skips_declaration_once_done(self) -> None:
        broker = RabbitMQBroker()
        broker._queues_declared = True
        channel = MagicMock()
        channel.declare_queue = AsyncMock()

        await broker._ensure_queues(channel)

        channel.declare_queue.assert_not_awaited()


class TestPublishJob:
    async def test_reports_a_job_that_could_not_be_queued(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            queue_service.broker, "publish", AsyncMock(side_effect=DeliveryError(None, None))
        )

        with pytest.raises(QueueUnavailableError):
            await queue_service.publish_job(uuid.uuid4(), uuid.uuid4())
