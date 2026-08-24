import json
import uuid

import aio_pika

from app.core.config import settings

QUEUE_NAME = "job_queue"
DEAD_QUEUE_NAME = "dead_queue"


async def publish_job(job_id: uuid.UUID, session_id: uuid.UUID) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()

        dead_queue = await channel.declare_queue(DEAD_QUEUE_NAME, durable=True)

        queue = await channel.declare_queue(
            QUEUE_NAME,
            durable=True,
            arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": dead_queue.name,
            },
        )

        message_body = json.dumps({
            "job_id": str(job_id),
            "session_id": str(session_id),
            "attempt": 0,
        }).encode()

        await channel.default_exchange.publish(
            aio_pika.Message(
                body=message_body,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=queue.name,
        )
