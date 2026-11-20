import asyncio
import os
import re
from logging.config import fileConfig

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import context
from siphon.orm import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_LOCK_TIMEOUT_RE = re.compile(r"^\d+(ms|s|min)$")


def _get_lock_timeout() -> str:
    raw = os.getenv("ALEMBIC_LOCK_TIMEOUT", "5s").strip()
    if not _LOCK_TIMEOUT_RE.match(raw):
        raise ValueError(f"Invalid ALEMBIC_LOCK_TIMEOUT value: {raw!r}")
    return raw


def run_migrations_offline() -> None:
    url = os.environ["DATABASE_URL"]
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            timeout = _get_lock_timeout()
            connection.execute(text(f"SET LOCAL lock_timeout = '{timeout}'"))
        context.run_migrations()


async def run_migrations_online() -> None:
    url = os.environ["DATABASE_URL"]
    connectable = create_async_engine(url, poolclass=NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
