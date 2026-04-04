"""Add partition_by to pipelines

Revision ID: 005
Revises: 004
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipelines",
        sa.Column("partition_by", sa.String(20), nullable=False, server_default="none"),
    )


def downgrade() -> None:
    op.drop_column("pipelines", "partition_by")
