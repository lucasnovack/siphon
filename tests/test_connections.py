# tests/test_connections.py
import importlib
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal
from siphon.db import get_db


def _fernet_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _admin():
    user = MagicMock()
    user.role = "admin"
    return Principal(type="user", user=user)


def _make_conn_row(name="prod-mysql"):
    row = MagicMock()
    row.id = uuid.uuid4()
    row.name = name
    row.conn_type = "sql"
    row.encrypted_config = b"encrypted"
    row.key_version = 1
    row.created_at = datetime.now(tz=UTC)
    row.updated_at = datetime.now(tz=UTC)
    return row


@pytest.fixture()
def client(monkeypatch):
    key = _fernet_key()
    monkeypatch.setenv("SIPHON_ENCRYPTION_KEY", key)
    import siphon.crypto as crypto
    importlib.reload(crypto)

    from siphon.auth.deps import get_current_principal
    from siphon.connections.router import router
    app = FastAPI()
    app.include_router(router)
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db_mock
    app.dependency_overrides[get_current_principal] = lambda: _admin()
    return TestClient(app), db_mock


def test_list_connections_returns_200(client):
    tc, db = client
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    resp = tc.get("/api/v1/connections")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_connection_returns_404(client):
    tc, db = client
    db.get = AsyncMock(return_value=None)
    resp = tc.get(f"/api/v1/connections/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_create_connection_returns_201(client, monkeypatch):
    tc, db = client
    row = _make_conn_row()
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    ))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda r: None)
    # After add+commit+refresh, db.get returns the row
    db.get = AsyncMock(return_value=row)

    with patch("siphon.connections.router._after_create", return_value=row):
        resp = tc.post("/api/v1/connections", json={
            "name": "prod-mysql",
            "conn_type": "sql",
            "config": {"connection": "mysql://u:p@host/db"},
        })
    assert resp.status_code == 201


def test_response_never_exposes_config(monkeypatch):
    """The response must not include the decrypted config."""
    monkeypatch.setenv("SIPHON_ENCRYPTION_KEY", _fernet_key())
    import siphon.crypto as crypto
    importlib.reload(crypto)
    from siphon.connections.router import _to_response
    row = _make_conn_row()
    resp = _to_response(row)
    resp_dict = resp.model_dump()
    assert "config" not in resp_dict
    assert "encrypted_config" not in resp_dict


def test_get_connection_types_returns_three_types(client):
    tc, _ = client
    resp = tc.get("/api/v1/connections/types/list")
    assert resp.status_code == 200
    types = [t["type"] for t in resp.json()]
    assert "sql" in types
    assert "sftp" in types
    assert "s3_parquet" in types


def test_test_connection_unknown_type_raises(monkeypatch):
    monkeypatch.setenv("SIPHON_ENCRYPTION_KEY", _fernet_key())
    from siphon.connections.router import _test_connection
    with pytest.raises(ValueError, match="Unknown connection type"):
        _test_connection("unknown_type", {})


def test_test_connection_sql_calls_connectorx(monkeypatch):
    monkeypatch.setenv("SIPHON_ENCRYPTION_KEY", _fernet_key())
    import connectorx as cx
    with patch.object(cx, "read_sql", return_value=None) as mock_cx:
        from siphon.connections.router import _test_connection
        _test_connection("sql", {"connection": "mysql://u:p@host/db"})
        mock_cx.assert_called_once()


def test_create_connection_conflict_returns_409(client):
    tc, db = client
    row = _make_conn_row()
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=row)  # already exists
    ))
    resp = tc.post("/api/v1/connections", json={
        "name": "prod-mysql",
        "conn_type": "sql",
        "config": {"connection": "mysql://u:p@host/db"},
    })
    assert resp.status_code == 409


def test_update_connection_returns_200(client):
    tc, db = client
    row = _make_conn_row()
    db.get = AsyncMock(return_value=row)
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)  # no duplicate
    ))
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: None)
    resp = tc.put(f"/api/v1/connections/{row.id}", json={"name": "renamed"})
    assert resp.status_code == 200


def test_delete_connection_returns_204(client):
    tc, db = client
    row = _make_conn_row()
    db.get = AsyncMock(return_value=row)
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    resp = tc.delete(f"/api/v1/connections/{row.id}")
    assert resp.status_code == 204


def test_create_connection_requires_admin(monkeypatch):
    monkeypatch.setenv("SIPHON_ENCRYPTION_KEY", _fernet_key())
    from siphon.auth.deps import get_current_principal
    from siphon.connections.router import router
    app = FastAPI()
    app.include_router(router)
    db_mock = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db_mock

    op = MagicMock()
    op.role = "operator"
    from siphon.auth.deps import Principal
    app.dependency_overrides[get_current_principal] = lambda: Principal(type="user", user=op)
    tc = TestClient(app)
    resp = tc.post("/api/v1/connections", json={
        "name": "test",
        "conn_type": "sql",
        "config": {"connection": "mysql://u:p@host/db"},
    })
    assert resp.status_code == 403
