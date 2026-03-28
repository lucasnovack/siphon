# src/siphon/auth/jwt_utils.py
import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from passlib.context import CryptContext

_JWT_SECRET: str = os.getenv("SIPHON_JWT_SECRET", "dev-secret-change-in-production")
_ALGORITHM = "HS256"
_DEFAULT_ACCESS_EXPIRE_MINUTES = 15
_REFRESH_TOKEN_EXPIRE_DAYS = 7

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def create_access_token(
    user_id: uuid.UUID,
    role: str,
    expires_minutes: int = _DEFAULT_ACCESS_EXPIRE_MINUTES,
) -> str:
    now = datetime.now(tz=UTC)
    expire = now + timedelta(minutes=expires_minutes)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": expire,
    }
    return pyjwt.encode(payload, _JWT_SECRET, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify an access token. Raises jwt.InvalidTokenError on failure."""
    return pyjwt.decode(token, _JWT_SECRET, algorithms=[_ALGORITHM])


def create_refresh_token() -> tuple[str, str, datetime]:
    """Generate a refresh token.

    Returns (token_string, sha256_hex_hash, expires_at).
    Only the hash is stored in the DB — the raw token goes in the httpOnly cookie.
    """
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(tz=UTC) + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS)
    return token, token_hash, expires_at


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)
