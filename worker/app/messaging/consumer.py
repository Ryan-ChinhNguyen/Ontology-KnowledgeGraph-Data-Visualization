"""Consumes job messages and decides what happens to each delivery.

The original message is acknowledged only once the job has been dealt with:
processed, found already settled, or handed on to the retry or dead-letter
queue. Every hand-off is published with confirms and as mandatory, so the
acknowledgement follows the broker's guarantee that the next copy is stored in
a queue that exists. A crash between the two produces a duplicate rather than
a loss, and claiming a job before running it makes a duplicate harmless.

No exception is allowed to escape the callback. A delivery that is neither
acknowledged nor rejected stays assigned to this consumer, and with a prefetch
of one that stops the worker from receiving anything else.
"""

import asyncio
import logging

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel
from aio_pika.exceptions import DeliveryError
from ontology_shared.messaging import (
    DEAD_QUEUE,
    JOB_QUEUE,
    RETRY_QUEUE,
    JobMessage,
    declare_topology,
    retry_delay_for,
)

from app.core.config import settings
from app.errors import PermanentJobError
from app.services.job_processor import Outcome, process_job

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
            self._channel = await connection.channel(
                publisher_confirms=True,
                on_return_raises=True,
            )
            await self._channel.set_qos(prefetch_count=settings.prefetch_count)

            queue = await declare_topology(self._channel)
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
            result = await process_job(job, attempt=attempt, is_final_attempt=is_final)
        except PermanentJobError:
            # Repeating this would fail identically, so it skips the retries
            # and keeps the dead-letter queue to genuinely unexplained work.
            log.error("Job cannot succeed, parking: job_id=%s", job.job_id, exc_info=True)
            await self._move(message, job.encode(), DEAD_QUEUE)
            return
        except Exception:
            await self._on_failure(message, job, attempt, is_final)
            return

        if result.outcome is Outcome.IN_PROGRESS:
            # Another worker holds the job. Checking again later — rather than
            # acknowledging — keeps a message in play in case that worker
            # dies, since its lease will then expire and this job can be
            # claimed. The attempt count is left alone: nothing was tried.
            log.info(
                "Job held by another worker, rechecking in %ds: job_id=%s",
                settings.in_progress_recheck_seconds,
                job.job_id,
            )
            await self._move(
                message,
                job.encode(),
                RETRY_QUEUE,
                delay=settings.in_progress_recheck_seconds,
            )
            return

        await self._acknowledge(message)
        log.info("Job %s: job_id=%s", result.outcome.value, job.job_id)

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
        log.warning("Job failed, retrying in %ds: job_id=%s", delay, job.job_id, exc_info=True)
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

        A returned message means the target queue has gone; declaring the
        topology again restores it, so the publish is retried once after that.
        If the hand-off still fails, the original is handed back to the broker
        instead of being acknowledged.
        """
        try:
            try:
                await self._publish(body, queue, delay)
            except DeliveryError:
                log.warning("Message to '%s' was unroutable; redeclaring queues", queue)
                await declare_topology(self._channel)
                await self._publish(body, queue, delay)
        except Exception:
            log.exception("Could not hand the message to '%s'; returning it to the queue", queue)
            await self._requeue(message)
            return

        await self._acknowledge(message)

    async def _publish(self, body: bytes, queue: str, delay: int | None) -> None:
        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                expiration=delay,
            ),
            routing_key=queue,
            mandatory=True,
        )

    async def _acknowledge(self, message: AbstractIncomingMessage) -> None:
        try:
            await message.ack()
        except Exception:
            # The channel is gone; the broker redelivers unacknowledged
            # messages when it closes, and the claim makes that harmless.
            log.exception("Could not acknowledge message")

    async def _requeue(self, message: AbstractIncomingMessage) -> None:
        try:
            await message.nack(requeue=True)
        except Exception:
            # The broker returns the message itself when the channel closes.
            log.exception("Could not return message to the queue")
