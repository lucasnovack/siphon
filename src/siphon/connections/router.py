# src/siphon/connections/router.py
import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.router import limiter
from siphon.crypto import decrypt, encrypt
from siphon.db import get_db
from siphon.orm import Connection
from siphon.plugins.sources.sql import _validate_host

router = APIRouter(prefix="/api/v1/connections", tags=["connections"])

ConnType = Literal["sql", "sftp", "s3_parquet"]

# ── Pydantic schemas ──────────────────────────────────────────────────────────


class ConnectionCreate(BaseModel):
    name: str
    type: ConnType
    config: dict[str, Any]
    max_concurrent_jobs: int = Field(default=2, ge=1)


class ConnectionUpdate(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    max_concurrent_jobs: int | None = Field(default=None, ge=1)


class ConnectionTestRequest(BaseModel):
    type: ConnType
    config: dict[str, Any]


class ConnectionResponse(BaseModel):
    id: str
    name: str
    type: str
    key_version: int
    max_concurrent_jobs: int
    created_at: datetime
    updated_at: datetime
    # config intentionally absent — never returned to callers


def _to_response(conn: Connection) -> ConnectionResponse:
    return ConnectionResponse(
        id=str(conn.id),
        name=conn.name,
        type=conn.conn_type,
        key_version=conn.key_version,
        max_concurrent_jobs=conn.max_concurrent_jobs,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )


async def _after_create(db: AsyncSession, conn: Connection) -> Connection:
    await db.commit()
    await db.refresh(conn)
    return conn


# ── CRUD ──────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ConnectionResponse])
async def list_connections(
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[ConnectionResponse]:
    result = await db.execute(
        select(Connection)
        .where(Connection.deleted_at.is_(None))
        .order_by(Connection.created_at)
    )
    return [_to_response(c) for c in result.scalars().all()]


@router.post("", response_model=ConnectionResponse, status_code=201)
async def create_connection(
    body: ConnectionCreate,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> ConnectionResponse:
    principal.require_admin()
    result = await db.execute(select(Connection).where(Connection.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(409, f"Connection '{body.name}' already exists")
    now = datetime.now(tz=UTC)
    conn = Connection(
        name=body.name,
        conn_type=body.type,
        encrypted_config=encrypt(json.dumps(body.config)),
        key_version=1,
        max_concurrent_jobs=body.max_concurrent_jobs,
        created_at=now,
        updated_at=now,
    )
    db.add(conn)
    conn = await _after_create(db, conn)
    return _to_response(conn)


@router.get("/types/list")
async def get_connection_types(
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> list[dict]:
    return _CONNECTION_TYPES


@router.post("/test")
@limiter.limit("20/minute")
async def test_connection_unsaved(
    request: Request,
    body: ConnectionTestRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> dict:
    import time
    loop = asyncio.get_event_loop()
    t0 = time.monotonic()
    try:
        await loop.run_in_executor(None, _test_connection, body.type, body.config)
    except Exception as exc:
        raise HTTPException(400, f"Connection test failed: {exc}") from exc
    return {"ok": True, "latency_ms": round((time.monotonic() - t0) * 1000)}


@router.get("/{conn_id}", response_model=ConnectionResponse)
async def get_connection(
    conn_id: uuid.UUID,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> ConnectionResponse:
    conn = await db.get(Connection, conn_id)
    if conn is None or conn.deleted_at is not None:
        raise HTTPException(404, "Connection not found")
    return _to_response(conn)


@router.put("/{conn_id}", response_model=ConnectionResponse)
async def update_connection(
    conn_id: uuid.UUID,
    body: ConnectionUpdate,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> ConnectionResponse:
    principal.require_admin()
    conn = await db.get(Connection, conn_id)
    if conn is None or conn.deleted_at is not None:
        raise HTTPException(404, "Connection not found")
    if body.name is not None:
        if body.name != conn.name:
            existing = await db.execute(select(Connection).where(Connection.name == body.name))
            if existing.scalar_one_or_none():
                raise HTTPException(409, f"Connection '{body.name}' already exists")
        conn.name = body.name
    if body.config is not None:
        conn.encrypted_config = encrypt(json.dumps(body.config))
        conn.key_version += 1
    if body.max_concurrent_jobs is not None:
        conn.max_concurrent_jobs = body.max_concurrent_jobs
    conn.updated_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(conn)
    return _to_response(conn)


@router.delete("/{conn_id}", status_code=204)
async def delete_connection(
    conn_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> None:
    principal.require_admin()
    conn = await db.get(Connection, conn_id)
    if conn is None or conn.deleted_at is not None:
        raise HTTPException(404, "Connection not found")
    conn.deleted_at = datetime.now(tz=UTC)
    await db.commit()


# ── Test connection ───────────────────────────────────────────────────────────


def _test_connection(conn_type: str, config: dict) -> None:
    """Synchronous connectivity check — run in a thread executor."""
    if conn_type == "sql":
        import connectorx as cx
        _validate_host(config["connection"])
        cx.read_sql(config["connection"], "SELECT 1", return_type="arrow")

    elif conn_type == "sftp":
        import paramiko
        _validate_host(f"dummy://{config['host']}")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        ssh.connect(
            config["host"],
            port=config.get("port", 22),
            username=config["username"],
            password=config["password"],
            timeout=10,
        )
        ssh.close()

    elif conn_type == "s3_parquet":
        import os

        import pyarrow.fs as pafs
        _validate_host(f"dummy://{config['endpoint']}")
        fs = pafs.S3FileSystem(
            endpoint_override=config["endpoint"],
            access_key=config["access_key"],
            secret_key=config["secret_key"],
            scheme=os.getenv("SIPHON_S3_SCHEME", "https"),
        )
        fs.get_file_info(pafs.FileSelector("/", recursive=False))

    else:
        raise ValueError(f"Unknown connection type: {conn_type!r}")


@router.post("/{conn_id}/test")
async def test_connection(
    conn_id: uuid.UUID,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    conn = await db.get(Connection, conn_id)
    if conn is None or conn.deleted_at is not None:
        raise HTTPException(404, "Connection not found")
    config = json.loads(decrypt(conn.encrypted_config))
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _test_connection, conn.conn_type, config)
    except Exception as exc:
        raise HTTPException(400, f"Connection test failed: {exc}") from exc
    return {"status": "ok"}


# ── Connection types metadata ─────────────────────────────────────────────────

_CONNECTION_TYPES = [
    {
        "type": "sql",
        "label": "SQL Database",
        "fields": [
            {
                "name": "connection",
                "type": "string",
                "label": "Connection URL",
                "secret": True,
                "placeholder": "mysql://user:pass@host:3306/db",
            },
        ],
    },
    {
        "type": "sftp",
        "label": "SFTP Server",
        "fields": [
            {"name": "host", "type": "string", "label": "Host"},
            {"name": "port", "type": "integer", "label": "Port", "default": 22},
            {"name": "username", "type": "string", "label": "Username"},
            {"name": "password", "type": "string", "label": "Password", "secret": True},
        ],
    },
    {
        "type": "s3_parquet",
        "label": "S3 / MinIO",
        "fields": [
            {
                "name": "endpoint",
                "type": "string",
                "label": "Endpoint",
                "placeholder": "minio.internal:9000",
            },
            {"name": "access_key", "type": "string", "label": "Access Key"},
            {"name": "secret_key", "type": "string", "label": "Secret Key", "secret": True},
        ],
    },
]
