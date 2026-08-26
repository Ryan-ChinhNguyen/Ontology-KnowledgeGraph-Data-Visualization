"""RabbitMQ connection and channel pools for the API service.

Pools are built when the application starts rather than at import time: a pool
binds to the running event loop, and there is none while modules are loading.
"""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractRobustConnection
from aio_pika.pool import Pool
from ontology_shared.messaging import DEAD_QUEUE, JOB_QUEUE, JOB_QUEUE_ARGUMENTS

from app.core.config import settings

log = logging.getLogger(__name__)


class NotConnectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("RabbitMQ pools are not open; call connect() during startup")


class RabbitMQBroker:
    """Owns the pools and hands out channels.

    Connections are few because each is a TCP connection; channels are many
    because they are cheap and are what concurrent requests contend for.
    """

    def __init__(self) -> None:
        self._connections: Pool[AbstractRobustConnection] | None = None
        self._channels: Pool[AbstractChannel] | None = None

    async def connect(self) -> None:
        self._connections = Pool(
            self._open_connection, max_size=settings.rabbitmq_connection_pool_size
        )
        self._channels = Pool(self._open_channel, max_size=settings.rabbitmq_channel_pool_size)
        await self._declare_queues()

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
            yield channel

    async def _open_connection(self) -> AbstractRobustConnection:
        return await aio_pika.connect_robust(settings.rabbitmq_url)

    async def _open_channel(self) -> AbstractChannel:
        if self._connections is None:
            raise NotConnectedError()
        async with self._connections.acquire() as connection:
            return await connection.channel()

    async def _declare_queues(self) -> None:
        """Declare both queues once at startup.

        The Worker declares the same queues with the same arguments; RabbitMQ
        rejects a redeclaration whose arguments differ, which is why the
        arguments live in the shared package.
        """
        async with self.channel() as channel:
            await channel.declare_queue(DEAD_QUEUE, durable=True)
            await channel.declare_queue(JOB_QUEUE, durable=True, arguments=JOB_QUEUE_ARGUMENTS)
        log.info("Declared queues: %s, %s", JOB_QUEUE, DEAD_QUEUE)


broker = RabbitMQBroker()
