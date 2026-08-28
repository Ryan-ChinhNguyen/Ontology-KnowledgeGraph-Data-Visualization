import asyncio
import logging

from ontology_shared.logging import configure_logging

from app.core.database import dispose_engine
from app.messaging.consumer import JobConsumer

configure_logging()
log = logging.getLogger(__name__)


async def main() -> None:
    log.info("Worker service starting")
    consumer = JobConsumer()
    try:
        await consumer.run()
    finally:
        await dispose_engine()
        log.info("Worker service stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted")
