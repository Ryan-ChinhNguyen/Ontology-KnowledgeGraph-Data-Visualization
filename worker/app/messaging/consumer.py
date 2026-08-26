"""Consumes job messages and decides what happens to one that fails.

Retries are performed by republishing with an incremented attempt counter
rather than by requeueing in place, so a poison message cannot spin at the
head of the queue and starve the jobs behind it.
"""

import asyncio
import logging

import aio_pika
from aio_pika.abc import AbstractIncomingMessage, AbstractRobustChannel
from ontology_shared.messaging import DEAD_QUEUE, JOB_QUEUE, JOB_QUEUE_ARGUMENTS, JobMessage

from app.core.config import settings
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
            self._channel = await connection.channel()
            await self._channel.set_qos(prefetch_count=settings.prefetch_count)

            await self._channel.declare_queue(DEAD_QUEUE, durable=True)
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
            # Undecodable bodies can never succeed; send them straight to the
            # dead-letter queue instead of burning retries on them.
            log.exception("Discarding malformed message")
            await message.reject(requeue=False)
            return

        attempt_number = job.attempt + 1
        is_final = attempt_number >= self._max_attempts
        log.info("Job received: job_id=%s attempt=%d/%d", job.job_id, attempt_number, self._max_attempts)

        try:
            await process_job(job, is_final_attempt=is_final)
        except Exception:
            await self._on_failure(message, job, is_final)
            return

        await message.ack()
        log.info("Job finished: job_id=%s", job.job_id)

    async def _on_failure(
        self, message: AbstractIncomingMessage, job: JobMessage, is_final: bool
    ) -> None:
        if is_final:
            log.exception("Job exhausted retries, dead-lettering: job_id=%s", job.job_id)
            await message.reject(requeue=False)
            return

        log.warning(
            "Job failed, scheduling retry: job_id=%s next_attempt=%d",
            job.job_id,
            job.attempt + 2,
            exc_info=True,
        )
        await self._republish(job)
        await message.ack()

    async def _republish(self, job: JobMessage) -> None:
        retry = job.model_copy(update={"attempt": job.attempt + 1})
        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=retry.encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=JOB_QUEUE,
        )
