# tests/test_preview.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal


def _admin():
    user = MagicMock()
    user.role = "admin"
    return Principal(type="user", user=user)


def _make_conn(conn_type="sql"):
    c = MagicMock()
    c.id = uuid.uuid4()
    c.name = "prod-mysql"
    c.conn_type = conn_type
    c.encrypted_config = b"encrypted"
    return c


import pytest
import siphon.preview.router as pr_module


@pytest.fixture()
def client():
    from siphon.db import get_db as _get_db  # noqa: F401 — only for override key lookup
    app = FastAPI()
    app.include_router(pr_module.router)
    db_mock = AsyncMock()
    app.dependency_overrides[pr_module.get_db] = lambda: db_mock
    app.dependency_overrides[pr_module.get_current_principal] = lambda: _admin()
    return TestClient(app), db_mock


def test_preview_unknown_connection_returns_404(client):
    tc, db = client
    db.get = AsyncMock(return_value=None)
    resp = tc.post("/api/v1/preview", json={
        "connection_id": str(uuid.uuid4()),
        "query": "SELECT 1",
    })
    assert resp.status_code == 404


def test_preview_non_sql_connection_returns_400(client):
    tc, db = client
    conn = _make_conn(conn_type="sftp")
    db.get = AsyncMock(return_value=conn)
    resp = tc.post("/api/v1/preview", json={
        "connection_id": str(conn.id),
        "query": "SELECT 1",
    })
    assert resp.status_code == 400
    assert "SQL" in resp.json()["detail"]


def test_preview_invalid_connection_id_returns_400(client):
    tc, db = client
    resp = tc.post("/api/v1/preview", json={
        "connection_id": "not-a-uuid",
        "query": "SELECT 1",
    })
    assert resp.status_code == 400


def test_preview_runs_query_and_returns_rows(client):
    import json as _json
    import pyarrow as pa
    tc, db = client
    conn = _make_conn()
    db.get = AsyncMock(return_value=conn)
    fake_table = pa.table({"id": [1, 2], "name": ["a", "b"]})

    with patch("siphon.crypto.decrypt", return_value=_json.dumps({"connection": "mysql://u:p@host/db"}).encode()):
        with patch("siphon.plugins.sources.sql._validate_host"):
            with patch("connectorx.read_sql", return_value=fake_table):
                resp = tc.post("/api/v1/preview", json={
                    "connection_id": str(conn.id),
                    "query": "SELECT * FROM orders",
                })
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["rows"], list)
    assert len(body["rows"]) == 2
    assert body["row_count"] == 2
    assert "id" in body["columns"]


class TestApplyLimit:
    def test_wraps_in_subquery(self):
        from siphon.preview.router import _apply_limit
        result = _apply_limit("SELECT id FROM t", 100)
        assert "SELECT * FROM" in result
        assert "LIMIT 100" in result

    def test_strips_trailing_semicolon(self):
        from siphon.preview.router import _apply_limit
        result = _apply_limit("SELECT 1;", 100)
        assert result.count(";") == 0

    def test_existing_limit_is_wrapped_not_doubled(self):
        from siphon.preview.router import _apply_limit
        # Even if the original query has LIMIT, the wrapper overrides it
        result = _apply_limit("SELECT id FROM t LIMIT 1000", 100)
        assert "LIMIT 100" in result
