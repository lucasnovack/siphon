# src/siphon/preview/router.py
"""POST /api/v1/preview — run a SQL query with LIMIT 100 and return rows as JSON.

Passes through _validate_host() so SSRF rules are enforced.
Available to both admin and operator roles.
"""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.router import limiter
from siphon.db import get_db

router = APIRouter(prefix="/api/v1/preview", tags=["preview"])

_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


class PreviewRequest(BaseModel):
    connection_id: str
    query: str


class PreviewResponse(BaseModel):
    columns: list[str]
    rows: list[list]
    row_count: int


@router.post("", response_model=PreviewResponse)
@limiter.limit("30/minute")
async def preview_query(
    request: Request,
    body: PreviewRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PreviewResponse:
    """Execute *query* against the connection, capped at 100 rows."""
    import uuid

    from siphon.crypto import decrypt
    from siphon.orm import Connection
    from siphon.plugins.sources.sql import _validate_host  # reuse SSRF guard

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

    _validate_host(connection_str)

    limited_query = _apply_limit(body.query.strip(), 100)

    try:
        import connectorx as cx
        table = cx.read_sql(connection_str, limited_query, return_type="arrow")
        d = table.to_pydict()
        columns = list(d.keys())
        rows = [[d[col][i] for col in columns] for i in range(table.num_rows)]
        return PreviewResponse(columns=columns, rows=rows, row_count=table.num_rows)
    except Exception as exc:
        raise HTTPException(400, f"Query failed: {exc}") from exc


def _apply_limit(query: str, limit: int) -> str:
    """Wrap the query so it never returns more than *limit* rows."""
    # Remove trailing semicolon then wrap in a subquery with LIMIT
    q = query.rstrip(";").strip()
    return f"SELECT * FROM ({q}) _siphon_preview LIMIT {limit}"
