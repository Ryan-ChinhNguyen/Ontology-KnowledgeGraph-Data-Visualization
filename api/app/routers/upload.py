import logging
import uuid

from fastapi import APIRouter, Depends, UploadFile, status
from ontology_shared.models import Job, Session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_storage
from app.exceptions import SessionNotFoundError
from app.models.schemas import SessionStatusResponse, UploadResponse
from app.services.queue_service import publish_job
from app.services.storage import FileStorage
from app.services.upload_service import process_upload

log = logging.getLogger(__name__)

router = APIRouter(tags=["upload"])


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a batch of data files for parsing",
)
async def upload_files(
    files: list[UploadFile],
    db: AsyncSession = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> UploadResponse:
    """Accept up to five same-format files and queue them for parsing.

    Returns as soon as the files are stored; parsing happens in the Worker.
    Poll ``GET /api/sessions/{session_id}`` for the outcome.
    """
    session, job = await process_upload(files, db, storage)
    await publish_job(job.job_id, session.session_id)

    log.info("Upload accepted: session_id=%s job_id=%s", session.session_id, job.job_id)
    return UploadResponse.build(session, job)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionStatusResponse,
    summary="Read the parsing status of an upload",
)
async def get_session_status(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SessionStatusResponse:
    session = await db.get(Session, session_id)
    if session is None:
        raise SessionNotFoundError(str(session_id))

    latest_job = await db.execute(
        select(Job)
        .where(Job.session_id == session_id)
        .order_by(Job.queued_at.desc())
        .limit(1)
    )
    return SessionStatusResponse.build(session, latest_job.scalar_one_or_none())
