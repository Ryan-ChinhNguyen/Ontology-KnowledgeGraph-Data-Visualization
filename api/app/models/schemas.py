"""Response bodies for the upload API.

Each schema knows how to build itself from ORM rows, keeping the field-by-field
mapping out of the route handlers.
"""

import uuid
from datetime import datetime

from ontology_shared.models import FileFormat, Job, JobStatus, Session, SessionStatus
from pydantic import BaseModel, ConfigDict


class ErrorResponse(BaseModel):
    """Body returned for every 4xx. Named so the API docs can show the shape
    of a failure alongside the success case."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"detail": "File 'people.csv' is empty"}}
    )

    detail: str


class UploadResponse(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "session_id": "3f2b9c14-8e7a-4d21-9b55-1c0a7e6d4f38",
                "job_id": "a71e5d90-2c48-4f6b-8d13-6e9b0f2a5c77",
                "status": "queued",
                "total_files": 2,
                "total_size_bytes": 48123,
            }
        },
    )

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
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "session_id": "3f2b9c14-8e7a-4d21-9b55-1c0a7e6d4f38",
                "format": "csv",
                "status": "failed",
                "total_files": 2,
                "total_size_bytes": 48123,
                "job_status": "failed",
                "error_message": "Error tokenizing data. C error: Expected 3 fields in line 42, saw 5",
                "created_at": "2026-08-26T09:14:02.117Z",
                "updated_at": "2026-08-26T09:14:06.883Z",
            }
        },
    )

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
