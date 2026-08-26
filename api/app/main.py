import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from ontology_shared.logging import configure_logging

from app.core.database import dispose_engine
from app.core.rabbitmq import broker
from app.routers import upload

configure_logging()
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the connection pools for the life of the process.

    Queues are declared once at startup rather than per publish, so the hot
    path only acquires a pooled channel.
    """
    log.info("API service starting")
    await broker.connect()
    log.info("API service ready")

    yield

    log.info("API service shutting down")
    await broker.close()
    await dispose_engine()


app = FastAPI(
    title="Ontology KG — API Service",
    description="Accepts data uploads and queues them for ontology extraction.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(upload.router, prefix="/api")


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}
