# src/siphon/auth/router.py
import hashlib
import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.jwt_utils import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from siphon.db import get_db
from siphon.orm import RefreshToken, User

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

limiter = Limiter(key_func=get_remote_address)

_REFRESH_MAX_AGE = 7 * 24 * 3600  # 7 days in seconds


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    is_active: bool


@router.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    credentials: LoginRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> JSONResponse:
    """Authenticate with email/password. Returns access token + sets httpOnly refresh cookie."""
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token(user.id, user.role)
    token_str, token_hash, expires_at = create_refresh_token()

    now = datetime.now(tz=UTC)
    refresh = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        issued_at=now,
        expires_at=expires_at,
    )
    db.add(refresh)
    await db.commit()

    response = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    response.set_cookie(
        key="refresh_token",
        value=token_str,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/v1/auth",
        max_age=_REFRESH_MAX_AGE,
    )
    return response


@router.post("/refresh")
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> JSONResponse:
    """Rotate refresh token. Reuse of a revoked token revokes all user sessions."""
    token_str = request.cookies.get("refresh_token")
    if not token_str:
        raise HTTPException(status_code=401, detail="No refresh token")

    token_hash = hashlib.sha256(token_str.encode()).hexdigest()
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    refresh = result.scalar_one_or_none()

    if not refresh:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    now = datetime.now(tz=UTC)

    # Reuse detection: revoked token presented → nuke all sessions
    if refresh.revoked_at is not None:
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == refresh.user_id)
            .where(RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await db.commit()
        raise HTTPException(
            status_code=401,
            detail="Refresh token reuse detected. All sessions have been revoked.",
        )

    if refresh.expires_at < now:
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Rotate: issue new token, revoke old
    new_token_str, new_token_hash, new_expires_at = create_refresh_token()
    new_refresh = RefreshToken(
        user_id=refresh.user_id,
        token_hash=new_token_hash,
        issued_at=now,
        expires_at=new_expires_at,
    )
    db.add(new_refresh)
    await db.flush()

    refresh.revoked_at = now
    refresh.replaced_by = new_refresh.id

    user = await db.get(User, refresh.user_id)
    await db.commit()

    access_token = create_access_token(user.id, user.role)
    response = JSONResponse({"access_token": access_token, "token_type": "bearer"})
    response.set_cookie(
        key="refresh_token",
        value=new_token_str,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/v1/auth",
        max_age=_REFRESH_MAX_AGE,
    )
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Revoke the current refresh token cookie."""
    token_str = request.cookies.get("refresh_token")
    if token_str:
        token_hash = hashlib.sha256(token_str.encode()).hexdigest()
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        refresh = result.scalar_one_or_none()
        if refresh and refresh.revoked_at is None:
            refresh.revoked_at = datetime.now(tz=UTC)
            await db.commit()
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(principal: Principal = Depends(get_current_principal)) -> UserResponse:  # noqa: B008
    """Return current authenticated user info. Requires JWT (not API key)."""
    if principal.user is None:
        raise HTTPException(status_code=401, detail="JWT required")
    u = principal.user
    return UserResponse(id=str(u.id), email=u.email, role=u.role, is_active=u.is_active)
