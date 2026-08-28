"""Initial upload schema: sessions, files, jobs

Revision ID: 0001
Revises:
Create Date: 2026-08-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FILE_FORMAT = sa.Enum("csv", "json", "sql", "parquet", name="fileformat")
SESSION_STATUS = sa.Enum(
    "uploading", "queued", "processing", "ready", "failed", name="sessionstatus"
)
JOB_STATUS = sa.Enum("queued", "processing", "done", "failed", name="jobstatus")


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("format", FILE_FORMAT, nullable=False),
        sa.Column("total_files", sa.Integer(), nullable=False),
        sa.Column("total_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", SESSION_STATUS, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
        sa.CheckConstraint("total_files > 0", name="ck_sessions_total_files_positive"),
        sa.CheckConstraint("total_size_bytes > 0", name="ck_sessions_total_size_positive"),
    )

    op.create_table(
        "files",
        sa.Column("file_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("file_id"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        # Enforces the duplicate rules that the API also checks: the same bytes
        # cannot be uploaded twice, and one session cannot hold two files of
        # the same name.
        sa.UniqueConstraint("sha256_hash", name="uq_files_sha256_hash"),
        sa.UniqueConstraint(
            "session_id", "original_filename", name="uq_files_session_filename"
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_files_size_positive"),
        sa.CheckConstraint("length(sha256_hash) = 64", name="ck_files_sha256_length"),
    )
    # The Worker loads a session's files by this column.
    op.create_index("ix_files_session_id", "files", ["session_id"])

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("status", JOB_STATUS, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.session_id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count_non_negative"),
    )
    # Serves the status endpoint's "latest job for this session" lookup, and
    # covers session_id alone as its leading term.
    op.create_index(
        "ix_jobs_session_id_queued_at",
        "jobs",
        ["session_id", sa.text("queued_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_session_id_queued_at", table_name="jobs")
    op.drop_table("jobs")

    op.drop_index("ix_files_session_id", table_name="files")
    op.drop_table("files")

    op.drop_table("sessions")

    # Dropping the tables leaves the enum types behind, so remove them too.
    bind = op.get_bind()
    JOB_STATUS.drop(bind, checkfirst=True)
    SESSION_STATUS.drop(bind, checkfirst=True)
    FILE_FORMAT.drop(bind, checkfirst=True)
