# tests/test_auth.py
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.jwt_utils import create_access_token
from siphon.db import get_db
from siphon.orm import User

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(role: str = "admin") -> User:
    """Create a mock User instance for testing."""
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = "test@example.com"
    user.hashed_password = "x"
    user.role = role
    user.is_active = True
    return user


# ── Tests for Principal dataclass ─────────────────────────────────────────────


def test_require_admin_raises_403_for_operator():
    user = _make_user("operator")
    principal = Principal(type="user", user=user)
    with pytest.raises(HTTPException) as exc_info:
        principal.require_admin()
    assert exc_info.value.status_code == 403


def test_require_admin_passes_for_admin():
    user = _make_user("admin")
    principal = Principal(type="user", user=user)
    principal.require_admin()  # must not raise


def test_require_admin_raises_403_for_api_key_principal():
    principal = Principal(type="api_key")
    with pytest.raises(HTTPException) as exc_info:
        principal.require_admin()
    assert exc_info.value.status_code == 403


def test_principal_role_returns_user_role():
    user = _make_user("operator")
    principal = Principal(type="user", user=user)
    assert principal.role == "operator"


def test_principal_role_returns_none_for_api_key():
    principal = Principal(type="api_key")
    assert principal.role is None


# ── Tests for get_current_principal dependency ────────────────────────────────


def _make_app_with_dep(mock_session: AsyncMock) -> FastAPI:
    """Build a minimal app with get_current_principal on a /protected route."""
    app = FastAPI()

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_db

    @app.get("/protected")
    async def protected(principal: Principal = Depends(get_current_principal)):  # noqa: B008
        return {"type": principal.type, "role": principal.role}

    return app


def test_jwt_grants_access():
    user = _make_user("operator")
    token = create_access_token(user.id, user.role)

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    mock_session.execute = AsyncMock(return_value=mock_result)

    app = _make_app_with_dep(mock_session)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "user"
    assert resp.json()["role"] == "operator"


def test_no_credentials_returns_401():
    mock_session = AsyncMock()
    app = _make_app_with_dep(mock_session)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/protected")
    assert resp.status_code == 401


def test_inactive_user_returns_401():
    user = _make_user("admin")
    user.is_active = False
    token = create_access_token(user.id, user.role)

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user
    mock_session.execute = AsyncMock(return_value=mock_result)

    app = _make_app_with_dep(mock_session)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_api_key_grants_access():
    """HTTP-level test: SIPHON_API_KEY bearer token hits the api_key branch."""
    import importlib
    import os
    from unittest.mock import patch

    # Must reload deps to pick up the patched env var
    with patch.dict(os.environ, {"SIPHON_API_KEY": "secret123"}):
        import siphon.auth.deps as deps_module
        importlib.reload(deps_module)
        from siphon.auth.deps import get_current_principal as reloaded_dep

        app2 = FastAPI()
        mock_session = AsyncMock()

        async def override_db():
            yield mock_session

        app2.dependency_overrides[get_db] = override_db

        @app2.get("/protected")
        async def protected(principal: Principal = Depends(reloaded_dep)):  # noqa: B008
            return {"type": principal.type}

        client = TestClient(app2, raise_server_exceptions=False)
        resp = client.get("/protected", headers={"Authorization": "Bearer secret123"})
        assert resp.status_code == 200
        assert resp.json()["type"] == "api_key"
