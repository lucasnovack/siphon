# tests/test_pipelines.py
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal


def _admin():
    user = MagicMock()
    user.role = "admin"
    return Principal(type="user", user=user)


def _operator():
    user = MagicMock()
    user.role = "operator"
    return Principal(type="user", user=user)


def _make_pipeline():
    p = MagicMock()
    p.id = uuid.uuid4()
    p.name = "bronze_cidades"
    p.source_connection_id = uuid.uuid4()
    p.dest_connection_id = uuid.uuid4()
    p.query = "SELECT * FROM cities"
    p.destination_path = "s3a://bronze/cities/{date}"
    p.extraction_mode = "full_refresh"
    p.incremental_key = None
    p.last_watermark = None
    p.last_schema_hash = None
    p.min_rows_expected = None
    p.max_rows_drop_pct = None
    p.pii_columns = None
    p.webhook_url = None
    p.alert_on = None
    p.sla_minutes = None
    p.partition_by = "none"
    p.priority = "normal"
    p.created_at = datetime.now(tz=UTC)
    p.updated_at = datetime.now(tz=UTC)
    return p


def _make_pipeline_row():
    return _make_pipeline()


@pytest.fixture()
def client():
    import siphon.pipelines.router as pr_module
    app = FastAPI()
    app.include_router(pr_module.router)
    db_mock = AsyncMock()
    # Key overrides on the router's own imports so they survive module reloads
    app.dependency_overrides[pr_module.get_db] = lambda: db_mock
    app.dependency_overrides[pr_module.get_current_principal] = lambda: _admin()
    return TestClient(app), db_mock


def test_list_pipelines_returns_200(client):
    tc, db = client
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    resp = tc.get("/api/v1/pipelines")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_pipeline_404(client):
    tc, db = client
    db.get = AsyncMock(return_value=None)
    resp = tc.get(f"/api/v1/pipelines/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_create_pipeline_201(client):
    tc, db = client
    p = _make_pipeline()
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("siphon.pipelines.router._after_create", return_value=p):
        resp = tc.post("/api/v1/pipelines", json={
            "name": "bronze_cidades",
            "source_connection_id": str(p.source_connection_id),
            "dest_connection_id": str(p.dest_connection_id),
            "query": "SELECT * FROM cities",
            "destination_path": "s3a://bronze/cities/{date}",
            "extraction_mode": "full_refresh",
        })
    assert resp.status_code == 201


def test_delete_pipeline_requires_admin(client):
    tc, db = client
    import siphon.pipelines.router as pr_module
    app2 = FastAPI()
    app2.include_router(pr_module.router)
    app2.dependency_overrides[pr_module.get_db] = lambda: db
    app2.dependency_overrides[pr_module.get_current_principal] = lambda: _operator()
    tc2 = TestClient(app2)
    db.get = AsyncMock(return_value=_make_pipeline())
    resp = tc2.delete(f"/api/v1/pipelines/{uuid.uuid4()}")
    assert resp.status_code == 403


def test_upsert_schedule_creates_schedule(client):
    tc, db = client
    p = _make_pipeline()
    db.get = AsyncMock(return_value=p)
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))
    db.add = MagicMock()
    db.commit = AsyncMock()
    schedule_mock = MagicMock()
    schedule_mock.cron = "0 3 * * *"
    schedule_mock.is_active = True
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("siphon.pipelines.router._after_schedule_upsert", return_value=schedule_mock):
        resp = tc.put(f"/api/v1/pipelines/{p.id}/schedule", json={
            "cron_expr": "0 3 * * *",
            "is_active": True,
        })
    assert resp.status_code == 200


def test_create_pipeline_with_pii_columns(client):
    tc, db = client
    p = _make_pipeline()
    p.pii_columns = {"email": "sha256", "cpf": "sha256", "token": "redact"}
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("siphon.pipelines.router._after_create", return_value=p):
        resp = tc.post("/api/v1/pipelines", json={
            "name": "pii-test-pipeline",
            "source_connection_id": str(p.source_connection_id),
            "dest_connection_id": str(p.dest_connection_id),
            "query": "SELECT * FROM t",
            "destination_path": "bronze/pii-test/",
            "pii_columns": {"email": "sha256", "cpf": "sha256", "token": "redact"},
        })
    assert resp.status_code == 201
    body = resp.json()
    assert body["pii_columns"] == {"email": "sha256", "cpf": "sha256", "token": "redact"}


def test_get_pipeline_returns_pii_columns(client):
    tc, db = client
    p = _make_pipeline()
    p.pii_columns = {"phone": "redact"}
    db.get = AsyncMock(return_value=p)

    resp = tc.get(f"/api/v1/pipelines/{p.id}")
    assert resp.status_code == 200
    assert resp.json()["pii_columns"] == {"phone": "redact"}


def test_update_pipeline_pii_columns(client):
    tc, db = client
    p = _make_pipeline()
    p.pii_columns = {"ssn": "sha256"}
    db.get = AsyncMock(return_value=p)
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    resp = tc.put(f"/api/v1/pipelines/{p.id}", json={
        "pii_columns": {"ssn": "sha256"}
    })
    assert resp.status_code == 200
    assert resp.json()["pii_columns"] == {"ssn": "sha256"}


def _make_pipeline_with_alerts():
    p = _make_pipeline()
    p.webhook_url = "https://hooks.slack.com/xyz"
    p.alert_on = ["failed", "schema_changed"]
    p.sla_minutes = 60
    return p


def test_create_pipeline_with_webhook(client):
    tc, db = client
    p = _make_pipeline_with_alerts()
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("siphon.pipelines.router._after_create", return_value=p):
        resp = tc.post("/api/v1/pipelines", json={
            "name": "webhook-pipeline",
            "source_connection_id": str(uuid.uuid4()),
            "dest_connection_id": str(uuid.uuid4()),
            "query": "SELECT 1",
            "destination_path": "bronze/wh/",
            "webhook_url": "https://hooks.slack.com/xyz",
            "alert_on": ["failed", "schema_changed"],
            "sla_minutes": 60,
        })
    assert resp.status_code == 201
    body = resp.json()
    assert body["webhook_url"] == "https://hooks.slack.com/xyz"
    assert body["alert_on"] == ["failed", "schema_changed"]
    assert body["sla_minutes"] == 60


def test_pipeline_response_includes_webhook_fields(client):
    tc, db = client
    p = _make_pipeline_with_alerts()
    db.get = AsyncMock(return_value=p)
    resp = tc.get(f"/api/v1/pipelines/{p.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "webhook_url" in body
    assert "alert_on" in body
    assert "sla_minutes" in body


def test_pipeline_response_includes_last_schema_and_expected_schema(client):
    """GET /pipelines/{id} returns last_schema and expected_schema fields (may be None)."""
    tc, db = client
    p = _make_pipeline()
    p.last_schema = None
    p.expected_schema = None
    db.get = AsyncMock(return_value=p)

    resp = tc.get(f"/api/v1/pipelines/{p.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "last_schema" in data
    assert "expected_schema" in data
    assert data["last_schema"] is None
    assert data["expected_schema"] is None


def test_put_pipeline_sets_expected_schema(client):
    """PUT /pipelines/{id} accepts expected_schema and returns it on GET."""
    tc, db = client
    p = _make_pipeline()
    p.last_schema = None
    p.expected_schema = None
    db.get = AsyncMock(return_value=p)
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    expected = [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}]
    put_resp = tc.put(
        f"/api/v1/pipelines/{p.id}",
        json={"expected_schema": expected},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["expected_schema"] == expected

    p.expected_schema = expected
    db.get = AsyncMock(return_value=p)
    get_resp = tc.get(f"/api/v1/pipelines/{p.id}")
    assert get_resp.json()["expected_schema"] == expected


def test_create_pipeline_persists_expected_schema(client):
    """POST /api/v1/pipelines persists expected_schema when provided."""
    tc, db = client
    p = _make_pipeline()
    expected = [{"name": "id", "type": "int64"}]
    p.expected_schema = expected
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)

    with patch("siphon.pipelines.router._after_create", return_value=p):
        resp = tc.post("/api/v1/pipelines", json={
            "name": "create-expected-schema-test",
            "source_connection_id": str(p.source_connection_id),
            "dest_connection_id": str(p.dest_connection_id),
            "query": "SELECT 1",
            "destination_path": "s3a://bronze/test/",
            "expected_schema": expected,
        })
    assert resp.status_code == 201
    assert resp.json()["expected_schema"] == expected


def test_pipeline_create_accepts_priority(client):
    """PipelineCreate must accept a priority field and persist it."""
    tc, db = client

    src_conn = MagicMock()
    src_conn.id = uuid.uuid4()
    src_conn.conn_type = "sql"
    src_conn.max_concurrent_jobs = 2

    dest_conn = MagicMock()
    dest_conn.id = uuid.uuid4()

    db.get = AsyncMock(side_effect=lambda model, id_: src_conn if model.__name__ == "Connection" else dest_conn)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    resp = tc.post("/api/v1/pipelines", json={
        "name": "test-priority",
        "source_connection_id": str(src_conn.id),
        "query": "SELECT 1",
        "destination_path": "s3://bucket/path",
        "priority": "high",
    })
    assert resp.status_code == 201


def test_pipeline_response_includes_priority(client):
    """PipelineResponse must include priority field."""
    tc, db = client
    row = _make_pipeline_row()
    row.priority = "high"
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
    ))
    resp = tc.get("/api/v1/pipelines")
    assert resp.status_code == 200
    assert resp.json()[0]["priority"] == "high"
