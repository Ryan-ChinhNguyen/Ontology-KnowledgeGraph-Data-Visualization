import logging
import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status
from ontology_shared.models import Job, Session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies import get_storage
from app.exceptions import SessionNotFoundError
from app.models.schemas import ErrorResponse, SessionStatusResponse, UploadResponse
from app.services.queue_service import publish_job
from app.services.session_service import delete_session
from app.services.storage import FileStorage
from app.services.upload_service import process_upload

log = logging.getLogger(__name__)

router = APIRouter(tags=["upload"])

UPLOAD_ERRORS: dict[int | str, dict] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ErrorResponse,
        "description": (
            "The batch broke an upload rule: no files, more than five, an empty file, "
            "an unsupported extension, mixed formats, or a repeated filename."
        ),
    },
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "These exact bytes have already been uploaded.",
    },
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE: {
        "model": ErrorResponse,
        "description": "The files add up to more than 20MB.",
    },
}


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a batch of data files for parsing",
    responses=UPLOAD_ERRORS,
)
async def upload_files(
    files: list[UploadFile] = File(
        ...,
        description=(
            "One to five files, all of the same format, 20MB in total. "
            "Accepted extensions: .csv, .tsv, .json, .sql, .parquet"
        ),
    ),
    db: AsyncSession = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> UploadResponse:
    """Accept a batch of files and queue them for parsing.

    Returns as soon as the files are stored — parsing runs in the Worker
    service. Poll `GET /api/sessions/{session_id}` to follow it: `ready` means
    the files parsed, `failed` means they did not and `error_message` says why.
    """
    session, job = await process_upload(files, db, storage)
    await publish_job(job.job_id, session.session_id)

    log.info("Upload accepted: session_id=%s job_id=%s", session.session_id, job.job_id)
    return UploadResponse.build(session, job)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionStatusResponse,
    summary="Read the parsing status of an upload",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No session with this id.",
        }
    },
)
async def get_session_status(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SessionStatusResponse:
    """Report where an upload has got to.

    `status` moves `queued` → `processing` → `ready` or `failed`. When a job
    has failed, `error_message` carries the reason from the last attempt.
    """
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


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an upload and its files",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No session with this id.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "The session is still queued or being processed.",
        },
    },
)
async def delete_session_by_id(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> None:
    """Remove a session, its records, and its stored files.

    Only a session that has finished — `ready` or `failed` — may be deleted.

    Because uploads are rejected when their content hash is already on record,
    this is also how the same file becomes uploadable again after a failure.
    """
    await delete_session(session_id, db, storage)
