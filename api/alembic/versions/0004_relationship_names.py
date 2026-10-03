"""Name each relationship, so a link reads as a phrase rather than as columns

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable because a link recorded before this existed has no name, and
    # because the ontology stage fills these in for links it proposes rather
    # than every link having one from the start.
    op.add_column(
        "dataset_relationships",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "dataset_relationships",
        sa.Column("inverse_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_relationships", "inverse_name")
    op.drop_column("dataset_relationships", "name")
