"""add max_concurrent_jobs to connections

Revision ID: 008
Revises: 007
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column(
            "max_concurrent_jobs",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )


def downgrade() -> None:
    op.drop_column("connections", "max_concurrent_jobs")
