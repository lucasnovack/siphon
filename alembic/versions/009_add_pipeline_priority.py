"""add priority to pipelines

Revision ID: 009
Revises: 008
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipelines",
        sa.Column(
            "priority",
            sa.String(10),
            nullable=False,
            server_default="normal",
        ),
    )


def downgrade() -> None:
    op.drop_column("pipelines", "priority")
