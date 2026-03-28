"""Add dest_connection_id to pipelines and triggered_by to job_runs

Revision ID: 002
Revises: 001
Create Date: 2026-03-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipelines",
        sa.Column(
            "dest_connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "job_runs",
        sa.Column(
            "triggered_by",
            sa.String(20),
            nullable=False,
            server_default="api",
        ),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "triggered_by")
    op.drop_column("pipelines", "dest_connection_id")
