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
from aio_pika.exceptions import DeliveryError
from aio_pika.pool import Pool
from ontology_shared.messaging import declare_topology

from app.core.config import settings

log = logging.getLogger(__name__)

#: Built into every RabbitMQ virtual host, so it always exists.
PROBE_EXCHANGE = "amq.direct"


class NotConnectedError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("RabbitMQ pools are not open; call open() during startup")


class RabbitMQBroker:
    """Owns the pools, hands out channels, and publishes.

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
        async with self._acquire() as channel:
            await self._ensure_queues(channel)
            yield channel

    async def publish(self, message: aio_pika.Message, routing_key: str) -> None:
        """Publish to a queue, failing loudly if it cannot be delivered there.

        Publishing is mandatory and the channel raises on a returned message,
        so a missing queue surfaces as an error instead of a silent drop — the
        broker still confirms an unroutable message, so confirms alone would
        report success.

        A returned message most likely means the queue was removed after it
        was declared, so the topology is declared again and the publish tried
        once more before giving up.
        """
        try:
            await self._publish_once(message, routing_key)
        except DeliveryError:
            log.warning("Message to '%s' was unroutable; redeclaring queues", routing_key)
            self._queues_declared = False
            await self._publish_once(message, routing_key)

    async def is_ready(self) -> bool:
        """Whether the broker can currently be reached.

        Takes a real round trip. Acquiring a channel is not enough: the pool
        hands back an existing channel without checking it, so that alone
        reports ready while the broker is down.

        The probe passively declares ``amq.direct``, an exchange every broker
        has. It leaves nothing behind and cannot fail because of this service's
        own queues — a missing queue does not stop uploads, since publishing
        declares it again. It is bounded by a timeout because, while the
        connection is being re-established, the call waits for it rather than
        failing.
        """
        try:
            await asyncio.wait_for(self._probe(), timeout=settings.rabbitmq_probe_timeout_seconds)
            return True
        except Exception:
            log.warning("RabbitMQ is not reachable", exc_info=True)
            return False

    async def _probe(self) -> None:
        async with self._acquire() as channel:
            # Not robust: a robust declaration is remembered for replay after
            # every reconnect and never forgotten, so probing that way would
            # grow without bound.
            await channel.declare_exchange(PROBE_EXCHANGE, passive=True, robust=False)

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[AbstractChannel]:
        if self._channels is None:
            raise NotConnectedError()
        async with self._channels.acquire() as channel:
            yield channel

    async def _publish_once(self, message: aio_pika.Message, routing_key: str) -> None:
        async with self.channel() as channel:
            await channel.default_exchange.publish(message, routing_key=routing_key, mandatory=True)

    async def _open_connection(self) -> AbstractRobustConnection:
        return await aio_pika.connect_robust(settings.rabbitmq_url)

    async def _open_channel(self) -> AbstractChannel:
        if self._connections is None:
            raise NotConnectedError()
        async with self._connections.acquire() as connection:
            return await connection.channel(publisher_confirms=True, on_return_raises=True)

    async def _ensure_queues(self, channel: AbstractChannel) -> None:
        """Declare the queues on first use, and again after one goes missing.

        Declaring here rather than at startup is what lets the service start
        without the broker.
        """
        if self._queues_declared:
            return

        async with self._declare_lock:
            if self._queues_declared:
                return
            await declare_topology(channel)
            self._queues_declared = True
            log.info("Declared queues")


broker = RabbitMQBroker()
