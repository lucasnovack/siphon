# src/siphon/db.py
import os
from collections.abc import AsyncGenerator

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = structlog.get_logger()

_DATABASE_URL: str | None = os.getenv("DATABASE_URL")

_DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
_DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
_DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
_DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

if _DATABASE_URL:
    _engine = create_async_engine(
        _DATABASE_URL,
        pool_pre_ping=True,
        pool_size=_DB_POOL_SIZE,
        max_overflow=_DB_MAX_OVERFLOW,
        pool_timeout=_DB_POOL_TIMEOUT,
        pool_recycle=_DB_POOL_RECYCLE,
    )
    _session_factory: async_sessionmaker | None = async_sessionmaker(
        _engine, expire_on_commit=False
    )
else:
    _engine = None
    _session_factory = None
    logger.warning("DATABASE_URL not set — database features are disabled")


def get_session_factory() -> async_sessionmaker | None:
    """Return the session factory, or None if DATABASE_URL is not configured."""
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an AsyncSession, or raise 503 if DB not configured."""
    if _session_factory is None:
        from fastapi import HTTPException
        raise HTTPException(503, "Database not configured. Set DATABASE_URL.")
    async with _session_factory() as session:
        yield session
