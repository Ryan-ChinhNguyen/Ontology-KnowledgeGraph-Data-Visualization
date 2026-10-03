"""Record which columns a relationship links, not just which tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable because a relationship proposed from the data, rather than read
    # from a declared foreign key, may not name a single column pair.
    op.add_column(
        "dataset_relationships",
        sa.Column("from_column", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "dataset_relationships",
        sa.Column("to_column", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_relationships", "to_column")
    op.drop_column("dataset_relationships", "from_column")
