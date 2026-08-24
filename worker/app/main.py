import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import aio_pika
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.parsers.base import BaseParser
from app.parsers.csv_parser import CsvParser
from app.parsers.json_parser import JsonParser
from app.parsers.parquet_parser import ParquetParser
from app.parsers.sql_parser import SqlParser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

QUEUE_NAME = "job_queue"
DEAD_QUEUE_NAME = "dead_queue"

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

PARSERS: dict[str, BaseParser] = {
    "csv": CsvParser(),
    "json": JsonParser(),
    "sql": SqlParser(),
    "parquet": ParquetParser(),
}


async def process_job(job_id: uuid.UUID, session_id: uuid.UUID, attempt: int) -> None:
    from sqlalchemy import select
    from app.models import Job, JobStatus, Session, SessionStatus, File

    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job or job.status == JobStatus.done:
            log.info(f"Job {job_id} already done or not found — skipping (idempotency)")
            return

        job.status = JobStatus.processing
        job.started_at = datetime.now(timezone.utc)
        job.attempt_count = attempt
        await db.commit()

        session = await db.get(Session, session_id)
        session.status = SessionStatus.processing
        await db.commit()

        result = await db.execute(select(File).where(File.session_id == session_id))
        files = result.scalars().all()
        file_paths = [f.stored_path for f in files]

        try:
            parser = PARSERS.get(session.format.value)
            if not parser:
                raise ValueError(f"No parser for format: {session.format}")

            normalized = parser.parse(file_paths)
            log.info(f"Job {job_id} parsed {len(normalized.tables)} tables")

            job.status = JobStatus.done
            job.completed_at = datetime.now(timezone.utc)
            session.status = SessionStatus.ready

        except Exception as e:
            log.error(f"Job {job_id} failed (attempt {attempt}): {e}")
            job.status = JobStatus.failed
            job.error_message = str(e)
            session.status = SessionStatus.failed

        await db.commit()


async def on_message(message: aio_pika.IncomingMessage) -> None:
    async with message.process(requeue=False):
        body = json.loads(message.body)
        job_id = uuid.UUID(body["job_id"])
        session_id = uuid.UUID(body["session_id"])
        attempt = body.get("attempt", 0)

        log.info(f"Received job {job_id} (attempt {attempt})")

        try:
            await process_job(job_id, session_id, attempt)
            log.info(f"Job {job_id} completed")
        except Exception as e:
            log.error(f"Unhandled error for job {job_id}: {e}")
            raise


async def main() -> None:
    log.info("Worker starting...")
    connection = await aio_pika.connect_robust(
        settings.rabbitmq_url,
        reconnect_interval=5,
    )

    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        dead_queue = await channel.declare_queue(DEAD_QUEUE_NAME, durable=True)
        queue = await channel.declare_queue(
            QUEUE_NAME,
            durable=True,
            arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": dead_queue.name,
                "x-delivery-limit": settings.max_retry_attempts,
            },
        )

        await queue.consume(on_message)
        log.info(f"Listening on queue: {QUEUE_NAME}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
