"""RabbitMQ connection and channel pools for the API service.

Pools are created when the application starts rather than at import time: a
pool binds to the running event loop, and there is none while modules load.
Creating them performs no I/O, so the service starts even when the broker is
unreachable — publishing then fails per request instead of preventing startup
and putting the service into a crash loop.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection
from aio_pika.pool import Pool
from ontology_shared.messaging import (
    DEAD_QUEUE,
    JOB_QUEUE,
    JOB_QUEUE_ARGUMENTS,
    RETRY_QUEUE,
    RETRY_QUEUE_ARGUMENTS,
)

from app.core.config import settings

log = logging.getLogger(__name__)


class NotConnectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("RabbitMQ pools are not open; call open() during startup")


class RabbitMQBroker:
    """Owns the pools and hands out channels.

    Connections are few because each is a TCP connection; channels are many
    because they are cheap and are what concurrent requests contend for.
    """

    def __init__(self) -> None:
        self._connections: Pool[AbstractRobustConnection] | None = None
        self._channels: Pool[AbstractChannel] | None = None
        self._queues_declared = False
        self._declare_lock = asyncio.Lock()

    def open(self) -> None:
        """Create the pools. Nothing connects until a channel is acquired."""
        self._connections = Pool(
            self._open_connection, max_size=settings.rabbitmq_connection_pool_size
        )
        self._channels = Pool(self._open_channel, max_size=settings.rabbitmq_channel_pool_size)
        self._queues_declared = False

    async def close(self) -> None:
        if self._channels is not None:
            await self._channels.close()
        if self._connections is not None:
            await self._connections.close()
        self._channels = self._connections = None
        log.info("RabbitMQ pools closed")

    @asynccontextmanager
    async def channel(self) -> AsyncIterator[AbstractChannel]:
        if self._channels is None:
            raise NotConnectedError()
        async with self._channels.acquire() as channel:
            await self._ensure_queues(channel)
            yield channel

    async def is_ready(self) -> bool:
        """Whether the broker can currently be reached."""
        try:
            async with self.channel():
                return True
        except Exception:
            log.warning("RabbitMQ is not reachable", exc_info=True)
            return False

    async def _open_connection(self) -> AbstractRobustConnection:
        return await aio_pika.connect_robust(settings.rabbitmq_url)

    async def _open_channel(self) -> AbstractChannel:
        if self._connections is None:
            raise NotConnectedError()
        async with self._connections.acquire() as connection:
            return await connection.channel()

    async def _ensure_queues(self, channel: AbstractChannel) -> None:
        """Declare both queues once, on first use.

        Declaring here rather than at startup is what lets the service start
        without the broker. The Worker declares the same queues with the same
        arguments; RabbitMQ rejects a redeclaration whose arguments differ,
        which is why the arguments live in the shared package.
        """
        if self._queues_declared:
            return

        async with self._declare_lock:
            if self._queues_declared:
                return
            await channel.declare_queue(DEAD_QUEUE, durable=True)
            await channel.declare_queue(RETRY_QUEUE, durable=True, arguments=RETRY_QUEUE_ARGUMENTS)
            await channel.declare_queue(JOB_QUEUE, durable=True, arguments=JOB_QUEUE_ARGUMENTS)
            self._queues_declared = True
            log.info("Declared queues: %s, %s, %s", JOB_QUEUE, RETRY_QUEUE, DEAD_QUEUE)


broker = RabbitMQBroker()
