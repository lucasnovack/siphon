# src/siphon/preview/router.py
"""POST /api/v1/preview — run a SQL query with LIMIT 100 and return rows as JSON.

Passes through _validate_host() so SSRF rules are enforced.
Available to both admin and operator roles.

Execution is isolated from the FastAPI event loop:
- When REDIS_URL is configured (or SIPHON_PREVIEW_MODE=celery), queries are
  dispatched to the dedicated Celery 'preview' queue with a strict timeout.
- Otherwise (local dev / tests without Redis), queries run in a bounded
  ThreadPoolExecutor with asyncio.wait_for() timeout protection.
"""
import asyncio
import contextlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.router import limiter
from siphon.db import get_db

router = APIRouter(prefix="/api/v1/preview", tags=["preview"])
logger = structlog.get_logger()

_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_PREVIEW_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(os.getenv("SIPHON_PREVIEW_MAX_WORKERS", "2")),
    thread_name_prefix="siphon-preview",
)


class PreviewRequest(BaseModel):
    connection_id: str
    query: str


class PreviewResponse(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int


def _get_preview_timeout() -> float:
    return float(os.getenv("SIPHON_PREVIEW_TIMEOUT", "15"))


def _use_celery_preview() -> bool:
    mode = os.getenv("SIPHON_PREVIEW_MODE", "").strip().lower()
    if mode == "celery":
        return True
    if mode == "thread":
        return False
    return bool(os.getenv("REDIS_URL"))


async def _run_via_celery(connection_str: str, limited_query: str, timeout: float) -> dict:
    from celery.exceptions import TimeoutError as CeleryTimeoutError

    from siphon.tasks import run_preview_task

    async_result = await asyncio.to_thread(
        run_preview_task.apply_async,
        args=[connection_str, limited_query],
        queue="preview",
    )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(async_result.get, timeout=timeout),
            timeout=timeout + 2,
        )
    except (TimeoutError, CeleryTimeoutError) as exc:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(async_result.revoke, terminate=True)
        raise TimeoutError(f"Preview query timed out after {timeout:.0f}s") from exc


async def _run_via_thread(connection_str: str, limited_query: str, timeout: float) -> dict:
    from siphon.tasks import execute_preview_sql

    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(_PREVIEW_EXECUTOR, execute_preview_sql, connection_str, limited_query),
        timeout=timeout,
    )


@router.post("", response_model=PreviewResponse)
@limiter.limit("30/minute")
async def preview_query(
    request: Request,
    body: PreviewRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PreviewResponse:
    """Execute *query* against the connection, capped at 100 rows and bounded by timeout."""
    import uuid

    from siphon.crypto import decrypt
    from siphon.orm import Connection
    from siphon.plugins.sources.sql import _validate_host  # reuse SSRF guard

    try:
        conn_uuid = uuid.UUID(body.connection_id)
    except ValueError as err:
        raise HTTPException(400, "Invalid connection_id") from err

    conn = await db.get(Connection, conn_uuid)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    if conn.conn_type != "sql":
        raise HTTPException(400, f"Preview only supports SQL connections, got {conn.conn_type!r}")

    config = json.loads(decrypt(conn.encrypted_config))
    connection_str = config.get("connection", "")

    _validate_host(connection_str)

    limited_query = _apply_limit(body.query.strip(), 100)
    timeout = _get_preview_timeout()

    try:
        if _use_celery_preview():
            payload = await _run_via_celery(connection_str, limited_query, timeout)
        else:
            payload = await _run_via_thread(connection_str, limited_query, timeout)
        return PreviewResponse(**payload)
    except TimeoutError as exc:
        logger.warning("preview_query_timeout", timeout_s=timeout, connection_id=body.connection_id)
        raise HTTPException(504, f"Preview query timed out after {timeout:.0f}s") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("preview_query_failed", error=str(exc))
        raise HTTPException(400, "Query failed — check server logs") from exc


def _apply_limit(query: str, limit: int) -> str:
    """Wrap the query so it never returns more than *limit* rows."""
    # Remove trailing semicolon then wrap in a subquery with LIMIT
    q = query.rstrip(";").strip()
    return f"SELECT * FROM ({q}) _siphon_preview LIMIT {limit}"
