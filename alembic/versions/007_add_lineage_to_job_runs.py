"""add lineage columns to job_runs

Revision ID: 007
Revises: 006
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("source_connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=True),
    )
    op.add_column(
        "job_runs",
        sa.Column("destination_path", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "destination_path")
    op.drop_column("job_runs", "source_connection_id")
