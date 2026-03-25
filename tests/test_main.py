# tests/test_main.py
"""
HTTP integration tests for the FastAPI app.

Strategy: register fake "sql" and "s3_parquet" plugins at module level so that
API requests with those types pass validation and route to working stubs.
Queue state is reset between tests via the `reset_queue` fixture.
"""
import os

import pyarrow as pa
import pytest
from starlette.testclient import TestClient

from siphon.plugins.destinations import register as register_dest
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources import register as register_source
from siphon.plugins.sources.base import Source

# ── Register fake plugins for the test session ────────────────────────────────
# These must be registered before `siphon.main` is imported so the queue is
# ready to dispatch them.

@register_source("sql")
class _FakeSQLSource(Source):
    def __init__(self, connection: str, query: str, **kwargs) -> None:
        pass

    def extract(self) -> pa.Table:
        return pa.table({"id": [1, 2, 3]})


@register_dest("s3_parquet")
class _FakeS3Dest(Destination):
    def __init__(self, path: str, endpoint: str, access_key: str, secret_key: str, **kwargs) -> None:
        pass

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return table.num_rows


# ── Import app AFTER plugin registration ─────────────────────────────────────
import siphon.main as main_module  # noqa: E402
from siphon.main import app  # noqa: E402

# ── Shared request fixtures ───────────────────────────────────────────────────

VALID_REQUEST = {
    "source": {"type": "sql", "connection": "mysql://u:p@h/db", "query": "SELECT 1"},
    "destination": {
        "type": "s3_parquet",
        "path": "s3a://bronze/x/2026-03-25",
        "endpoint": "minio:9000",
        "access_key": "k",
        "secret_key": "s",
    },
}


@pytest.fixture(autouse=True)
def reset_queue():
    """Reset queue state between tests to avoid cross-test pollution."""
    q = main_module.queue
    q._jobs.clear()
    q._active = 0
    q._queued = 0
    q._draining = False
    yield
    q._jobs.clear()
    q._active = 0
    q._queued = 0
    q._draining = False


@pytest.fixture
def client():
    """TestClient triggers lifespan (queue.start() and queue.drain())."""
    # Use a short drain timeout so tests don't hang waiting for background jobs.
    # DRAIN_TIMEOUT is read by the lifespan coroutine at drain-call time, so
    # patching the module variable here keeps it in effect through __exit__.
    main_module.DRAIN_TIMEOUT = 2
    with TestClient(app) as tc:
        yield tc


# ── POST /jobs ────────────────────────────────────────────────────────────────

def test_post_jobs_returns_202(client):
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_post_jobs_returns_job_id(client):
    r1 = client.post("/jobs", json=VALID_REQUEST)
    r2 = client.post("/jobs", json=VALID_REQUEST)
    assert r1.json()["job_id"] != r2.json()["job_id"]  # unique UUIDs


def test_post_jobs_429_when_queue_full(client):
    q = main_module.queue
    # Manually saturate the queue
    q._active = q._max_workers
    q._queued = q._max_queue
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 429
    assert "Queue is full" in response.json()["detail"]


def test_post_jobs_invalid_source_type_returns_422(client):
    bad = {**VALID_REQUEST, "source": {"type": "unknown", "connection": "x", "query": "y"}}
    response = client.post("/jobs", json=bad)
    assert response.status_code == 422


# ── POST /extract ─────────────────────────────────────────────────────────────

def test_post_extract_returns_200_with_status(client):
    response = client.post("/extract", json=VALID_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("success", "failed", "running", "queued")
    assert "job_id" in body


def test_post_extract_429_when_queue_full(client):
    q = main_module.queue
    q._active = q._max_workers
    q._queued = q._max_queue
    response = client.post("/extract", json=VALID_REQUEST)
    assert response.status_code == 429


# ── GET /jobs/{id} ────────────────────────────────────────────────────────────

def test_get_job_returns_status(client):
    post = client.post("/jobs", json=VALID_REQUEST)
    job_id = post.json()["job_id"]
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] in ("queued", "running", "success", "failed")


def test_get_job_404_for_unknown(client):
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


# ── GET /jobs/{id}/logs ───────────────────────────────────────────────────────

def test_get_job_logs_returns_logs_response(client):
    post = client.post("/jobs", json=VALID_REQUEST)
    job_id = post.json()["job_id"]
    response = client.get(f"/jobs/{job_id}/logs")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert isinstance(body["logs"], list)
    assert "next_offset" in body


def test_get_job_logs_with_since_offset(client):
    post = client.post("/jobs", json=VALID_REQUEST)
    job_id = post.json()["job_id"]
    # Wait briefly for job to produce logs
    import time
    time.sleep(0.2)
    # Full logs
    full = client.get(f"/jobs/{job_id}/logs?since=0").json()
    # Skip first log line
    partial = client.get(f"/jobs/{job_id}/logs?since=1").json()
    assert partial["next_offset"] == full["next_offset"]
    if len(full["logs"]) > 1:
        assert partial["logs"] == full["logs"][1:]


def test_get_job_logs_404_for_unknown(client):
    response = client.get("/jobs/nonexistent/logs")
    assert response.status_code == 404


# ── GET /health/live ──────────────────────────────────────────────────────────

def test_health_live_returns_200(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── GET /health/ready ─────────────────────────────────────────────────────────

def test_health_ready_returns_200_when_accepting(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["accepting_jobs"] is True


def test_health_ready_returns_503_when_queue_full(client):
    q = main_module.queue
    q._active = q._max_workers
    q._queued = q._max_queue
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["accepting_jobs"] is False
    assert body["reason"] == "queue_full"


def test_health_ready_returns_503_when_draining(client):
    main_module.queue._draining = True
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["accepting_jobs"] is False
    assert body["reason"] == "draining"


# ── GET /health ───────────────────────────────────────────────────────────────

def test_health_debug_returns_full_info(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "accepting_jobs" in body
    assert "queue" in body
    assert "uptime_seconds" in body
    assert body["queue"]["workers_max"] == main_module.queue._max_workers


# ── Security: API key middleware ──────────────────────────────────────────────

def test_api_key_required_returns_401(client, monkeypatch):
    monkeypatch.setenv("SIPHON_API_KEY", "secret-key")
    # Reload the middleware-relevant state by patching the module variable
    main_module.API_KEY = "secret-key"
    try:
        response = client.post("/jobs", json=VALID_REQUEST)
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"
    finally:
        main_module.API_KEY = None


def test_api_key_correct_passes(client, monkeypatch):
    main_module.API_KEY = "secret-key"
    try:
        response = client.post(
            "/jobs",
            json=VALID_REQUEST,
            headers={"Authorization": "Bearer secret-key"},
        )
        assert response.status_code == 202
    finally:
        main_module.API_KEY = None


def test_health_live_bypasses_api_key(client):
    main_module.API_KEY = "secret-key"
    try:
        response = client.get("/health/live")  # no auth header
        assert response.status_code == 200
    finally:
        main_module.API_KEY = None


def test_health_ready_bypasses_api_key(client):
    main_module.API_KEY = "secret-key"
    try:
        response = client.get("/health/ready")  # no auth header
        assert response.status_code == 200
    finally:
        main_module.API_KEY = None


# ── Security: request size limit ─────────────────────────────────────────────

def test_request_too_large_returns_413(client, monkeypatch):
    """SIPHON_MAX_REQUEST_SIZE_MB=0 means any non-empty body is rejected."""
    monkeypatch.setenv("SIPHON_MAX_REQUEST_SIZE_MB", "0")
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 413


# ── Security: 422 handler suppresses body ────────────────────────────────────

def test_422_does_not_include_raw_body(client):
    """Validation errors must not echo the request body (credentials leak prevention)."""
    # Use a request with a sensitive-looking value that would appear in "input" if leaked
    bad_req = {
        "source": {
            "type": "sql",
            "connection": "mysql://admin:SuperSecret123@prod-db.example.com/mydb",
            # missing "query" field to trigger validation error
        },
        "destination": {
            "type": "s3_parquet",
            "path": "s3a://bronze/x",
            "endpoint": "minio:9000",
            "access_key": "k",
            "secret_key": "s",
        },
    }
    response = client.post("/jobs", json=bad_req)
    assert response.status_code == 422
    body = response.json()
    response_text = str(body)
    # The password must NOT appear anywhere in the response
    assert "SuperSecret123" not in response_text
    assert "admin" not in response_text
    # Errors should still provide useful information (type, loc, msg)
    assert "detail" in body
    errors = body.get("errors", [])
    assert len(errors) > 0
    # No error dict should have an "input" key
    for err in errors:
        assert "input" not in err, f"Error dict leaks input data: {err}"
