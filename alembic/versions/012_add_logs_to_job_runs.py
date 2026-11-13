"""add logs column to job_runs

Revision ID: 012
Revises: 011
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("logs", JSONB().with_variant(sa.JSON(), "sqlite"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "logs")
