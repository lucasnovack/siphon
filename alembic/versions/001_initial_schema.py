"""Initial schema: users, refresh_tokens, connections, pipelines, schedules, job_runs

Revision ID: 001
Revises:
Create Date: 2026-03-28
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # refresh_tokens
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replaced_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("refresh_tokens.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    # connections
    op.create_table(
        "connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("conn_type", sa.String(20), nullable=False),
        sa.Column("encrypted_config", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # pipelines
    op.create_table(
        "pipelines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "source_connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connections.id"),
            nullable=False,
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("destination_path", sa.String(500), nullable=False),
        sa.Column("extraction_mode", sa.String(20), nullable=False, server_default="full_refresh"),
        sa.Column("incremental_key", sa.String(100), nullable=True),
        sa.Column("last_watermark", sa.String(100), nullable=True),
        sa.Column("last_schema_hash", sa.String(64), nullable=True),
        sa.Column("min_rows_expected", sa.Integer(), nullable=True),
        sa.Column("max_rows_drop_pct", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # schedules
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pipeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("cron", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # job_runs
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column(
            "pipeline_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pipelines.id"), nullable=True
        ),
        sa.Column(
            "schedule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schedules.id"), nullable=True
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rows_read", sa.BigInteger(), nullable=True),
        sa.Column("rows_written", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("schema_changed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_job_runs_job_id", "job_runs", ["job_id"])


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("schedules")
    op.drop_table("pipelines")
    op.drop_table("connections")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
