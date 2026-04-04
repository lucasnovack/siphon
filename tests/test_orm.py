"""Smoke tests — verify ORM models are importable and have the expected columns."""

from siphon.orm import Base, Connection, JobRun, Pipeline, RefreshToken, Schedule, User


def test_user_columns():
    cols = {c.name for c in User.__table__.columns}
    assert cols == {"id", "email", "hashed_password", "role", "is_active", "created_at", "updated_at"}


def test_refresh_token_columns():
    cols = {c.name for c in RefreshToken.__table__.columns}
    assert cols == {"id", "user_id", "token_hash", "issued_at", "expires_at", "revoked_at", "replaced_by"}


def test_connection_columns():
    cols = {c.name for c in Connection.__table__.columns}
    assert cols == {"id", "name", "conn_type", "encrypted_config", "key_version", "created_at", "updated_at"}


def test_pipeline_columns():
    cols = {c.name for c in Pipeline.__table__.columns}
    assert {"id", "name", "source_connection_id", "query", "destination_path",
            "extraction_mode", "incremental_key", "last_watermark", "last_schema_hash",
            "min_rows_expected", "max_rows_drop_pct", "created_at", "updated_at"}.issubset(cols)


def test_schedule_columns():
    cols = {c.name for c in Schedule.__table__.columns}
    assert cols == {"id", "pipeline_id", "cron", "is_active", "next_run_at", "created_at", "updated_at"}


def test_job_run_columns():
    cols = {c.name for c in JobRun.__table__.columns}
    assert {"id", "job_id", "pipeline_id", "schedule_id", "status", "rows_read",
            "rows_written", "duration_ms", "error", "schema_changed",
            "started_at", "finished_at", "created_at"}.issubset(cols)


def test_base_metadata_has_all_tables():
    assert set(Base.metadata.tables.keys()) == {
        "users", "refresh_tokens", "connections", "pipelines", "schedules", "job_runs"
    }


def test_pipeline_has_pii_columns_attr():
    import uuid as _uuid
    import datetime as _dt
    from siphon.orm import Pipeline
    p = Pipeline(
        name="test",
        source_connection_id=_uuid.uuid4(),
        query="SELECT 1",
        destination_path="bronze/t/",
        created_at=_dt.datetime.now(),
        updated_at=_dt.datetime.now(),
    )
    assert hasattr(p, "pii_columns")
    assert p.pii_columns is None  # nullable, defaults to None


def test_pipeline_has_webhook_and_sla_attrs():
    import uuid as _uuid
    import datetime as _dt
    from siphon.orm import Pipeline
    p = Pipeline(
        name="webhook-test",
        source_connection_id=_uuid.uuid4(),
        query="SELECT 1",
        destination_path="bronze/t/",
        created_at=_dt.datetime.now(),
        updated_at=_dt.datetime.now(),
    )
    assert hasattr(p, "webhook_url")
    assert p.webhook_url is None
    assert hasattr(p, "alert_on")
    assert p.alert_on is None
    assert hasattr(p, "sla_minutes")
    assert p.sla_minutes is None
    assert hasattr(p, "sla_notified_at")
    assert p.sla_notified_at is None
