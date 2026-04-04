# src/siphon/pipelines/router.py
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.router import limiter
from siphon.db import get_db
from siphon.orm import Connection, JobRun, Pipeline, Schedule

router = APIRouter(prefix="/api/v1/pipelines", tags=["pipelines"])
logger = logging.getLogger(__name__)

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _validate_incremental_key(v: str | None) -> str | None:
    if v is not None and not _VALID_IDENTIFIER.match(v):
        raise ValueError(
            "incremental_key must be a plain SQL identifier "
            "(letters, digits, underscores, dots only)"
        )
    return v


# ── Schemas ───────────────────────────────────────────────────────────────────


class PipelineCreate(BaseModel):
    name: str
    source_connection_id: uuid.UUID
    dest_connection_id: uuid.UUID | None = None
    query: str
    destination_path: str
    extraction_mode: Literal["full_refresh", "incremental"] = "full_refresh"
    incremental_key: str | None = None
    min_rows_expected: int | None = None
    max_rows_drop_pct: int | None = None
    pii_columns: dict[str, Literal["sha256", "redact"]] | None = None

    @field_validator("incremental_key")
    @classmethod
    def validate_incremental_key(cls, v: str | None) -> str | None:
        return _validate_incremental_key(v)


class PipelineUpdate(BaseModel):
    name: str | None = None
    query: str | None = None
    destination_path: str | None = None
    extraction_mode: Literal["full_refresh", "incremental"] | None = None
    incremental_key: str | None = None
    min_rows_expected: int | None = None
    max_rows_drop_pct: int | None = None
    pii_columns: dict[str, Literal["sha256", "redact"]] | None = None

    @field_validator("incremental_key")
    @classmethod
    def validate_incremental_key(cls, v: str | None) -> str | None:
        return _validate_incremental_key(v)


class ScheduleUpsert(BaseModel):
    cron: str
    is_active: bool = True


class TriggerRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None

    @field_validator("date_from", "date_to")
    @classmethod
    def must_be_iso8601(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            from datetime import datetime as _dt
            _dt.fromisoformat(v)
        except ValueError:
            raise ValueError("date_from/date_to must be a valid ISO-8601 datetime string")
        return v


class ScheduleResponse(BaseModel):
    cron_expr: str
    is_active: bool
    next_run_at: datetime | None


class PipelineResponse(BaseModel):
    id: str
    name: str
    source_connection_id: str
    dest_connection_id: str | None
    query: str
    destination_path: str
    extraction_mode: str
    incremental_key: str | None
    last_watermark: str | None
    last_schema_hash: str | None
    min_rows_expected: int | None
    max_rows_drop_pct: int | None
    pii_columns: dict[str, str] | None
    is_active: bool
    schedule: ScheduleResponse | None
    created_at: datetime
    updated_at: datetime


def _to_response(p: Pipeline, schedule: Schedule | None = None) -> PipelineResponse:
    sched = None
    if schedule:
        sched = ScheduleResponse(
            cron_expr=schedule.cron,
            is_active=schedule.is_active,
            next_run_at=schedule.next_run_at,
        )
    return PipelineResponse(
        id=str(p.id),
        name=p.name,
        source_connection_id=str(p.source_connection_id),
        dest_connection_id=str(p.dest_connection_id) if p.dest_connection_id else None,
        query=p.query,
        destination_path=p.destination_path,
        extraction_mode=p.extraction_mode,
        incremental_key=p.incremental_key,
        last_watermark=p.last_watermark,
        last_schema_hash=p.last_schema_hash,
        min_rows_expected=p.min_rows_expected,
        max_rows_drop_pct=p.max_rows_drop_pct,
        pii_columns=p.pii_columns,
        is_active=True,
        schedule=sched,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


async def _after_create(db: AsyncSession, p: Pipeline) -> Pipeline:
    await db.commit()
    await db.refresh(p)
    return p


async def _after_schedule_upsert(db: AsyncSession, s: Schedule) -> Schedule:
    await db.commit()
    await db.refresh(s)
    return s


def _get_queue():
    from siphon.main import queue
    return queue


# ── CRUD ──────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[PipelineResponse]:
    result = await db.execute(select(Pipeline).order_by(Pipeline.created_at))
    return [_to_response(p) for p in result.scalars().all()]


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PipelineResponse:
    principal.require_admin()
    result = await db.execute(select(Pipeline).where(Pipeline.name == body.name))
    if result.scalar_one_or_none():
        raise HTTPException(409, f"Pipeline '{body.name}' already exists")
    now = datetime.now(tz=UTC)
    pipeline = Pipeline(
        name=body.name,
        source_connection_id=body.source_connection_id,
        dest_connection_id=body.dest_connection_id,
        query=body.query,
        destination_path=body.destination_path,
        extraction_mode=body.extraction_mode,
        incremental_key=body.incremental_key,
        min_rows_expected=body.min_rows_expected,
        max_rows_drop_pct=body.max_rows_drop_pct,
        pii_columns=body.pii_columns,
        created_at=now,
        updated_at=now,
    )
    db.add(pipeline)
    pipeline = await _after_create(db, pipeline)
    return _to_response(pipeline)


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: uuid.UUID,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PipelineResponse:
    p = await db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return _to_response(p)


@router.put("/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineUpdate,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PipelineResponse:
    principal.require_admin()
    p = await db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    for field in (
        "name", "query", "destination_path", "extraction_mode",
        "incremental_key", "min_rows_expected", "max_rows_drop_pct",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(p, field, val)
    # Special handling for pii_columns: None means "not provided" but {} means "clear all rules"
    if body.pii_columns is not None:
        p.pii_columns = body.pii_columns
    p.updated_at = datetime.now(tz=UTC)
    await db.commit()
    await db.refresh(p)
    return _to_response(p)


@router.delete("/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> None:
    principal.require_admin()
    p = await db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    from sqlalchemy import update as sa_update
    # Null out pipeline_id on job_runs first and flush so the FK is cleared before DELETE
    await db.execute(
        sa_update(JobRun).where(JobRun.pipeline_id == pipeline_id).values(pipeline_id=None)
    )
    await db.flush()
    # Remove schedule (FK: schedules.pipeline_id → pipelines.id, no cascade)
    sched_result = await db.execute(select(Schedule).where(Schedule.pipeline_id == pipeline_id))
    schedule = sched_result.scalar_one_or_none()
    if schedule:
        await db.delete(schedule)
        await db.flush()
    await db.delete(p)
    await db.commit()


# ── Schedule upsert / delete ──────────────────────────────────────────────────


@router.put("/{pipeline_id}/schedule")
async def upsert_schedule(
    pipeline_id: uuid.UUID,
    body: ScheduleUpsert,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    principal.require_admin()
    p = await db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")
    result = await db.execute(select(Schedule).where(Schedule.pipeline_id == pipeline_id))
    schedule = result.scalar_one_or_none()
    now = datetime.now(tz=UTC)
    if schedule:
        schedule.cron = body.cron
        schedule.is_active = body.is_active
        schedule.updated_at = now
    else:
        schedule = Schedule(
            pipeline_id=pipeline_id,
            cron=body.cron,
            is_active=body.is_active,
            created_at=now,
            updated_at=now,
        )
        db.add(schedule)
    schedule = await _after_schedule_upsert(db, schedule)
    try:
        from siphon.scheduler import sync_schedule
        await sync_schedule(pipeline_id, body.cron, body.is_active)
    except Exception:
        logger.warning("Scheduler sync failed for pipeline %s — schedule persisted but may not fire", pipeline_id, exc_info=True)
    return {"pipeline_id": str(pipeline_id), "cron": schedule.cron, "is_active": schedule.is_active}


@router.delete("/{pipeline_id}/schedule", status_code=204)
async def delete_schedule(
    pipeline_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> None:
    principal.require_admin()
    result = await db.execute(select(Schedule).where(Schedule.pipeline_id == pipeline_id))
    schedule = result.scalar_one_or_none()
    if schedule:
        await db.delete(schedule)
        await db.commit()
    try:
        from siphon.scheduler import remove_schedule
        await remove_schedule(pipeline_id)
    except Exception:
        logger.warning("Scheduler removal failed for pipeline %s", pipeline_id, exc_info=True)


# ── Trigger ───────────────────────────────────────────────────────────────────


@router.post("/{pipeline_id}/trigger", status_code=202)
@limiter.limit("60/minute")
async def trigger_pipeline(
    request: Request,
    pipeline_id: uuid.UUID,
    body: TriggerRequest = TriggerRequest(),
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    from siphon.crypto import decrypt
    from siphon.models import ExtractRequest, Job
    from siphon.plugins.destinations import get as get_destination
    from siphon.plugins.sources import get as get_source

    p = await db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "Pipeline not found")

    if bool(body.date_from) != bool(body.date_to):
        raise HTTPException(400, "date_from and date_to must both be provided for a backfill run")

    src_conn = await db.get(Connection, p.source_connection_id)
    if not src_conn:
        raise HTTPException(400, "Source connection not found")

    if p.dest_connection_id is None:
        raise HTTPException(400, "Pipeline has no destination connection configured")
    dest_conn = await db.get(Connection, p.dest_connection_id)
    if not dest_conn:
        raise HTTPException(400, "Destination connection not found")

    src_config = json.loads(decrypt(src_conn.encrypted_config))
    dest_config = json.loads(decrypt(dest_conn.encrypted_config))

    query = p.query
    if body.date_from and body.date_to:
        # Backfill: inject two-sided window; watermark injection skipped
        if not p.incremental_key:
            raise HTTPException(400, "Backfill requires incremental_key to be set on the pipeline")
        from siphon.pipelines.watermark import inject_backfill_window
        dialect = src_config.get("connection", "").split("://")[0]
        query = inject_backfill_window(
            query, p.incremental_key, body.date_from, body.date_to, dialect
        )
    elif p.extraction_mode == "incremental" and p.incremental_key and p.last_watermark:
        from siphon.pipelines.watermark import inject_watermark
        dialect = src_config.get("connection", "").split("://")[0]
        query = inject_watermark(query, p.incremental_key, p.last_watermark, dialect)

    if src_conn.conn_type != "sql":
        raise HTTPException(400, f"Unsupported source connection type: {src_conn.conn_type!r}")
    if dest_conn.conn_type != "s3_parquet":
        raise HTTPException(400, f"Unsupported destination connection type: {dest_conn.conn_type!r}")

    from datetime import date
    dest_path = p.destination_path.replace("{date}", str(date.today()))

    source_payload = {"type": "sql", "connection": src_config["connection"], "query": query}
    dest_payload = {
        "type": "s3_parquet",
        "path": dest_path,
        "endpoint": dest_config["endpoint"],
        "access_key": dest_config["access_key"],
        "secret_key": dest_config["secret_key"],
        "extraction_mode": p.extraction_mode,
    }

    try:
        req = ExtractRequest(**{"source": source_payload, "destination": dest_payload})
    except Exception as exc:
        raise HTTPException(400, f"Invalid pipeline config: {exc}") from exc

    has_dq = p.min_rows_expected is not None or p.max_rows_drop_pct is not None
    job = Job(
        job_id=str(uuid.uuid4()),
        pipeline_id=str(pipeline_id),
        pipeline_schema_hash=p.last_schema_hash,
        pipeline_pii=p.pii_columns or None,
        pipeline_dq={
            "min_rows_expected": p.min_rows_expected,
            "max_rows_drop_pct": p.max_rows_drop_pct,
            "prev_rows": None,
        } if has_dq else None,
        is_backfill=bool(body.date_from and body.date_to),
    )

    try:
        source_cls = get_source(req.source.type)
        dest_cls = get_destination(req.destination.type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    source = source_cls(**req.source.model_dump(exclude={"type"}))
    destination = dest_cls(**req.destination.model_dump(exclude={"type"}), job_id=job.job_id)

    # Create the job_run row first so worker can UPDATE it instead of INSERT
    now = datetime.now(tz=UTC)
    run = JobRun(
        job_id=job.job_id,
        pipeline_id=pipeline_id,
        status="queued",
        triggered_by="backfill" if job.is_backfill else "manual",
        created_at=now,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    job.run_id = run.id  # worker will UPDATE this row on completion

    q = _get_queue()
    await q.submit(job, source, destination)

    return {"job_id": job.job_id, "status": job.status, "run_id": run.id}


# ── Pipeline runs list ────────────────────────────────────────────────────────


@router.get("/{pipeline_id}/runs")
async def get_pipeline_runs(
    pipeline_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict]:
    result = await db.execute(
        select(JobRun)
        .where(JobRun.pipeline_id == pipeline_id)
        .order_by(desc(JobRun.created_at))
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "id": r.job_id,
            "job_id": r.job_id,
            "status": r.status,
            "triggered_by": r.triggered_by,
            "rows_read": r.rows_read,
            "rows_written": r.rows_written,
            "duration_ms": r.duration_ms,
            "schema_changed": r.schema_changed,
            "error": r.error,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "created_at": r.created_at.isoformat(),
        }
        for r in result.scalars().all()
    ]
