# tests/test_users.py
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal, get_current_principal
from siphon.db import get_db
from siphon.orm import User
from siphon.users.router import router as users_router


def _make_user(role: str = "admin") -> User:
    u = MagicMock(spec=User)
    u.id = uuid.uuid4()
    u.email = f"{role}@example.com"
    u.hashed_password = "hashed"
    u.role = role
    u.is_active = True
    u.created_at = datetime.now(tz=UTC)
    u.updated_at = datetime.now(tz=UTC)
    u.deleted_at = None
    return u


def _make_user_row(role: str = "operator") -> User:
    return _make_user(role)


def _users_app(mock_session: AsyncMock, current_user: User) -> TestClient:
    app = FastAPI()
    app.include_router(users_router)

    async def override_db():
        yield mock_session

    async def override_principal():
        return Principal(type="user", user=current_user)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_principal] = override_principal
    return TestClient(app, raise_server_exceptions=False)


def test_list_users_admin_success():
    admin = _make_user("admin")
    user2 = _make_user("operator")

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [admin, user2]
    mock_session.execute = AsyncMock(return_value=result)

    client = _users_app(mock_session, admin)
    resp = client.get("/api/v1/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_users_operator_returns_403():
    operator = _make_user("operator")
    mock_session = AsyncMock()

    client = _users_app(mock_session, operator)
    resp = client.get("/api/v1/users")
    assert resp.status_code == 403


def test_create_user_admin_success():
    admin = _make_user("admin")
    mock_session = AsyncMock()
    no_result = MagicMock()
    no_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=no_result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    client = _users_app(mock_session, admin)
    resp = client.post(
        "/api/v1/users",
        json={"email": "new@example.com", "password": "Pass1234!", "role": "operator"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert "hashed_password" not in body


def test_create_user_duplicate_email_returns_409():
    admin = _make_user("admin")
    existing = _make_user("operator")
    existing.email = "dup@example.com"

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    mock_session.execute = AsyncMock(return_value=result)

    client = _users_app(mock_session, admin)
    resp = client.post(
        "/api/v1/users",
        json={"email": "dup@example.com", "password": "Pass1234!", "role": "operator"},
    )
    assert resp.status_code == 409


def test_get_user_by_id():
    admin = _make_user("admin")
    target = _make_user("operator")

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=target)

    client = _users_app(mock_session, admin)
    resp = client.get(f"/api/v1/users/{target.id}")
    assert resp.status_code == 200
    assert resp.json()["role"] == "operator"


def test_get_user_not_found_returns_404():
    admin = _make_user("admin")
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)

    client = _users_app(mock_session, admin)
    resp = client.get(f"/api/v1/users/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_update_user_changes_role():
    admin = _make_user("admin")
    target = _make_user("operator")

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=target)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    client = _users_app(mock_session, admin)
    resp = client.put(f"/api/v1/users/{target.id}", json={"role": "admin"})
    assert resp.status_code == 200
    assert target.role == "admin"


def test_delete_user_returns_204():
    admin = _make_user("admin")
    target = _make_user("operator")

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=target)
    mock_session.delete = AsyncMock()
    mock_session.commit = AsyncMock()

    client = _users_app(mock_session, admin)
    resp = client.delete(f"/api/v1/users/{target.id}")
    assert resp.status_code == 204


def test_delete_user_soft_deletes():
    """DELETE /users/{id} must set deleted_at."""
    admin = _make_user("admin")
    user = _make_user_row()
    user.deleted_at = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=user)
    mock_session.commit = AsyncMock()

    client = _users_app(mock_session, admin)
    resp = client.delete(f"/api/v1/users/{user.id}")
    assert resp.status_code == 204
    assert user.deleted_at is not None


def test_list_users_excludes_soft_deleted():
    """GET /users must exclude users where deleted_at is set."""
    admin = _make_user("admin")
    alive = _make_user_row()
    alive.deleted_at = None

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [alive]
    mock_session.execute = AsyncMock(return_value=result)

    client = _users_app(mock_session, admin)
    resp = client.get("/api/v1/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
