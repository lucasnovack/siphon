# src/siphon/worker.py
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlparse

import structlog
from opentelemetry import trace as _otel_trace

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source
from siphon.plugins.sources.sql import _validate_host

logger = structlog.get_logger()
_tracer = _otel_trace.get_tracer("siphon.worker")

try:
    from siphon.metrics import (
        job_duration_seconds,
        jobs_total,
        rows_extracted_total,
        schema_changes_total,
    )
    _METRICS = True
except Exception:  # prometheus_client not installed in some test envs
    _METRICS = False


# ── Schema hash ───────────────────────────────────────────────────────────────


def _compute_schema_hash(schema) -> str:
    """Return a SHA-256 hex digest of the Arrow schema field names + types."""
    fields = [(f.name, str(f.type)) for f in schema]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


# ── Schema registry ───────────────────────────────────────────────────────────


def _schema_to_dict(schema) -> list[dict]:
    """Serialize an Arrow schema to a JSON-safe list of field descriptors.

    Format: [{"name": "col", "type": "int64", "nullable": True}, ...]
    """
    return [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]


def _check_schema(actual, expected: list[dict]) -> str | None:
    """Validate *actual* Arrow schema against *expected* field descriptors.

    Rules:
    - Missing column (in expected but not actual): error
    - Type mismatch: error
    - Extra column (in actual but not expected): warning log only, no error

    Returns an error message string on failure, None on success.
    """
    expected_map = {f["name"]: f["type"] for f in expected}
    actual_map = {field.name: str(field.type) for field in actual}

    missing = [n for n in expected_map if n not in actual_map]
    if missing:
        return f"Schema validation failed: missing columns {missing}"

    mismatched = [
        f"{n}: expected {expected_map[n]!r}, got {actual_map[n]!r}"
        for n in expected_map
        if n in actual_map and actual_map[n] != expected_map[n]
    ]
    if mismatched:
        return f"Schema validation failed: type mismatch [{', '.join(mismatched)}]"

    extra = [n for n in actual_map if n not in expected_map]
    if extra:
        logger.warning("extra_columns_not_in_expected_schema", columns=extra)

    return None


# ── Data quality ──────────────────────────────────────────────────────────────


def _check_data_quality(dq: dict, rows_read: int) -> str | None:
    """Return an error message if DQ constraints are violated, else None."""
    min_exp = dq.get("min_rows_expected")
    if min_exp is not None and rows_read < min_exp:
        return f"Data quality: expected >= {min_exp} rows, got {rows_read}"

    max_drop = dq.get("max_rows_drop_pct")
    prev_rows = dq.get("prev_rows")
    if max_drop is not None and prev_rows:
        drop_pct = (1 - rows_read / prev_rows) * 100
        if drop_pct > max_drop:
            return (
                f"Data quality: row drop {drop_pct:.1f}% exceeds limit {max_drop}%"
                f" (prev={prev_rows}, current={rows_read})"
            )
    return None


# ── PII masking ───────────────────────────────────────────────────────────────


def _apply_pii_masking(table, pii_columns: dict[str, str]):
    """Apply sha256 hashing or redaction to specified columns.

    Columns not present in the table are silently skipped.
    Schema hash should be captured BEFORE calling this function.
    """
    import pyarrow as pa

    for col_name, method in pii_columns.items():
        if col_name not in table.schema.names:
            continue
        if method == "sha256":
            hashed = pa.array(
                [hashlib.sha256(str(v).encode()).hexdigest() if v is not None else None
                 for v in table.column(col_name).to_pylist()]
            )
            idx = table.schema.get_field_index(col_name)
            table = table.set_column(idx, col_name, hashed)
        elif method == "redact":
            null_col = pa.array([None] * table.num_rows, type=pa.null())
            idx = table.schema.get_field_index(col_name)
            table = table.set_column(idx, col_name, null_col)
    return table


# ── Webhook firing ───────────────────────────────────────────────────────────


def _fire_webhook(url: str, payload: dict) -> None:
    """POST *payload* as JSON to *url* with a 5-second timeout.

    Failures are logged but never propagated — a broken webhook must not
    affect the job result or the caller's control flow.
    """
    try:
        _validate_host(urlparse(url).hostname)
    except Exception:
        logger.warning("webhook_host_blocked", url=url)
        return
    try:
        import httpx
        httpx.post(url, json=payload, timeout=5)
    except Exception as exc:
        logger.warning("webhook_post_failed", url=url, error=str(exc))


def _maybe_fire_webhook(job: "Job") -> None:
    """Fire the pipeline webhook if the current job event matches alert_on."""
    alert = job.pipeline_alert
    if not alert:
        return
    url = alert.get("webhook_url")
    alert_on = alert.get("alert_on") or ["failed"]
    if not url:
        return

    events_to_fire = []
    if job.status == "failed" and "failed" in alert_on:
        events_to_fire.append("failed")
    if (
        "schema_changed" in alert_on
        and job.schema_hash
        and job.pipeline_schema_hash
        and job.schema_hash != job.pipeline_schema_hash
    ):
        events_to_fire.append("schema_changed")

    for event in events_to_fire:
        payload = {
            "event": event,
            "pipeline_id": job.pipeline_id,
            "job_id": job.job_id,
            "status": job.status,
            "error": job.error,
            "rows_read": job.rows_read,
            "rows_written": job.rows_written,
            "timestamp": datetime.now(tz=UTC).isoformat(),
        }
        _fire_webhook(url, payload)


# ── Core extraction ───────────────────────────────────────────────────────────


def _sync_extract_and_write(
    job: Job,
    source: Source,
    destination: Destination,
) -> tuple[int, int]:
    """Run extraction + write in the calling thread (ThreadPoolExecutor worker).

    Side effect: sets ``job.schema_hash`` after the first batch is read.
    When ``job.pipeline_dq`` is set, all batches are buffered so the row count
    can be checked before any write occurs (spec §18 data quality).
    """
    # Clean up any stale staging from a previous crashed run
    if hasattr(destination, "cleanup_staging"):
        destination.cleanup_staging()

    dq = job.pipeline_dq

    if dq is not None:
        # Buffer all batches to validate row count before writing
        batches = list(source.extract_batches())
        if not batches:
            dq_error = _check_data_quality(dq, 0)
            if dq_error:
                raise ValueError(dq_error)
            return 0, 0

        job.schema_hash = _compute_schema_hash(batches[0].schema)

        # Schema validation (expected_schema DQ check)
        if job.pipeline_expected_schema:
            schema_error = _check_schema(batches[0].schema, job.pipeline_expected_schema)
            if schema_error:
                raise ValueError(schema_error)

        # Store schema for registry update
        job._actual_schema = batches[0].schema

        rows_read = sum(b.num_rows for b in batches)

        dq_error = _check_data_quality(dq, rows_read)
        if dq_error:
            raise ValueError(dq_error)

        rows_written = 0
        for i, batch in enumerate(batches):
            if job.pipeline_pii:
                batch = _apply_pii_masking(batch, job.pipeline_pii)
            rows_written += destination.write(batch, is_first_chunk=(i == 0))
        return rows_read, rows_written

    # No DQ — stream directly without buffering
    rows_read = 0
    rows_written = 0
    for i, batch in enumerate(source.extract_batches()):
        if i == 0:
            job.schema_hash = _compute_schema_hash(batch.schema)
            # Schema validation
            if job.pipeline_expected_schema:
                schema_error = _check_schema(batch.schema, job.pipeline_expected_schema)
                if schema_error:
                    raise ValueError(schema_error)
            # Store schema for registry
            job._actual_schema = batch.schema
        rows_read += batch.num_rows
        if job.pipeline_pii:
            batch = _apply_pii_masking(batch, job.pipeline_pii)
        rows_written += destination.write(batch, is_first_chunk=(i == 0))
    return rows_read, rows_written


# ── Persistence ───────────────────────────────────────────────────────────────


async def _persist_job_run(job: Job, db_factory) -> None:
    """Write or update a JobRun row.

    - If ``job.run_id`` is set: UPDATE the existing row created by trigger_pipeline.
    - Otherwise: INSERT a new row (legacy ``POST /jobs`` path, no pipeline).

    Errors are logged but never propagated so they cannot mask the real job result.
    """
    try:
        import uuid

        from sqlalchemy import select

        from siphon.orm import JobRun

        duration_ms = None
        if job.started_at and job.finished_at:
            duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

        schema_changed = bool(
            job.schema_hash
            and job.pipeline_schema_hash
            and job.schema_hash != job.pipeline_schema_hash
        )

        async with db_factory() as session:
            if job.run_id is not None:
                # UPDATE the row that was created as "queued" in trigger_pipeline
                result = await session.execute(
                    select(JobRun).where(JobRun.id == job.run_id)
                )
                run = result.scalar_one_or_none()
                if run is not None:
                    run.status = job.status
                    run.rows_read = job.rows_read
                    run.rows_written = job.rows_written
                    run.duration_ms = duration_ms
                    run.error = job.error
                    run.schema_changed = schema_changed
                    run.started_at = job.started_at
                    run.finished_at = job.finished_at
                    run.source_connection_id = (
                        uuid.UUID(job.source_connection_id)
                        if job.source_connection_id else None
                    )
                    run.destination_path = job.destination_path
                    await session.commit()
                    return

            # Fallback: INSERT (legacy /jobs path or run_id row not found)
            run = JobRun(
                job_id=job.job_id,
                status=job.status,
                rows_read=job.rows_read,
                rows_written=job.rows_written,
                duration_ms=duration_ms,
                error=job.error,
                schema_changed=schema_changed,
                started_at=job.started_at,
                finished_at=job.finished_at,
                source_connection_id=(
                    uuid.UUID(job.source_connection_id)
                    if job.source_connection_id else None
                ),
                destination_path=job.destination_path,
                created_at=datetime.now(tz=UTC),
            )
            session.add(run)
            await session.commit()

    except Exception as exc:
        logger.warning("persist_job_run_failed", error=str(exc))


async def _update_pipeline_metadata(job: Job, db_factory) -> None:
    """After a successful pipeline job: update last_watermark and last_schema_hash.

    Only runs when ``job.pipeline_id`` is set and status is success/partial_success.
    Watermark is set to current UTC (ISO-8601) — upper bound of extracted data.
    Only updates watermark for incremental pipelines; always updates schema hash.
    """
    if job.pipeline_id is None:
        return
    if job.status not in ("success", "partial_success"):
        return
    if job.is_backfill:
        return  # backfill runs must not move the global watermark
    try:
        import uuid

        from sqlalchemy import select

        from siphon.orm import Pipeline

        async with db_factory() as session:
            pipeline_uuid = uuid.UUID(job.pipeline_id)
            result = await session.execute(
                select(Pipeline).where(Pipeline.id == pipeline_uuid)
            )
            pipeline = result.scalar_one_or_none()
            if pipeline is None:
                return
            # Always update the watermark on success so that switching from
            # full → incremental starts from the last successful run, not from
            # a stale watermark left by a previous incremental run.
            pipeline.last_watermark = datetime.now(tz=UTC).isoformat()
            if job.schema_hash:
                pipeline.last_schema_hash = job.schema_hash
            if getattr(job, "_actual_schema", None) is not None:
                pipeline.last_schema = _schema_to_dict(job._actual_schema)
            pipeline.updated_at = datetime.now(tz=UTC)
            await session.commit()
    except Exception as exc:
        logger.warning(
            "update_pipeline_metadata_failed", pipeline_id=job.pipeline_id, error=str(exc)
        )


# ── Main entry point ──────────────────────────────────────────────────────────


async def run_job(
    source: Source,
    destination: Destination,
    job: Job,
    executor: ThreadPoolExecutor,
    timeout: int,
    db_factory=None,
) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        job_id=job.job_id,
        pipeline_id=job.pipeline_id,
    )
    with _tracer.start_as_current_span("siphon.job") as span:
        span.set_attribute("job_id", job.job_id)
        if job.pipeline_id:
            span.set_attribute("pipeline_id", job.pipeline_id)
        await _run_job_inner(source, destination, job, executor, timeout, db_factory)


async def _run_job_inner(
    source: Source,
    destination: Destination,
    job: Job,
    executor: ThreadPoolExecutor,
    timeout: int,
    db_factory=None,
) -> None:
    """Execute a single extraction job. Updates job state in-place.

    db_factory: optional async_sessionmaker from db.py. When provided:
    - persists/updates a JobRun row after the job completes
    - updates pipeline watermark and schema hash on success
    """
    loop = asyncio.get_running_loop()
    job.status = "running"
    job.started_at = datetime.now(tz=UTC)
    logger.info("job_started", pipeline=job.pipeline_id or "none")

    try:
        rows_read, rows_written = await asyncio.wait_for(
            loop.run_in_executor(executor, _sync_extract_and_write, job, source, destination),
            timeout=timeout,
        )
        job.rows_read = rows_read
        job.rows_written = rows_written
        failed_files = list(getattr(source, "failed_files", []))
        job.failed_files = failed_files

        if rows_read != rows_written:
            job.status = "failed"
            job.error = (
                f"Row count mismatch: {rows_read} rows read but {rows_written} rows written"
            )
            job.logs.append(f"ERROR: {job.error}")
            logger.error("job_row_count_mismatch", rows_read=rows_read, rows_written=rows_written)
        elif failed_files:
            job.status = "partial_success"
            job.logs.append(
                f"Partial success: {rows_read} rows written, {len(failed_files)} files failed"
            )
            logger.warning("job_partial_success", failed_files=len(failed_files))
        else:
            job.status = "success"
            job.logs.append(f"Completed: {rows_read} rows read, {rows_written} rows written")
            logger.info("job_succeeded", rows=rows_read)

        # Schema change detection — log warning; write is never blocked
        if job.schema_hash and job.pipeline_schema_hash and job.schema_hash != job.pipeline_schema_hash:
            msg = (
                f"Schema change detected for pipeline {job.pipeline_id}: "
                f"old={job.pipeline_schema_hash[:8]}… new={job.schema_hash[:8]}…"
            )
            job.logs.append(f"WARNING: {msg}")
            logger.warning(
                "schema_change_detected",
                pipeline_id=job.pipeline_id,
                old_hash=job.pipeline_schema_hash[:8],
                new_hash=job.schema_hash[:8],
            )

    except TimeoutError:
        job.status = "failed"
        job.error = f"Job exceeded timeout of {timeout}s"
        job.logs.append(f"ERROR: {job.error}")
        logger.error("job_timed_out", timeout_s=timeout)

    except ValueError as exc:
        # Data quality violations raise ValueError from _check_data_quality
        job.status = "failed"
        job.error = str(exc)
        job.logs.append(f"ERROR: {job.error}")
        logger.error("job_failed_dq", error=str(exc))

    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.logs.append(f"ERROR: {job.error}")
        logger.error("job_failed", error=str(exc))

    finally:
        job.finished_at = datetime.now(tz=UTC)
        if _METRICS and job.started_at and job.finished_at:
            elapsed = (job.finished_at - job.started_at).total_seconds()
            job_duration_seconds.observe(elapsed)
            jobs_total.labels(status=job.status).inc()
            if job.rows_written:
                rows_extracted_total.inc(job.rows_written)
            if job.schema_hash and job.pipeline_schema_hash and job.schema_hash != job.pipeline_schema_hash:
                schema_changes_total.inc()
        # Promote staging BEFORE DB writes so that if the pod dies after promote
        # but before the watermark update, the next run re-extracts (at-most-twice
        # for incremental; idempotent for full_refresh). The previous order
        # (promote after DB) risked advancing the watermark while data sat in
        # staging and got cleaned up on restart — a silent data gap.
        if job.status in ("success", "partial_success") and hasattr(destination, "promote"):
            try:
                await loop.run_in_executor(executor, destination.promote)
            except Exception as exc:
                logger.error("staging_promote_failed", error=str(exc))
        if db_factory is not None:
            await _persist_job_run(job, db_factory)
            await _update_pipeline_metadata(job, db_factory)
        # Fire webhook if configured and event matches
        if job.pipeline_alert:
            _maybe_fire_webhook(job)
