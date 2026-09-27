"""Record what each upload parsed into: tables, columns and declared links

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_tables",
        sa.Column("table_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        # Where the rows were written. Opaque to callers, so the file can move
        # to an object store without touching the schema.
        sa.Column("parquet_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("table_id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "name", name="uq_dataset_tables_session_name"),
        sa.CheckConstraint("row_count >= 0", name="ck_dataset_tables_row_count_non_negative"),
    )
    op.create_index("ix_dataset_tables_session_id", "dataset_tables", ["session_id"])

    op.create_table(
        "dataset_columns",
        sa.Column("column_id", sa.UUID(), nullable=False),
        sa.Column("table_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("inferred_type", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("column_id"),
        sa.ForeignKeyConstraint(["table_id"], ["dataset_tables.table_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("table_id", "name", name="uq_dataset_columns_table_name"),
    )
    op.create_index("ix_dataset_columns_table_id", "dataset_columns", ["table_id"])

    op.create_table(
        "dataset_relationships",
        sa.Column("relationship_id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("from_table", sa.String(length=255), nullable=False),
        sa.Column("to_table", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("relationship_id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.session_id"], ondelete="CASCADE"),
    )
    op.create_index("ix_dataset_relationships_session_id", "dataset_relationships", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_dataset_relationships_session_id", table_name="dataset_relationships")
    op.drop_table("dataset_relationships")

    op.drop_index("ix_dataset_columns_table_id", table_name="dataset_columns")
    op.drop_table("dataset_columns")

    op.drop_index("ix_dataset_tables_session_id", table_name="dataset_tables")
    op.drop_table("dataset_tables")
