# tests/test_main.py
"""
HTTP integration tests for the FastAPI app.

Strategy: register fake "sql" and "s3_parquet" plugins at module level so that
API requests with those types pass validation and route to working stubs.
Queue is now Celery-backed; submit() is patched to avoid needing a real broker.
"""

from unittest.mock import MagicMock, patch

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
    def __init__(
        self, path: str, endpoint: str, access_key: str, secret_key: str, **kwargs
    ) -> None:
        pass

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return table.num_rows


# ── Import app AFTER plugin registration ─────────────────────────────────────
import siphon.main as main_module  # noqa: E402
from siphon.auth.deps import Principal, get_current_principal  # noqa: E402
from siphon.main import app  # noqa: E402

# Override auth for all main tests (no DATABASE_URL in unit test env)
_ANON_PRINCIPAL = Principal(type="api_key")


async def _override_principal():
    return _ANON_PRINCIPAL


app.dependency_overrides[get_current_principal] = _override_principal

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
def patch_celery_submit():
    """Patch run_pipeline_task.apply_async so tests don't need a real Redis broker."""
    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock()
        yield mock_task


@pytest.fixture
def client():
    """TestClient triggers lifespan (queue.start() and queue.drain())."""
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


def test_post_jobs_invalid_source_type_returns_422(client):
    bad = {**VALID_REQUEST, "source": {"type": "unknown", "connection": "x", "query": "y"}}
    response = client.post("/jobs", json=bad)
    assert response.status_code == 422


# ── POST /extract ─────────────────────────────────────────────────────────────


def test_post_extract_returns_200_with_status(client, monkeypatch):
    monkeypatch.setenv("SIPHON_ENABLE_SYNC_EXTRACT", "true")
    main_module.ENABLE_SYNC_EXTRACT = True
    try:
        response = client.post("/extract", json=VALID_REQUEST)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("success", "failed", "running", "queued")
        assert "job_id" in body
    finally:
        main_module.ENABLE_SYNC_EXTRACT = False


def test_post_extract_disabled_by_default(client):
    """POST /extract returns 404 unless SIPHON_ENABLE_SYNC_EXTRACT=true."""
    main_module.ENABLE_SYNC_EXTRACT = False
    response = client.post("/extract", json=VALID_REQUEST)
    assert response.status_code == 404


# ── GET /jobs/{id} ────────────────────────────────────────────────────────────


def test_get_job_404_for_unknown(client):
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


# ── GET /jobs/{id}/logs ───────────────────────────────────────────────────────


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


# ── GET /health ───────────────────────────────────────────────────────────────


def test_health_debug_returns_full_info(client):
    admin_user = MagicMock()
    admin_user.role = "admin"
    admin_principal = Principal(type="user", user=admin_user)
    app.dependency_overrides[get_current_principal] = lambda: admin_principal
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides[get_current_principal] = _override_principal
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "accepting_jobs" in body
    assert "queue" in body
    assert "uptime_seconds" in body
    assert body["queue"]["backend"] == "celery"


# ── Security: request size limit ─────────────────────────────────────────────


def test_request_too_large_returns_413(client, monkeypatch):
    """SIPHON_MAX_REQUEST_SIZE_MB=0 means any non-empty body is rejected."""
    monkeypatch.setenv("SIPHON_MAX_REQUEST_SIZE_MB", "0")
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 413


# ── Security: 422 handler suppresses body ────────────────────────────────────


def test_422_does_not_include_raw_body(client):
    """Validation errors must not echo the request body (credentials leak prevention)."""
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
    assert "SuperSecret123" not in response_text
    assert "admin" not in response_text
    assert "detail" in body
    errors = body.get("errors", [])
    assert len(errors) > 0
    for err in errors:
        assert "input" not in err, f"Error dict leaks input data: {err}"


# ── Phase 8 additions — API key via get_current_principal ─────────────────────


def test_existing_post_jobs_still_works_with_api_key(patch_celery_submit):
    """POST /jobs must still work with SIPHON_API_KEY after middleware→dependency swap."""
    import importlib
    with patch.dict("os.environ", {"SIPHON_API_KEY": "testkey"}):
        import siphon.db as db_module
        importlib.reload(db_module)
        import siphon.auth.deps as deps_module
        importlib.reload(deps_module)
        importlib.reload(main_module)
        from siphon.auth.deps import Principal as CurrentPrincipal
        from siphon.auth.deps import get_current_principal as current_gcp
        from siphon.main import app as reloaded_app

        async def _override():
            return CurrentPrincipal(type="api_key")

        reloaded_app.dependency_overrides[current_gcp] = _override
        client = TestClient(reloaded_app)
        resp = client.post(
            "/jobs",
            json=VALID_REQUEST,
            headers={"Authorization": "Bearer testkey"},
        )
        assert resp.status_code == 202


def test_existing_post_jobs_blocked_without_api_key(patch_celery_submit):
    """POST /jobs returns 401 when SIPHON_API_KEY is set and header is missing."""
    import importlib
    with patch.dict("os.environ", {"SIPHON_API_KEY": "testkey"}):
        import siphon.db as db_module
        importlib.reload(db_module)
        import siphon.auth.deps as deps_module
        importlib.reload(deps_module)
        importlib.reload(main_module)

        from unittest.mock import AsyncMock

        from siphon.db import get_db as current_get_db
        from siphon.main import app as reloaded_app

        async def _mock_db():
            yield AsyncMock()

        reloaded_app.dependency_overrides[current_get_db] = _mock_db
        client = TestClient(reloaded_app)
        resp = client.post("/jobs", json=VALID_REQUEST)
        assert resp.status_code == 401


def test_health_live_no_auth_needed(patch_celery_submit):
    """GET /health/live must not require auth (Kubernetes probe)."""
    import importlib
    with patch.dict("os.environ", {"SIPHON_API_KEY": "testkey"}):
        importlib.reload(main_module)
        from siphon.main import app as reloaded_app
        client = TestClient(reloaded_app)
        resp = client.get("/health/live")
        assert resp.status_code == 200


def test_health_ready_no_auth_needed(patch_celery_submit):
    """GET /health/ready must not require auth (Kubernetes probe)."""
    import importlib
    with patch.dict("os.environ", {"SIPHON_API_KEY": "testkey"}):
        importlib.reload(main_module)
        from siphon.main import app as reloaded_app
        client = TestClient(reloaded_app)
        resp = client.get("/health/ready")
        assert resp.status_code in (200, 503)
