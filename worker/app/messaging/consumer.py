"""Consumes job messages and decides what happens to one that fails.

The original message is acknowledged only after the job has been dealt with
for good — either processed successfully, or safely handed to a holding queue
or the dead-letter queue. Every hand-off is published with confirms, so the
acknowledgement follows the broker's own guarantee that the next copy is
stored. A crash between the two produces a duplicate rather than a loss, and
the Worker's idempotency check makes a duplicate a no-op.
"""

import asyncio
import logging

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel
from ontology_shared.messaging import (
    DEAD_QUEUE,
    JOB_QUEUE,
    JOB_QUEUE_ARGUMENTS,
    RETRY_QUEUE,
    RETRY_QUEUE_ARGUMENTS,
    JobMessage,
    retry_delay_for,
)

from app.core.config import settings
from app.errors import PermanentJobError
from app.services.job_processor import process_job

log = logging.getLogger(__name__)


class JobConsumer:
    def __init__(self, max_attempts: int = settings.max_retry_attempts) -> None:
        self._max_attempts = max_attempts
        self._channel: AbstractRobustChannel | None = None

    async def run(self) -> None:
        """Connect and consume until cancelled.

        ``connect_robust`` reconnects on its own after a broker outage, so a
        RabbitMQ restart pauses the worker instead of stopping it.
        """
        connection = await aio_pika.connect_robust(
            settings.rabbitmq_url,
            reconnect_interval=settings.reconnect_interval,
        )

        async with connection:
            # Confirms are what let a republished retry be acknowledged safely.
            self._channel = await connection.channel(publisher_confirms=True)
            await self._channel.set_qos(prefetch_count=settings.prefetch_count)

            await self._channel.declare_queue(DEAD_QUEUE, durable=True)
            await self._channel.declare_queue(
                RETRY_QUEUE, durable=True, arguments=RETRY_QUEUE_ARGUMENTS
            )
            queue = await self._channel.declare_queue(
                JOB_QUEUE, durable=True, arguments=JOB_QUEUE_ARGUMENTS
            )

            await queue.consume(self._on_message)
            log.info("Consuming from '%s' (prefetch=%d)", JOB_QUEUE, settings.prefetch_count)
            await asyncio.Future()

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        try:
            job = JobMessage.decode(message.body)
        except ValueError:
            # An undecodable body can never succeed, so it is parked directly
            # rather than cycling through retries. The bytes are forwarded
            # unchanged, since they are the only record of what arrived.
            log.exception("Parking malformed message")
            await self._move(message, message.body, DEAD_QUEUE)
            return

        attempt = job.attempt + 1
        is_final = attempt >= self._max_attempts
        log.info("Job received: job_id=%s attempt=%d/%d", job.job_id, attempt, self._max_attempts)

        try:
            await process_job(job, attempt=attempt, is_final_attempt=is_final)
        except PermanentJobError:
            # Repeating this would fail identically, so it skips the retries
            # and keeps the dead-letter queue to genuinely unexplained work.
            log.error("Job cannot succeed, parking: job_id=%s", job.job_id, exc_info=True)
            await self._move(message, job.encode(), DEAD_QUEUE)
            return
        except Exception:
            await self._on_failure(message, job, attempt, is_final)
            return

        await message.ack()
        log.info("Job finished: job_id=%s", job.job_id)

    async def _on_failure(
        self,
        message: AbstractIncomingMessage,
        job: JobMessage,
        attempt: int,
        is_final: bool,
    ) -> None:
        if is_final:
            log.exception("Job exhausted its attempts: job_id=%s", job.job_id)
            await self._move(message, job.encode(), DEAD_QUEUE)
            return

        delay = retry_delay_for(attempt)
        log.warning(
            "Job failed, retrying in %ds: job_id=%s", delay, job.job_id, exc_info=True
        )
        await self._move(message, job.next_attempt().encode(), RETRY_QUEUE, delay=delay)

    async def _move(
        self,
        message: AbstractIncomingMessage,
        body: bytes,
        queue: str,
        *,
        delay: int | None = None,
    ) -> None:
        """Send ``body`` to another queue, then release the original.

        ``delay`` sets the message's own expiry, which is how a single holding
        queue can serve several wait times.

        ``publish`` returns only once the broker has confirmed the message, so
        reaching the acknowledgement means the next copy is durably stored. If
        the publish fails, the exception propagates without acknowledging and
        the original stays queued for redelivery.
        """
        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                expiration=delay,
            ),
            routing_key=queue,
        )
        await message.ack()
