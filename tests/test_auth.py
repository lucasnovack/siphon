# tests/test_auth.py
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.jwt_utils import create_access_token
from siphon.auth.router import router as auth_router
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


# ── Auth router tests ─────────────────────────────────────────────────────────


def _auth_app(mock_session: AsyncMock) -> TestClient:
    """Build a TestClient with the auth router and overridden DB."""
    app = FastAPI()
    app.include_router(auth_router)

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app, raise_server_exceptions=False)


def test_login_success_returns_access_token():
    from siphon.auth.jwt_utils import hash_password
    user = _make_user("admin")
    user.hashed_password = hash_password("pass123")

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    mock_session.execute = AsyncMock(return_value=result)
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    client = _auth_app(mock_session)
    resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": "pass123"})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert "refresh_token" in resp.cookies


def test_login_wrong_password_returns_401():
    from siphon.auth.jwt_utils import hash_password
    user = _make_user("admin")
    user.hashed_password = hash_password("correct")

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    mock_session.execute = AsyncMock(return_value=result)

    client = _auth_app(mock_session)
    resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_email_returns_401():
    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=result)

    client = _auth_app(mock_session)
    resp = client.post("/api/v1/auth/login", json={"email": "ghost@x.com", "password": "x"})
    assert resp.status_code == 401


def test_login_inactive_user_returns_401():
    from siphon.auth.jwt_utils import hash_password
    user = _make_user("admin")
    user.hashed_password = hash_password("pass123")
    user.is_active = False

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    mock_session.execute = AsyncMock(return_value=result)

    client = _auth_app(mock_session)
    resp = client.post("/api/v1/auth/login", json={"email": user.email, "password": "pass123"})
    assert resp.status_code == 401


def test_refresh_rotates_token():
    from datetime import UTC, datetime

    from siphon.auth.jwt_utils import create_refresh_token
    from siphon.orm import RefreshToken

    user = _make_user("admin")
    token_str, token_hash, expires_at = create_refresh_token()

    old_refresh = MagicMock(spec=RefreshToken)
    old_refresh.id = uuid.uuid4()
    old_refresh.user_id = user.id
    old_refresh.token_hash = token_hash
    old_refresh.issued_at = datetime.now(tz=UTC)
    old_refresh.expires_at = expires_at
    old_refresh.revoked_at = None
    old_refresh.replaced_by = None

    mock_session = AsyncMock()
    refresh_result = MagicMock()
    refresh_result.scalar_one_or_none.return_value = old_refresh
    mock_session.execute = AsyncMock(return_value=refresh_result)
    mock_session.get = AsyncMock(return_value=user)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()

    client = _auth_app(mock_session)
    client.cookies.set("refresh_token", token_str, path="/api/v1/auth")
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "refresh_token" in resp.cookies


def test_refresh_revoked_token_revokes_all_sessions():
    from datetime import UTC, datetime, timedelta

    from siphon.auth.jwt_utils import create_refresh_token
    from siphon.orm import RefreshToken

    user = _make_user("admin")
    token_str, token_hash, _ = create_refresh_token()

    revoked_refresh = MagicMock(spec=RefreshToken)
    revoked_refresh.id = uuid.uuid4()
    revoked_refresh.user_id = user.id
    revoked_refresh.token_hash = token_hash
    revoked_refresh.issued_at = datetime.now(tz=UTC)
    revoked_refresh.expires_at = datetime.now(tz=UTC) + timedelta(days=7)
    revoked_refresh.revoked_at = datetime.now(tz=UTC)  # already revoked!
    revoked_refresh.replaced_by = None

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = revoked_refresh
    mock_session.execute = AsyncMock(return_value=result)
    mock_session.commit = AsyncMock()

    client = _auth_app(mock_session)
    client.cookies.set("refresh_token", token_str, path="/api/v1/auth")
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401
    # Should have executed UPDATE to revoke all sessions
    assert mock_session.execute.call_count >= 2


def test_logout_revokes_token():
    from siphon.auth.jwt_utils import create_refresh_token
    from siphon.orm import RefreshToken

    user = _make_user("admin")
    token_str, token_hash, _ = create_refresh_token()

    refresh = MagicMock(spec=RefreshToken)
    refresh.id = uuid.uuid4()
    refresh.user_id = user.id
    refresh.token_hash = token_hash
    refresh.revoked_at = None

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = refresh
    mock_session.execute = AsyncMock(return_value=result)
    mock_session.commit = AsyncMock()

    client = _auth_app(mock_session)
    # Pass token in body since cookie path doesn't cover /logout
    resp = client.post("/api/v1/auth/logout", json={"refresh_token": token_str})
    assert resp.status_code == 200
    assert refresh.revoked_at is not None


def test_me_returns_user_info():
    user = _make_user("operator")
    token = create_access_token(user.id, user.role)

    mock_session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    mock_session.execute = AsyncMock(return_value=result)

    app = FastAPI()
    app.include_router(auth_router)

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == user.email
    assert body["role"] == "operator"
