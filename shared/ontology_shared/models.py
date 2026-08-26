"""Database schema shared by the API and Worker services.

This module is the single source of truth for the tables. The API writes rows
here during upload; the Worker reads and updates them while processing. Any
column added for one service is therefore immediately visible to the other.
"""

import enum
import uuid

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from datetime import datetime

from ontology_shared.clock import utc_now


class Base(DeclarativeBase):
    pass


class SessionStatus(str, enum.Enum):
    """Lifecycle of one upload, from accepted bytes to parsed tables."""

    uploading = "uploading"
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class JobStatus(str, enum.Enum):
    """Lifecycle of the background parse job for a session."""

    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class FileFormat(str, enum.Enum):
    csv = "csv"
    json = "json"
    sql = "sql"
    parquet = "parquet"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _session_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        index=True,
    )


class Session(Base):
    """One upload request: 1-5 files of a single format."""

    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = _uuid_pk()
    format: Mapped[FileFormat] = mapped_column(Enum(FileFormat, name="fileformat"))
    total_files: Mapped[int] = mapped_column(Integer)
    total_size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="sessionstatus"),
        default=SessionStatus.uploading,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    files: Mapped[list["File"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class File(Base):
    """One stored file. ``stored_path`` is opaque to callers so that the
    backing store can move from local disk to object storage unchanged."""

    __tablename__ = "files"

    file_id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    original_filename: Mapped[str] = mapped_column(String(255))
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    stored_path: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped["Session"] = relationship(back_populates="files")


class Job(Base):
    """Background parse job. ``status == done`` is the idempotency marker the
    Worker checks before doing any work, so a redelivered message is a no-op."""

    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="jobstatus"), default=JobStatus.queued
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["Session"] = relationship(back_populates="jobs")
