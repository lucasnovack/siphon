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


class ConcurrencyLimitReached(Exception):
    """Raised when a connection already has max_concurrent_jobs running."""


@app.task(
    bind=True,
    name="siphon.tasks.run_pipeline_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_pipeline_task(
    self,
    job_dict: dict,
    source_dict: dict,
    destination_dict: dict,
    max_concurrent: int | None = None,
) -> None:
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
        asyncio.run(_run_job_async(source, destination, job, max_concurrent=max_concurrent))
    except ConcurrencyLimitReached as exc:
        delay = min(15 * (2 ** self.request.retries), 120)
        logger.warning(
            "concurrency_limit_reached_retrying",
            job_id=job.job_id,
            source_connection_id=job.source_connection_id,
            countdown=delay,
            retries=self.request.retries,
        )
        raise self.retry(exc=exc, countdown=delay, max_retries=20) from exc
    except Exception as exc:
        logger.error("Task failed, retrying", error=str(exc))
        raise self.retry(exc=exc) from exc


async def _run_job_async(
    source,
    destination,
    job,
    max_concurrent: int | None = None,
) -> None:
    """Async wrapper: creates a fresh NullPool DB engine per task, runs the job, disposes engine.

    A fresh NullPool engine is required because Celery prefork workers share module state
    across tasks and should not hold idle pooled connections per task.
    """
    import os
    import uuid
    from concurrent.futures import ThreadPoolExecutor
    from datetime import UTC, datetime

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from siphon import worker
    from siphon.concurrency import acquire_connection_slot, release_connection_slot
    from siphon.orm import Connection, JobRun

    db_url = os.getenv("DATABASE_URL")
    engine = create_async_engine(db_url, poolclass=NullPool) if db_url else None
    db_factory = async_sessionmaker(engine, expire_on_commit=False) if engine else None
    job_timeout = int(os.getenv("SIPHON_JOB_TIMEOUT", "3600"))
    redis_slot_acquired = False

    try:
        # Check per-connection concurrency limit and mark job as running
        if db_factory:
            try:
                async with db_factory() as session:
                    if job.source_connection_id:
                        conn_uuid = uuid.UUID(job.source_connection_id)
                        use_redis = bool(os.getenv("REDIS_URL"))
                        limit_stmt = select(Connection.max_concurrent_jobs).where(
                            Connection.id == conn_uuid
                        )
                        if not use_redis:
                            limit_stmt = limit_stmt.with_for_update()

                        conn_limit = await session.scalar(limit_stmt)
                        effective_limit = conn_limit if conn_limit is not None else max_concurrent
                        if effective_limit is not None and effective_limit >= 1:
                            redis_result = None
                            if use_redis:
                                redis_result = await asyncio.to_thread(
                                    acquire_connection_slot,
                                    job.source_connection_id,
                                    job.job_id,
                                    effective_limit,
                                    job_timeout + 60,
                                )

                            if redis_result is not None:
                                acquired, running_count = redis_result
                                if not acquired:
                                    raise ConcurrencyLimitReached(
                                        f"Connection {job.source_connection_id} reached "
                                        f"max_concurrent_jobs ({running_count}/{effective_limit})"
                                    )
                                redis_slot_acquired = True
                            else:
                                running_count = await session.scalar(
                                    select(func.count(JobRun.id)).where(
                                        JobRun.source_connection_id == conn_uuid,
                                        JobRun.status == "running",
                                        JobRun.job_id != job.job_id,
                                    )
                                )
                                if (running_count or 0) >= effective_limit:
                                    raise ConcurrencyLimitReached(
                                        f"Connection {job.source_connection_id} reached "
                                        f"max_concurrent_jobs ({running_count}/{effective_limit})"
                                    )

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
                        if not job.logs:
                            job.logs.append(f"Job picked up by worker (job_id={job.job_id})")
                        run.logs = list(job.logs)
                        await session.commit()
            except ConcurrencyLimitReached:
                raise
            except Exception:
                pass  # best-effort — don't abort the job if status update fails

        max_workers = int(os.getenv("SIPHON_MAX_WORKERS", "1"))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            await worker.run_job(source, destination, job, executor, job_timeout, db_factory=db_factory)
    finally:
        if redis_slot_acquired and job.source_connection_id:
            await asyncio.to_thread(
                release_connection_slot,
                job.source_connection_id,
                job.job_id,
            )
        if engine:
            await engine.dispose()


def _build_purge_fs(base_path: str, dest_config: dict | None = None):
    """Build a PyArrow FileSystem and scheme prefix for S3 or GCS purge operations."""
    import os

    import pyarrow.fs as pafs

    cfg = dest_config or {}
    is_gcs = base_path.startswith("gs://") or cfg.get("type") == "gcs_parquet"
    if is_gcs:
        from siphon.plugins.destinations.gcs_auth import build_gcs_filesystem

        fs = build_gcs_filesystem(
            auth_mode=cfg.get("auth_mode") or "adc",
            credentials_json=cfg.get("credentials_json") or None,
            project_id=cfg.get("project_id") or None,
            target_service_account=cfg.get("target_service_account") or None,
            endpoint_override=cfg.get("endpoint_override") or None,
            scheme=cfg.get("scheme") or None,
        )
        return fs, "gs://"

    endpoint = cfg.get("endpoint") or os.getenv("SIPHON_S3_ENDPOINT", "http://minio:9000")
    access_key = cfg.get("access_key") or os.getenv("SIPHON_S3_ACCESS_KEY", "")
    secret_key = cfg.get("secret_key") or os.getenv("SIPHON_S3_SECRET_KEY", "")
    scheme = os.getenv("SIPHON_S3_SCHEME", "https")
    fs_kwargs: dict = {
        "endpoint_override": endpoint,
        "access_key": access_key,
        "secret_key": secret_key,
    }
    if not endpoint.startswith(("http://", "https://")):
        fs_kwargs["scheme"] = scheme
    fs = pafs.S3FileSystem(**fs_kwargs)
    prefix = "s3a://" if base_path.startswith("s3a://") else "s3://"
    return fs, prefix


def _purge_s3_files(
    base_path: str,
    before_date,
    partition_filter: str | None,
    list_fn=None,
    delete_fn=None,
    dest_config: dict | None = None,
) -> dict:
    """List and delete Parquet files under base_path (S3 or GCS) matching filters.

    list_fn and delete_fn are injectable for testing. In production they use
    PyArrow S3FileSystem or GcsFileSystem depending on the path scheme.
    """
    if list_fn is None or delete_fn is None:
        import pyarrow.fs as pafs

        fs, scheme_prefix = _build_purge_fs(base_path, dest_config)

        if list_fn is None:
            def list_fn(path):
                bucket_path = path.removeprefix("s3a://").removeprefix("s3://").removeprefix("gs://")
                file_info = fs.get_file_info(pafs.FileSelector(bucket_path, recursive=True))
                return [
                    scheme_prefix + f.path
                    for f in file_info
                    if f.is_file and f.path.endswith(".parquet")
                ]

        if delete_fn is None:
            def delete_fn(path):
                key = path.removeprefix("s3a://").removeprefix("s3://").removeprefix("gs://")
                info = fs.get_file_info(key)
                size = info.size or 0
                fs.delete_file(key)
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
    dest_config: dict | None = None,
) -> None:
    """Background Celery task: purge Parquet files (S3 or GCS) and update gdpr_events row."""
    from datetime import date as date_type

    before_date = date_type.fromisoformat(before_date_str) if before_date_str else None

    try:
        result = _purge_s3_files(
            base_path,
            before_date,
            partition_filter,
            dest_config=dest_config,
        )
    except Exception as exc:
        logger.error("Object store purge failed", event_id=event_id, error=str(exc))
        raise self.retry(exc=exc) from exc

    asyncio.run(_update_gdpr_event(event_id, result))


async def _update_gdpr_event(event_id: str, result: dict) -> None:
    """Mark the gdpr_events row as completed with file/byte counts.

    Creates a fresh NullPool async engine per call so that Celery prefork workers
    never reuse a connection pool bound to a different event loop or hold idle connections.
    """
    import os
    import uuid as uuid_mod

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from siphon.orm import GdprEvent

    db_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(db_url, poolclass=NullPool)
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


def _json_safe(value):
    """Convert non-JSON-native SQL/Arrow Python values for Celery JSON transport."""
    import uuid
    from datetime import date, datetime, time
    from decimal import Decimal

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def execute_preview_sql(connection_str: str, limited_query: str) -> dict:
    """Run a bounded preview SQL query via ConnectorX and return a JSON-safe payload."""
    import connectorx as cx

    from siphon.plugins.sources.sql import _validate_host

    _validate_host(connection_str)
    table = cx.read_sql(connection_str, limited_query, return_type="arrow")
    d = table.to_pydict()
    columns = list(d.keys())
    rows = [[_json_safe(d[col][i]) for col in columns] for i in range(table.num_rows)]
    return {"columns": columns, "rows": rows, "row_count": table.num_rows}


@app.task(
    name="siphon.tasks.run_preview_task",
    queue="preview",
    soft_time_limit=20,
    time_limit=25,
)
def run_preview_task(connection_str: str, limited_query: str) -> dict:
    """Celery task executed on the isolated 'preview' queue."""
    return execute_preview_sql(connection_str, limited_query)
