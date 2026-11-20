# src/siphon/gdpr/router.py
import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.db import get_db
from siphon.orm import GdprEvent, Pipeline
from siphon.tasks import purge_s3_data_task

router = APIRouter(tags=["gdpr"])
logger = structlog.get_logger()

_LARGE_PURGE_THRESHOLD = 1000


def _require_admin(principal: Principal) -> None:
    if principal.type != "user" or principal.user.role != "admin":
        raise HTTPException(403, "Admin access required")


def _count_s3_files(
    base_path: str,
    partition_filter: str | None,
    dest_config: dict | None = None,
) -> int:
    """Count Parquet files matching the path + filter (S3 or GCS). Used to decide sync vs async purge."""
    import pyarrow.fs as pafs

    from siphon.tasks import _build_purge_fs

    try:
        fs, _ = _build_purge_fs(base_path, dest_config)
        bucket_path = base_path.removeprefix("s3a://").removeprefix("s3://").removeprefix("gs://")
        file_info = fs.get_file_info(pafs.FileSelector(bucket_path, recursive=True))
        files = [f for f in file_info if f.is_file and f.path.endswith(".parquet")]
        if partition_filter:
            files = [f for f in files if partition_filter in f.path]
        return len(files)
    except Exception:
        return 0


def _purge_s3_files(
    base_path: str,
    before_date,
    partition_filter: str | None,
    dest_config: dict | None = None,
) -> dict:
    """Synchronous purge — called directly for small volumes."""
    from siphon.tasks import _purge_s3_files as _task_purge

    return _task_purge(base_path, before_date, partition_filter, dest_config=dest_config)


class GdprEventResponse(BaseModel):
    id: str
    pipeline_id: str
    requested_by: str
    before_date: date | None
    partition_filter: str | None
    files_deleted: int | None
    bytes_deleted: int | None
    status: str
    requested_at: datetime
    completed_at: datetime | None


def _to_event_response(e: GdprEvent) -> GdprEventResponse:
    return GdprEventResponse(
        id=str(e.id),
        pipeline_id=str(e.pipeline_id),
        requested_by=str(e.requested_by),
        before_date=e.before_date,
        partition_filter=e.partition_filter,
        files_deleted=e.files_deleted,
        bytes_deleted=e.bytes_deleted,
        status=e.status,
        requested_at=e.requested_at,
        completed_at=e.completed_at,
    )


async def _load_dest_config(db: AsyncSession, pipeline: Pipeline) -> dict | None:
    """Load decrypted destination connection config for the pipeline, if configured."""
    dest_conn_id = getattr(pipeline, "dest_connection_id", None)
    if not isinstance(dest_conn_id, uuid.UUID):
        return None
    import json

    from siphon.crypto import decrypt
    from siphon.orm import Connection

    dest_conn = await db.get(Connection, dest_conn_id)
    if dest_conn is None or not getattr(dest_conn, "encrypted_config", None):
        return None
    try:
        cfg = json.loads(decrypt(dest_conn.encrypted_config))
        cfg["type"] = dest_conn.conn_type
        return cfg
    except Exception:
        return None


@router.delete("/api/v1/pipelines/{pipeline_id}/data")
async def purge_pipeline_data(
    pipeline_id: uuid.UUID,
    before: date | None = Query(None, description="Delete files from partitions before this date"),  # noqa: B008
    partition: str | None = Query(None, description="Delete files matching this partition value"),  # noqa: B008
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> Any:
    _require_admin(principal)

    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(404, "Pipeline not found")

    dest_config = await _load_dest_config(db, pipeline)
    base_path = pipeline.destination_path
    now = datetime.now(tz=UTC)
    event = GdprEvent(
        id=uuid.uuid4(),
        pipeline_id=pipeline_id,
        requested_by=principal.user.id,
        before_date=before,
        partition_filter=partition,
        status="in_progress",
        requested_at=now,
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)

    file_count = (
        _count_s3_files(base_path, partition, dest_config=dest_config)
        if dest_config is not None
        else _count_s3_files(base_path, partition)
    )

    if file_count >= _LARGE_PURGE_THRESHOLD:
        # Async path — dispatch Celery task and return 202
        apply_kwargs: dict[str, Any] = {"queue": "normal"}
        if dest_config is not None:
            apply_kwargs["kwargs"] = {"dest_config": dest_config}
        purge_s3_data_task.apply_async(
            args=[
                str(event.id),
                base_path,
                before.isoformat() if before else None,
                partition,
            ],
            **apply_kwargs,
        )
        logger.info(
            "Large purge dispatched",
            event_id=str(event.id),
            pipeline_id=str(pipeline_id),
            estimated_files=file_count,
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=202,
            content={
                "event_id": str(event.id),
                "status": "in_progress",
                "estimated_files": file_count,
            },
        )

    # Sync path — purge inline
    result = (
        _purge_s3_files(base_path, before, partition, dest_config=dest_config)
        if dest_config is not None
        else _purge_s3_files(base_path, before, partition)
    )
    event.status = "completed"
    event.files_deleted = result["files_deleted"]
    event.bytes_deleted = result["bytes_deleted"]
    event.completed_at = datetime.now(tz=UTC)
    await db.commit()

    logger.info(
        "Purge complete",
        event_id=str(event.id),
        files_deleted=result["files_deleted"],
        bytes_deleted=result["bytes_deleted"],
    )
    return {
        "event_id": str(event.id),
        "files_deleted": result["files_deleted"],
        "bytes_deleted": result["bytes_deleted"],
        "status": "completed",
    }


@router.get("/api/v1/gdpr/events", response_model=list[GdprEventResponse])
async def list_gdpr_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[GdprEventResponse]:
    _require_admin(principal)
    result = await db.execute(
        select(GdprEvent).order_by(desc(GdprEvent.requested_at)).limit(limit).offset(offset)
    )
    return [_to_event_response(e) for e in result.scalars().all()]


@router.get("/api/v1/gdpr/events/{event_id}", response_model=GdprEventResponse)
async def get_gdpr_event(
    event_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> GdprEventResponse:
    _require_admin(principal)
    event = await db.get(GdprEvent, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")
    return _to_event_response(event)
