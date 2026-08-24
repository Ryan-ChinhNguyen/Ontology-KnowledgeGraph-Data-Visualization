import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SessionStatus(str, enum.Enum):
    uploading = "uploading"
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class JobStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class FileFormat(str, enum.Enum):
    csv = "csv"
    json = "json"
    sql = "sql"
    parquet = "parquet"


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    format: Mapped[FileFormat] = mapped_column(Enum(FileFormat, name="fileformat"))
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus, name="sessionstatus"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    files: Mapped[list["File"]] = relationship(back_populates="session")
    jobs: Mapped[list["Job"]] = relationship(back_populates="session")


class File(Base):
    __tablename__ = "files"

    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.session_id"))
    stored_path: Mapped[str] = mapped_column(Text)

    session: Mapped["Session"] = relationship(back_populates="files")


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sessions.session_id"))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="jobstatus"))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["Session"] = relationship(back_populates="jobs")
