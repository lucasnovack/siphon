# src/siphon/auth/deps.py
import os
import uuid
from typing import Any, Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.jwt_utils import decode_access_token
from siphon.db import get_db
from siphon.orm import User

_API_KEY: str | None = os.getenv("SIPHON_API_KEY")


class Principal(BaseModel):
    type: Literal["api_key", "user"]
    user: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def role(self) -> str | None:
        return self.user.role if self.user else None

    def require_admin(self) -> None:
        if self.user is None or self.user.role != "admin":
            raise HTTPException(status_code=403, detail="Admin required")


async def get_current_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Principal:
    """Dual-auth: SIPHON_API_KEY (Airflow backward-compat) OR JWT access token."""
    auth = request.headers.get("Authorization", "")

    # 1. Legacy API key — backward-compat with Airflow
    if _API_KEY and auth == f"Bearer {_API_KEY}":
        return Principal(type="api_key")

    # 2. JWT access token
    if auth.startswith("Bearer "):
        token = auth[7:]
        try:
            import jwt as pyjwt

            payload = decode_access_token(token)
            if payload.get("type") != "access":
                raise ValueError("not an access token")
            result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
            user = result.scalar_one_or_none()
            if user and user.is_active:
                return Principal(type="user", user=user)
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Unauthorized")
