import uuid

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.db import Job, Session
from app.models.schemas import SessionStatusResponse, UploadResponse
from app.services.queue_service import publish_job
from app.services.upload_service import process_upload

router = APIRouter(prefix="/api", tags=["upload"])


@router.post("/upload", response_model=UploadResponse)
async def upload_files(
    files: list[UploadFile],
    db: AsyncSession = Depends(get_db),
):
    session, job = await process_upload(files, db)
    await publish_job(job.job_id, session.session_id)

    return UploadResponse(
        session_id=session.session_id,
        job_id=job.job_id,
        status=session.status,
        total_files=session.total_files,
        total_size_bytes=session.total_size_bytes,
    )


@router.get("/sessions/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(Session, session_id)
    if not session:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(Job).where(Job.session_id == session_id).order_by(Job.queued_at.desc())
    )
    job = result.scalar_one_or_none()

    return SessionStatusResponse(
        session_id=session.session_id,
        format=session.format,
        status=session.status,
        total_files=session.total_files,
        total_size_bytes=session.total_size_bytes,
        job_status=job.status if job else None,
        error_message=job.error_message if job else None,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )
