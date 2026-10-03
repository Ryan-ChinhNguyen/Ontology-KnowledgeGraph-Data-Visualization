"""Database schema shared by the API and Worker services.

This module is the single source of truth for the tables. The API writes rows
here during upload; the Worker reads and updates them while processing. Any
column added for one service is therefore immediately visible to the other.
"""

import enum
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
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


def _session_fk(*, index: bool = True) -> Mapped[uuid.UUID]:
    """Foreign key onto ``sessions``, cascading so deleting a session removes
    everything recorded about it.

    ``index`` is turned off where a composite index already covers the column
    as its leading term, which would make a second index redundant.
    """
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        index=index,
    )


class Session(Base):
    """One upload request: 1-5 files of a single format."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("total_files > 0", name="ck_sessions_total_files_positive"),
        CheckConstraint("total_size_bytes > 0", name="ck_sessions_total_size_positive"),
    )

    session_id: Mapped[uuid.UUID] = _uuid_pk()
    format: Mapped[FileFormat] = mapped_column(Enum(FileFormat, name="fileformat"))
    total_files: Mapped[int] = mapped_column(Integer)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger)
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
    dataset_tables: Mapped[list["DatasetTable"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class File(Base):
    """One stored file. ``stored_path`` is opaque to callers so that the
    backing store can move from local disk to object storage unchanged."""

    __tablename__ = "files"
    __table_args__ = (
        # The application checks for a duplicate hash before inserting, but two
        # concurrent uploads of the same bytes would both pass that check. This
        # is what actually guarantees the rule.
        UniqueConstraint("sha256_hash", name="uq_files_sha256_hash"),
        UniqueConstraint("session_id", "original_filename", name="uq_files_session_filename"),
        CheckConstraint("size_bytes > 0", name="ck_files_size_positive"),
        CheckConstraint("length(sha256_hash) = 64", name="ck_files_sha256_length"),
    )

    file_id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    original_filename: Mapped[str] = mapped_column(String(255))
    #: Not separately indexed: the unique constraint above creates one.
    sha256_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    stored_path: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped["Session"] = relationship(back_populates="files")


class DatasetTable(Base):
    """One table found in an upload, and where its rows were written.

    Rows live in a Parquet file rather than here: they are read once to build
    the graph and shown a page at a time in between, neither of which the
    database is the right place for. ``parquet_path`` is opaque to callers so
    the file can move to an object store without a schema change.
    """

    __tablename__ = "dataset_tables"
    __table_args__ = (
        UniqueConstraint("session_id", "name", name="uq_dataset_tables_session_name"),
        CheckConstraint("row_count >= 0", name="ck_dataset_tables_row_count_non_negative"),
    )

    table_id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    name: Mapped[str] = mapped_column(String(255))
    row_count: Mapped[int] = mapped_column(BigInteger)
    parquet_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped["Session"] = relationship(back_populates="dataset_tables")
    columns: Mapped[list["DatasetColumn"]] = relationship(
        back_populates="table", cascade="all, delete-orphan", order_by="DatasetColumn.position"
    )


class DatasetColumn(Base):
    """A column of a parsed table, in the order it appears in the file."""

    __tablename__ = "dataset_columns"
    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_dataset_columns_table_name"),
    )

    column_id: Mapped[uuid.UUID] = _uuid_pk()
    table_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_tables.table_id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    inferred_type: Mapped[str] = mapped_column(String(64))

    table: Mapped["DatasetTable"] = relationship(back_populates="columns")


class DatasetRelationship(Base):
    """A link between two parsed tables.

    Only formats that declare their own links fill this in — a SQL dump's
    foreign keys. Links for the others are proposed later, from the data.

    The columns are what make the link traversable: naming the two tables says
    they are connected, naming the columns says how. A composite key is stored
    as one comma-joined value, because it is a single link.
    """

    __tablename__ = "dataset_relationships"

    relationship_id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = _session_fk()
    from_table: Mapped[str] = mapped_column(String(255))
    to_table: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(64))
    from_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_column: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Job(Base):
    """Background parse job. ``status == done`` is the idempotency marker the
    Worker checks before doing any work, so a redelivered message is a no-op."""

    __tablename__ = "jobs"
    __table_args__ = (
        # Matches the "latest job for a session" lookup the status endpoint
        # runs, and covers session_id on its own as the leading term.
        Index("ix_jobs_session_id_queued_at", "session_id", text("queued_at DESC")),
        CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count_non_negative"),
    )

    job_id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = _session_fk(index=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="jobstatus"), default=JobStatus.queued
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped["Session"] = relationship(back_populates="jobs")
