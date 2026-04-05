"""Add last_schema and expected_schema to pipelines

Revision ID: 006
Revises: 005
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipelines", sa.Column("last_schema", JSONB, nullable=True))
    op.add_column("pipelines", sa.Column("expected_schema", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("pipelines", "expected_schema")
    op.drop_column("pipelines", "last_schema")
