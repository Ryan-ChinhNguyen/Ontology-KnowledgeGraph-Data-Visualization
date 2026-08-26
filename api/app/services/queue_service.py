import logging
import uuid

import aio_pika
from ontology_shared.messaging import JOB_QUEUE, JobMessage

from app.core.rabbitmq import broker

log = logging.getLogger(__name__)


async def publish_job(job_id: uuid.UUID, session_id: uuid.UUID) -> None:
    """Hand a job to the Worker.

    The message is persistent and the queue durable, so a broker restart does
    not drop queued work.
    """
    message = JobMessage(job_id=job_id, session_id=session_id)

    async with broker.channel() as channel:
        await channel.default_exchange.publish(
            aio_pika.Message(
                body=message.encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=JOB_QUEUE,
        )

    log.info("Job published: job_id=%s session_id=%s", job_id, session_id)
