# src/siphon/runs/router.py
"""Global job-run history and per-run log access.

GET  /api/v1/runs              — paginated list of all job_runs
GET  /api/v1/runs/{id}/logs    — cursor-based in-memory logs for a running job
POST /api/v1/runs/{id}/cancel  — request cancellation of a queued/running job
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.db import get_db
from siphon.orm import JobRun

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _run_to_dict(r: JobRun) -> dict:
    return {
        "id": r.job_id,
        "job_id": r.job_id,
        "pipeline_id": str(r.pipeline_id) if r.pipeline_id else None,
        "source_connection_id": str(r.source_connection_id) if r.source_connection_id else None,
        "destination_path": r.destination_path,
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


@router.get("")
async def list_runs(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict]:
    """Return paginated job_runs ordered newest-first."""
    result = await db.execute(
        select(JobRun).order_by(desc(JobRun.created_at)).limit(limit).offset(offset)
    )
    return [_run_to_dict(r) for r in result.scalars().all()]


@router.get("/{run_id}", response_model=None)
async def get_run(
    run_id: str,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Return a single job_run by job_id."""
    from sqlalchemy import select as sa_select
    result = await db.execute(sa_select(JobRun).where(JobRun.job_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(run)


@router.get("/{run_id}/logs")
async def get_run_logs(
    run_id: str,
    since: int = Query(0, ge=0, description="Return only log lines at or after this offset"),
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Return in-memory log lines for a job that is queued or running.

    For completed jobs the logs live only in memory until the job is evicted
    (``SIPHON_JOB_TTL_SECONDS``).  Use the ``since`` parameter for polling:
    pass the ``next_offset`` from the previous response.
    """
    from sqlalchemy import select as sa_select
    result = await db.execute(sa_select(JobRun).where(JobRun.job_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")

    from siphon.main import queue

    job = queue.get_job(run_id)
    if job is None:
        # Job already evicted or never in memory (historical run)
        return {"run_id": run_id, "logs": [], "next_offset": since}

    logs_slice = job.logs[since:]
    return {
        "run_id": run_id,
        "logs": logs_slice,
        "next_offset": since + len(logs_slice),
    }


@router.post("/{run_id}/cancel", status_code=202)
async def cancel_run(
    run_id: str,
    principal: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Request cancellation of a queued or running job.

    Siphon's queue does not support hard-kill of running threads.  This endpoint
    marks the job as ``failed`` with ``error="Cancelled by user"`` if it is still
    queued (not yet started).  Running jobs log a cancellation request but finish
    naturally — true mid-extraction cancel is not implemented in v1.
    """
    principal.require_admin()

    from sqlalchemy import select as sa_select
    result = await db.execute(sa_select(JobRun).where(JobRun.job_id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.status not in ("queued", "running"):
        raise HTTPException(409, f"Run is already {run.status!r}")

    from siphon.main import queue

    job = queue.get_job(run_id)
    if job is None:
        raise HTTPException(409, "Job not found in queue (may have already completed)")

    if job.status == "queued":
        job.status = "failed"
        job.error = "Cancelled by user"
        job.logs.append("ERROR: Cancelled by user")
        run.status = "failed"
        run.error = "Cancelled by user"
        await db.commit()
        return {"run_id": run_id, "status": "failed", "message": "Job cancelled"}

    # Running — cannot hard-kill; log the request
    job.logs.append("WARNING: Cancellation requested by user (job will finish current batch)")
    return {"run_id": run_id, "status": "running", "message": "Cancellation requested"}
