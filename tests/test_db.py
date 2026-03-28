# tests/test_db.py
import os
from unittest.mock import patch

import pytest


def test_get_session_factory_returns_none_when_no_url():
    """Without DATABASE_URL, get_session_factory() returns None."""
    with patch.dict(os.environ, {}, clear=True):
        import importlib
        import siphon.db as db_module
        importlib.reload(db_module)
        assert db_module.get_session_factory() is None


def test_get_session_factory_returns_factory_when_url_set():
    """With DATABASE_URL, get_session_factory() returns an async_sessionmaker."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}):
        import importlib
        import siphon.db as db_module
        importlib.reload(db_module)
        factory = db_module.get_session_factory()
        assert factory is not None
        assert isinstance(factory, async_sessionmaker)


@pytest.mark.asyncio
async def test_get_db_raises_503_when_no_url():
    """get_db() raises 503 when DATABASE_URL is not set."""
    from fastapi import HTTPException
    with patch.dict(os.environ, {}, clear=True):
        import importlib
        import siphon.db as db_module
        importlib.reload(db_module)
        with pytest.raises(HTTPException) as exc_info:
            async for _ in db_module.get_db():
                pass
        assert exc_info.value.status_code == 503
