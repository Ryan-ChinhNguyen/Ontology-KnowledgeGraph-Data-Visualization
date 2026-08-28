import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from ontology_shared.logging import configure_logging
from sqlalchemy import text

from app.core.database import dispose_engine, session_factory
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
    broker.open()

    # A dependency that is merely down must not stop the process from starting,
    # or a brief broker outage turns into a crash loop. Readiness reports it
    # instead.
    if not await broker.is_ready():
        log.warning("RabbitMQ unreachable at startup; uploads will fail until it returns")

    log.info("API service started")
    yield

    log.info("API service shutting down")
    await broker.close()
    await dispose_engine()


TAGS_METADATA = [
    {
        "name": "upload",
        "description": (
            "Submit data files and follow their parsing. Uploading returns immediately; "
            "the Worker service parses in the background, so the outcome is read back "
            "from the session endpoint."
        ),
    },
    {"name": "ops", "description": "Health and liveness."},
]

DESCRIPTION = """
Accepts data uploads and queues them for ontology extraction.

**Uploading**

A batch is one to five files that share a single format, 20MB in total. Files
are checked by content hash, so re-uploading identical bytes is rejected.

| Format | Extensions |
|--------|------------|
| CSV / TSV | `.csv`, `.tsv` |
| JSON | `.json` |
| SQL dump | `.sql` |
| Parquet | `.parquet` |

**Following a job**

`POST /api/upload` responds once the files are stored, before they are parsed.
Poll `GET /api/sessions/{session_id}` until `status` reaches `ready` or
`failed`; on failure `error_message` carries the reason.
"""

app = FastAPI(
    title="Ontology KG — API Service",
    description=DESCRIPTION,
    version="0.1.0",
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)

app.include_router(upload.router, prefix="/api")


@app.get("/health", tags=["ops"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """Whether the process is up. Says nothing about its dependencies."""
    return {"status": "ok"}


@app.get(
    "/health/ready",
    tags=["ops"],
    summary="Readiness probe",
    responses={503: {"description": "A dependency is unreachable."}},
)
async def readiness(response: Response) -> dict[str, object]:
    """Whether uploads can actually be served right now.

    Reports each dependency separately so a failure points at what to start.
    """
    dependencies = {
        "postgres": await _postgres_reachable(),
        "rabbitmq": await broker.is_ready(),
    }
    ready = all(dependencies.values())

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "dependencies": dependencies}


async def _postgres_reachable() -> bool:
    try:
        async with session_factory() as db:
            await db.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("PostgreSQL is not reachable", exc_info=True)
        return False
