# src/siphon/tasks.py
import asyncio
import dataclasses
from datetime import UTC, datetime

import structlog

from siphon.celery_app import app
from siphon.models import Job

logger = structlog.get_logger()


def _job_to_dict(job: Job) -> dict:
    """Serialize a Job dataclass to a JSON-safe dict for Celery transport."""
    d = dataclasses.asdict(job)
    # Convert datetime fields to ISO strings
    for key in ("created_at", "started_at", "finished_at"):
        if d.get(key) is not None:
            d[key] = d[key].isoformat()
    # _actual_schema is transient (Arrow schema object) — drop it
    d.pop("_actual_schema", None)
    return d


def _job_from_dict(d: dict) -> Job:
    """Reconstruct a Job dataclass from a Celery-transported dict."""
    d = dict(d)  # copy to avoid mutating caller's dict
    # Parse datetime fields back
    for key in ("created_at", "started_at", "finished_at"):
        if d.get(key) is not None:
            d[key] = datetime.fromisoformat(d[key])
    valid_fields = {f.name for f in dataclasses.fields(Job)}
    return Job(**{k: v for k, v in d.items() if k in valid_fields})


@app.task(
    bind=True,
    name="siphon.tasks.run_pipeline_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_pipeline_task(self, job_dict: dict, source_dict: dict, destination_dict: dict) -> None:
    """Celery task: reconstruct Job + plugins, call run_job(), persist result.

    Uses a single asyncio.run() call with a fresh engine per task to avoid
    event loop / connection pool conflicts in Celery prefork workers.
    """
    from siphon.plugins.destinations import get as get_destination
    from siphon.plugins.sources import get as get_source

    job = _job_from_dict(job_dict)
    structlog.contextvars.bind_contextvars(job_id=job.job_id, pipeline_id=job.pipeline_id)

    source_dict = dict(source_dict)
    destination_dict = dict(destination_dict)
    source_type = source_dict.pop("type", None)
    dest_type = destination_dict.pop("type", None)

    try:
        source_cls = get_source(source_type)
        dest_cls = get_destination(dest_type)
    except ValueError as exc:
        logger.error("Unknown plugin type", error=str(exc))
        raise  # permanent failure — don't retry an invalid plugin type

    source = source_cls(**source_dict)
    destination = dest_cls(**destination_dict, job_id=job.job_id)

    try:
        asyncio.run(_run_job_async(source, destination, job))
    except Exception as exc:
        logger.error("Task failed, retrying", error=str(exc))
        raise self.retry(exc=exc) from exc


async def _run_job_async(source, destination, job) -> None:
    """Async wrapper: creates a fresh DB engine per task, runs the job, disposes engine.

    A fresh engine is required because Celery prefork workers share module state
    across tasks. Reusing an engine whose connection pool was bound to a previous
    event loop causes 'Future attached to a different loop' errors.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from siphon import worker
    from siphon.orm import JobRun

    db_url = os.getenv("DATABASE_URL")
    engine = create_async_engine(db_url, pool_pre_ping=True) if db_url else None
    db_factory = async_sessionmaker(engine, expire_on_commit=False) if engine else None

    try:
        # Mark job as running
        if db_factory:
            try:
                async with db_factory() as session:
                    result = await session.execute(
                        select(JobRun)
                        .where(JobRun.job_id == job.job_id)
                        .order_by(JobRun.id.desc())
                        .limit(1)
                    )
                    run = result.scalar_one_or_none()
                    if run and run.status == "queued":
                        run.status = "running"
                        run.started_at = datetime.now(UTC)
                        await session.commit()
            except Exception:
                pass  # best-effort — don't abort the job if status update fails

        max_workers = int(os.getenv("SIPHON_MAX_WORKERS", "1"))
        job_timeout = int(os.getenv("SIPHON_JOB_TIMEOUT", "3600"))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            await worker.run_job(source, destination, job, executor, job_timeout, db_factory=db_factory)
    finally:
        if engine:
            await engine.dispose()


def _purge_s3_files(
    base_path: str,
    before_date,
    partition_filter: str | None,
    list_fn=None,
    delete_fn=None,
) -> dict:
    """List and delete Parquet files under base_path matching the given filters.

    list_fn and delete_fn are injectable for testing. In production they use
    PyArrow S3FileSystem.
    """
    import os

    if list_fn is None:
        def list_fn(path):
            import pyarrow.fs as pafs
            endpoint = os.getenv("SIPHON_S3_ENDPOINT", "http://minio:9000")
            access_key = os.getenv("SIPHON_S3_ACCESS_KEY", "")
            secret_key = os.getenv("SIPHON_S3_SECRET_KEY", "")
            fs = pafs.S3FileSystem(
                endpoint_override=endpoint,
                access_key=access_key,
                secret_key=secret_key,
            )
            bucket_path = path.removeprefix("s3://")
            file_info = fs.get_file_info(pafs.FileSelector(bucket_path, recursive=True))
            return [
                "s3://" + f.path
                for f in file_info
                if f.is_file and f.path.endswith(".parquet")
            ]

    if delete_fn is None:
        def delete_fn(path):
            import pyarrow.fs as pafs2
            fs = pafs2.S3FileSystem(
                endpoint_override=os.getenv("SIPHON_S3_ENDPOINT", "http://minio:9000"),
                access_key=os.getenv("SIPHON_S3_ACCESS_KEY", ""),
                secret_key=os.getenv("SIPHON_S3_SECRET_KEY", ""),
            )
            info = fs.get_file_info(path.removeprefix("s3://"))
            size = info.size or 0
            fs.delete_file(path.removeprefix("s3://"))
            return size

    files = list_fn(base_path)

    # Apply filters
    if partition_filter:
        files = [f for f in files if partition_filter in f]
    if before_date:
        import re
        _date_re = re.compile(r'_date=(\d{4}-\d{2}-\d{2})')
        from datetime import date as _date_type

        def _file_before_date(path: str) -> bool:
            m = _date_re.search(path)
            if not m:
                return False  # skip files without a date partition
            return _date_type.fromisoformat(m.group(1)) < before_date

        files = [f for f in files if _file_before_date(f)]

    total_bytes = 0
    for f in files:
        total_bytes += delete_fn(f)

    return {"files_deleted": len(files), "bytes_deleted": total_bytes}


@app.task(
    bind=True,
    name="siphon.tasks.purge_s3_data_task",
    max_retries=2,
    default_retry_delay=60,
)
def purge_s3_data_task(
    self,
    event_id: str,
    base_path: str,
    before_date_str: str | None,
    partition_filter: str | None,
) -> None:
    """Background Celery task: purge Parquet files and update gdpr_events row."""
    from datetime import date as date_type

    before_date = date_type.fromisoformat(before_date_str) if before_date_str else None

    try:
        result = _purge_s3_files(base_path, before_date, partition_filter)
    except Exception as exc:
        logger.error("S3 purge failed", event_id=event_id, error=str(exc))
        raise self.retry(exc=exc) from exc

    asyncio.run(_update_gdpr_event(event_id, result))


async def _update_gdpr_event(event_id: str, result: dict) -> None:
    """Mark the gdpr_events row as completed with file/byte counts.

    Creates a fresh async engine per call so that Celery prefork workers never
    reuse a connection pool that was bound to a different event loop.
    """
    import os
    import uuid as uuid_mod

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from siphon.orm import GdprEvent

    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url, pool_pre_ping=True)
    db_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with db_factory() as session:
            event = await session.get(GdprEvent, uuid_mod.UUID(event_id))
            if event:
                event.status = "completed"
                event.files_deleted = result["files_deleted"]
                event.bytes_deleted = result["bytes_deleted"]
                event.completed_at = datetime.now(tz=UTC)
                await session.commit()
    finally:
        await engine.dispose()
