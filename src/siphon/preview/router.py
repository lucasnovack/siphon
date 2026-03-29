# src/siphon/preview/router.py
"""POST /api/v1/preview — run a SQL query with LIMIT 100 and return rows as JSON.

Passes through _validate_host() so SSRF rules are enforced.
Available to both admin and operator roles.
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.db import get_db

router = APIRouter(prefix="/api/v1/preview", tags=["preview"])

_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


class PreviewRequest(BaseModel):
    connection_id: str
    query: str


@router.post("")
async def preview_query(
    body: PreviewRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict]:
    """Execute *query* against the connection, capped at 100 rows."""
    from siphon.crypto import decrypt
    from siphon.orm import Connection
    from siphon.plugins.sources.sql import _validate_host  # reuse SSRF guard

    import uuid

    try:
        conn_uuid = uuid.UUID(body.connection_id)
    except ValueError:
        raise HTTPException(400, "Invalid connection_id")

    conn = await db.get(Connection, conn_uuid)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    if conn.conn_type != "sql":
        raise HTTPException(400, f"Preview only supports SQL connections, got {conn.conn_type!r}")

    config = json.loads(decrypt(conn.encrypted_config))
    connection_str = config.get("connection", "")

    # SSRF guard — reuse the same validation as the SQL source
    _validate_host(connection_str)

    # Cap the query at LIMIT 100 — strip existing LIMIT then append ours
    limited_query = _apply_limit(body.query.strip(), 100)

    try:
        import connectorx as cx
        table = cx.read_sql(connection_str, limited_query, return_type="arrow")
        return table.to_pylist()
    except Exception as exc:
        raise HTTPException(400, f"Query failed: {exc}") from exc


def _apply_limit(query: str, limit: int) -> str:
    """Wrap the query so it never returns more than *limit* rows."""
    # Remove trailing semicolon then wrap in a subquery with LIMIT
    q = query.rstrip(";").strip()
    return f"SELECT * FROM ({q}) _siphon_preview LIMIT {limit}"
