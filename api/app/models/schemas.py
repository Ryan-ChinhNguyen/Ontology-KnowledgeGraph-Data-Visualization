"""Response bodies for the upload API.

Each schema knows how to build itself from ORM rows, keeping the field-by-field
mapping out of the route handlers.
"""

import uuid
from datetime import datetime

from ontology_shared.models import FileFormat, Job, JobStatus, Session, SessionStatus
from pydantic import BaseModel, ConfigDict


class UploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    job_id: uuid.UUID
    status: SessionStatus
    total_files: int
    total_size_bytes: int

    @classmethod
    def build(cls, session: Session, job: Job) -> "UploadResponse":
        return cls(
            session_id=session.session_id,
            job_id=job.job_id,
            status=session.status,
            total_files=session.total_files,
            total_size_bytes=session.total_size_bytes,
        )


class SessionStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    format: FileFormat
    status: SessionStatus
    total_files: int
    total_size_bytes: int
    job_status: JobStatus | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def build(cls, session: Session, job: Job | None) -> "SessionStatusResponse":
        return cls(
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
