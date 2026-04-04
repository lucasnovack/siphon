"""Add webhook_url, alert_on, sla_minutes, sla_notified_at to pipelines

Revision ID: 004
Revises: 003
Create Date: 2026-04-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipelines", sa.Column("webhook_url", sa.String(500), nullable=True))
    op.add_column(
        "pipelines",
        sa.Column("alert_on", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("pipelines", sa.Column("sla_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "pipelines",
        sa.Column("sla_notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pipelines", "sla_notified_at")
    op.drop_column("pipelines", "sla_minutes")
    op.drop_column("pipelines", "alert_on")
    op.drop_column("pipelines", "webhook_url")
