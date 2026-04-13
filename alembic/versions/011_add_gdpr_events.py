"""add gdpr_events table

Revision ID: 011
Revises: 010
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gdpr_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pipeline_id", UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("before_date", sa.Date(), nullable=True),
        sa.Column("partition_filter", sa.Text(), nullable=True),
        sa.Column("files_deleted", sa.Integer(), nullable=True),
        sa.Column("bytes_deleted", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("gdpr_events")
