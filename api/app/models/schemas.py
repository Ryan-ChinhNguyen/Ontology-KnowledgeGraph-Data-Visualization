import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.db import FileFormat, JobStatus, SessionStatus


class UploadResponse(BaseModel):
    session_id: uuid.UUID
    job_id: uuid.UUID
    status: SessionStatus
    total_files: int
    total_size_bytes: int


class SessionStatusResponse(BaseModel):
    session_id: uuid.UUID
    format: FileFormat
    status: SessionStatus
    total_files: int
    total_size_bytes: int
    job_status: JobStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime
