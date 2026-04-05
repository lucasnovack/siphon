# tests/test_runs.py
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal

import siphon.runs.router as rr_module


def _admin():
    user = MagicMock()
    user.role = "admin"
    return Principal(type="user", user=user)


def _operator():
    user = MagicMock()
    user.role = "operator"
    return Principal(type="user", user=user)


def _make_run(status="success"):
    r = MagicMock()
    r.id = 1
    r.job_id = str(uuid.uuid4())
    r.pipeline_id = uuid.uuid4()
    r.status = status
    r.triggered_by = "manual"
    r.rows_read = 100
    r.rows_written = 100
    r.duration_ms = 1234
    r.schema_changed = False
    r.error = None
    r.started_at = datetime.now(tz=UTC)
    r.finished_at = datetime.now(tz=UTC)
    r.created_at = datetime.now(tz=UTC)
    return r


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(rr_module.router)
    db_mock = AsyncMock()
    app.dependency_overrides[rr_module.get_db] = lambda: db_mock
    app.dependency_overrides[rr_module.get_current_principal] = lambda: _admin()
    return TestClient(app), db_mock


def test_list_runs_returns_200(client):
    tc, db = client
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    resp = tc.get("/api/v1/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_runs_returns_runs(client):
    tc, db = client
    run = _make_run()
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[run])))
    ))
    resp = tc.get("/api/v1/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["job_id"] == run.job_id


def test_get_run_logs_returns_404_when_run_missing(client):
    tc, db = client
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result_mock)
    resp = tc.get("/api/v1/runs/999/logs")
    assert resp.status_code == 404


def test_get_run_logs_returns_empty_when_job_evicted(client):
    tc, db = client
    run = _make_run()
    db.get = AsyncMock(return_value=run)

    from unittest.mock import patch
    with patch("siphon.main.queue") as mock_queue:
        mock_queue.get_job.return_value = None
        resp = tc.get(f"/api/v1/runs/{run.id}/logs")
    assert resp.status_code == 200
    assert resp.json()["logs"] == []


def test_get_run_logs_cursor(client):
    tc, db = client
    run = _make_run(status="running")
    db.get = AsyncMock(return_value=run)

    from siphon.models import Job
    job = MagicMock(spec=Job)
    job.logs = ["line1", "line2", "line3"]

    from unittest.mock import patch
    with patch("siphon.main.queue") as mock_queue:
        mock_queue.get_job.return_value = job
        resp = tc.get(f"/api/v1/runs/{run.id}/logs?since=1")

    assert resp.status_code == 200
    body = resp.json()
    assert body["logs"] == ["line2", "line3"]
    assert body["next_offset"] == 3


def test_cancel_run_requires_admin(client):
    tc, db = client
    from siphon.auth.deps import get_current_principal
    app2 = FastAPI()
    app2.include_router(rr_module.router)
    db_mock2 = AsyncMock()
    app2.dependency_overrides[rr_module.get_db] = lambda: db_mock2
    app2.dependency_overrides[rr_module.get_current_principal] = lambda: _operator()
    tc2 = TestClient(app2)
    db_mock2.get = AsyncMock(return_value=_make_run(status="queued"))
    resp = tc2.post("/api/v1/runs/1/cancel")
    assert resp.status_code == 403


def test_cancel_queued_run(client):
    tc, db = client
    run = _make_run(status="queued")
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()

    from siphon.models import Job
    job = MagicMock(spec=Job)
    job.status = "queued"
    job.logs = []

    from unittest.mock import patch
    with patch("siphon.main.queue") as mock_queue:
        mock_queue.get_job.return_value = job
        resp = tc.post(f"/api/v1/runs/{run.id}/cancel")

    assert resp.status_code == 202
    assert job.status == "failed"


def test_cancel_already_finished_returns_409(client):
    tc, db = client
    run = _make_run(status="success")
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = run
    db.execute = AsyncMock(return_value=result_mock)
    resp = tc.post(f"/api/v1/runs/{run.id}/cancel")
    assert resp.status_code == 409
