# tests/test_gdpr.py
"""Tests for GDPR purge API and audit log."""
import uuid
from datetime import UTC, datetime, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal


def _admin():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = "admin"
    return Principal(type="user", user=user)


def _make_pipeline(dest_path="s3://bucket/pipeline-data"):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.destination_path = dest_path
    p.deleted_at = None
    return p


def test_purge_s3_task_lists_and_deletes_files(monkeypatch):
    """purge_s3_data_task must list + delete Parquet files and return counts."""
    from siphon.tasks import _purge_s3_files

    deleted = []

    def fake_list(path):
        return [f"{path}/part1.parquet", f"{path}/part2.parquet"]

    def fake_delete(path):
        deleted.append(path)
        return 1024  # bytes

    result = _purge_s3_files(
        base_path="s3://bucket/pipeline-data",
        before_date=None,
        partition_filter=None,
        list_fn=fake_list,
        delete_fn=fake_delete,
    )

    assert result["files_deleted"] == 2
    assert result["bytes_deleted"] == 2048
    assert len(deleted) == 2


@pytest.fixture()
def client(monkeypatch):
    import siphon.gdpr.router as gdpr_module
    app = FastAPI()
    app.include_router(gdpr_module.router)
    db_mock = AsyncMock()
    app.dependency_overrides[gdpr_module.get_db] = lambda: db_mock
    app.dependency_overrides[gdpr_module.get_current_principal] = lambda: _admin()
    return TestClient(app), db_mock


def test_purge_returns_200_for_small_volume(client):
    """DELETE /pipelines/{id}/data with <1000 files must return 200 with summary."""
    tc, db = client
    pipeline = _make_pipeline()
    db.get = AsyncMock(return_value=pipeline)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch("siphon.gdpr.router._purge_s3_files", return_value={"files_deleted": 5, "bytes_deleted": 10240}):
        with patch("siphon.gdpr.router._count_s3_files", return_value=5):
            resp = tc.delete(f"/api/v1/pipelines/{pipeline.id}/data")

    assert resp.status_code == 200
    assert resp.json()["files_deleted"] == 5


def test_purge_returns_202_for_large_volume(client):
    """DELETE /pipelines/{id}/data with >=1000 files must return 202 and dispatch Celery task."""
    tc, db = client
    pipeline = _make_pipeline()
    db.get = AsyncMock(return_value=pipeline)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch("siphon.gdpr.router._count_s3_files", return_value=1500):
        with patch("siphon.gdpr.router.purge_s3_data_task") as mock_task:
            mock_task.apply_async = MagicMock()
            resp = tc.delete(f"/api/v1/pipelines/{pipeline.id}/data")

    assert resp.status_code == 202
    assert "event_id" in resp.json()
    mock_task.apply_async.assert_called_once()


def test_purge_requires_admin(monkeypatch):
    """DELETE /pipelines/{id}/data must return 403 for non-admin users."""
    import siphon.gdpr.router as gdpr_module
    operator = MagicMock()
    operator.role = "operator"
    principal = Principal(type="user", user=operator)

    test_app = FastAPI()
    test_app.include_router(gdpr_module.router)
    db_mock = AsyncMock()
    test_app.dependency_overrides[gdpr_module.get_db] = lambda: db_mock
    test_app.dependency_overrides[gdpr_module.get_current_principal] = lambda: principal
    tc = TestClient(test_app)

    resp = tc.delete(f"/api/v1/pipelines/{uuid.uuid4()}/data")
    assert resp.status_code == 403


def test_list_gdpr_events_returns_200(client):
    """GET /api/v1/gdpr/events must return paginated list of purge events."""
    tc, db = client
    event = MagicMock()
    event.id = uuid.uuid4()
    event.pipeline_id = uuid.uuid4()
    event.requested_by = uuid.uuid4()
    event.before_date = None
    event.partition_filter = None
    event.files_deleted = 10
    event.bytes_deleted = 20480
    event.status = "completed"
    event.requested_at = datetime.now(tz=UTC)
    event.completed_at = datetime.now(tz=UTC)
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[event])))
    ))
    resp = tc.get("/api/v1/gdpr/events")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["status"] == "completed"
