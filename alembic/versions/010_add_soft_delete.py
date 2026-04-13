"""add deleted_at soft delete columns

Revision ID: 010
Revises: 009
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("connections", "pipelines", "schedules", "users"):
        op.add_column(
            table,
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    for table in ("connections", "pipelines", "schedules", "users"):
        op.drop_column(table, "deleted_at")
