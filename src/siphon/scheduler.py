# src/siphon/scheduler.py
"""APScheduler integration for scheduled pipeline execution.

Design decisions:
- Uses BackgroundScheduler (thread-based) so it runs alongside FastAPI's async loop.
- PostgreSQL jobstore: schedules survive pod restarts.
- Advisory lock: ``pg_try_advisory_xact_lock`` prevents duplicate execution when
  multiple replicas are running (rolling deploy safety).
- ``sync_schedule`` / ``remove_schedule`` are called by the pipelines router after
  each schedule upsert/delete; they gracefully no-op when DATABASE_URL is absent.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import asyncio
import structlog

logger = structlog.get_logger()

_scheduler = None  # module-level singleton, started in lifespan
_loop: asyncio.AbstractEventLoop | None = None  # set via set_event_loop() from lifespan


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the running event loop so APScheduler background threads can submit coroutines."""
    global _loop
    _loop = loop


# ── Public API ────────────────────────────────────────────────────────────────


def get_scheduler():
    return _scheduler


def start_scheduler() -> None:
    """Initialise and start the APScheduler instance.

    Called from ``main.py`` lifespan on startup.  When ``DATABASE_URL`` is not
    set (unit-test / local-no-db), silently returns without starting a scheduler.
    """
    global _scheduler

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.warning("Scheduler not started: DATABASE_URL is not set")
        return

    try:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.background import BackgroundScheduler

        # APScheduler uses synchronous SQLAlchemy — strip the async driver
        # so psycopg2 is used instead of asyncpg.
        sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        jobstore = SQLAlchemyJobStore(url=sync_url)
        _scheduler = BackgroundScheduler(jobstores={"default": jobstore})
        _scheduler.start()
        logger.info("Scheduler started with PostgreSQL jobstore")
        _scheduler.add_job(
            _check_sla_violations,
            trigger="interval",
            minutes=5,
            id="sla_checker",
            replace_existing=True,
        )
        logger.info("SLA checker scheduled (every 5 minutes)")
    except Exception as exc:
        logger.error("scheduler_start_failed", error=str(exc))
        _scheduler = None


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully.  Called from ``main.py`` lifespan on shutdown."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception as exc:
            logger.warning("scheduler_stop_error", error=str(exc))
        _scheduler = None


async def sync_schedule(pipeline_id: uuid.UUID, cron: str, is_active: bool) -> None:
    """Add or update an APScheduler job for *pipeline_id*.

    Called by the pipelines router after a schedule upsert.
    The job id in APScheduler is the pipeline UUID string.
    """
    if _scheduler is None:
        return

    job_id = str(pipeline_id)
    try:
        if not is_active:
            _remove_job(job_id)
            return

        trigger_kwargs = _parse_cron(cron)

        existing = _scheduler.get_job(job_id)
        if existing:
            _scheduler.reschedule_job(job_id, trigger="cron", **trigger_kwargs)
            logger.info("job_rescheduled", job_id=job_id, cron=cron)
        else:
            _scheduler.add_job(
                _fire_pipeline,
                trigger="cron",
                id=job_id,
                args=[job_id],
                replace_existing=True,
                **trigger_kwargs,
            )
            logger.info("job_scheduled", job_id=job_id, cron=cron)
    except Exception as exc:
        logger.error("sync_schedule_failed", pipeline_id=str(pipeline_id), error=str(exc))


async def remove_schedule(pipeline_id: uuid.UUID) -> None:
    """Remove the APScheduler job for *pipeline_id*, if any."""
    if _scheduler is None:
        return
    _remove_job(str(pipeline_id))


# ── Internal helpers ──────────────────────────────────────────────────────────


def _remove_job(job_id: str) -> None:
    try:
        _scheduler.remove_job(job_id)
        logger.info("schedule_removed", job_id=job_id)
    except Exception:
        pass  # job may not exist


def _parse_cron(cron: str) -> dict:
    """Parse a 5-field cron string into APScheduler CronTrigger kwargs.

    Validates the expression by constructing a CronTrigger, which raises
    ValueError on invalid field values (e.g. '99 99 99 99 99').
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (expected 5 fields): {cron!r}")
    minute, hour, day, month, day_of_week = parts
    trigger_kwargs = {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger(**trigger_kwargs)
    except Exception as exc:
        raise ValueError(f"Invalid cron expression {cron!r}: {exc}") from exc
    return trigger_kwargs


def _fire_pipeline(pipeline_id_str: str) -> None:
    """Scheduled callback: acquire advisory lock then enqueue a pipeline trigger.

    Runs in the APScheduler background thread.  Uses a synchronous DB connection
    (psycopg2 / pg8000) for the advisory lock check, then submits to the async
    queue via ``asyncio.run_coroutine_threadsafe``.
    """
    try:
        _fire_with_advisory_lock(pipeline_id_str)
    except Exception as exc:
        logger.error("scheduled_fire_failed", pipeline_id=pipeline_id_str, error=str(exc))


def _fire_with_advisory_lock(pipeline_id_str: str) -> None:
    """Acquire a PostgreSQL advisory lock then fire the pipeline.

    Uses the lower 63 bits of the pipeline UUID as the lock key so it fits in
    a bigint.  If another pod already holds the lock, the job is skipped — this
    makes rolling deploys safe without requiring a ``Recreate`` strategy.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return

    lock_key = _uuid_to_lock_key(pipeline_id_str)

    import psycopg2

    sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )
    conn = psycopg2.connect(sync_url)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (lock_key,))
            (acquired,) = cur.fetchone()
            if not acquired:
                logger.debug(
                    "advisory_lock_not_acquired", pipeline_id=pipeline_id_str
                )
                conn.rollback()
                return

            # Lock acquired in this transaction — enqueue the pipeline
            _enqueue_pipeline_async(pipeline_id_str)
            conn.commit()
    finally:
        conn.close()


def _uuid_to_lock_key(pipeline_id_str: str) -> int:
    """Map a UUID to a positive bigint suitable for pg_try_advisory_xact_lock."""
    return uuid.UUID(pipeline_id_str).int & 0x7FFFFFFFFFFFFFFF


def _is_sla_breached(
    last_success_at: "datetime | None",
    sla_minutes: int,
    sla_notified_at: "datetime | None",
    now: "datetime",
) -> bool:
    """Return True if an SLA breach notification should be fired.

    Conditions:
    - last_success_at is None (never ran) OR now - last_success_at > sla_minutes
    - AND we haven't sent a notification within the last sla_minutes
    """
    window = timedelta(minutes=sla_minutes)
    overdue = last_success_at is None or (now - last_success_at) > window
    if not overdue:
        return False
    recently_notified = sla_notified_at is not None and (now - sla_notified_at) < window
    return not recently_notified


def _build_sla_payload(
    pipeline_id: str,
    sla_minutes: int,
    last_success_at: "datetime | None",
    now: "datetime",
) -> dict:
    return {
        "event": "sla_breach",
        "pipeline_id": pipeline_id,
        "sla_minutes": sla_minutes,
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
        "timestamp": now.isoformat(),
    }


def _check_sla_violations() -> None:
    """Run every 5 minutes: find SLA-breached pipelines and fire webhooks.

    Uses a synchronous psycopg2 connection (same pattern as _fire_with_advisory_lock)
    so it can run in the APScheduler background thread without an asyncio loop.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return

    try:
        import psycopg2
        sync_url = database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        conn = psycopg2.connect(sync_url)
        try:
            _run_sla_check(conn)
        finally:
            conn.close()
    except Exception as exc:
        logger.error("sla_check_failed", error=str(exc))


def _run_sla_check(conn) -> None:
    """Core SLA check logic given a psycopg2 connection."""
    import httpx

    now = datetime.now(tz=UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id::text, p.webhook_url, p.sla_minutes, p.sla_notified_at
            FROM pipelines p
            WHERE p.sla_minutes IS NOT NULL
              AND p.webhook_url IS NOT NULL
            """
        )
        pipelines = cur.fetchall()

        for pipeline_id, webhook_url, sla_minutes, sla_notified_at in pipelines:
            cur.execute(
                """
                SELECT max(finished_at)
                FROM job_runs
                WHERE pipeline_id = %s::uuid
                  AND status IN ('success', 'partial_success')
                """,
                (pipeline_id,),
            )
            (last_success_at,) = cur.fetchone()

            if not _is_sla_breached(last_success_at, sla_minutes, sla_notified_at, now):
                continue

            payload = _build_sla_payload(pipeline_id, sla_minutes, last_success_at, now)
            try:
                httpx.post(webhook_url, json=payload, timeout=5)
                logger.warning("sla_breach_fired", pipeline_id=pipeline_id)
            except Exception as exc:
                logger.warning("sla_webhook_post_failed", pipeline_id=pipeline_id, error=str(exc))

            cur.execute(
                "UPDATE pipelines SET sla_notified_at = %s WHERE id = %s::uuid",
                (now, pipeline_id),
            )
        conn.commit()


def _enqueue_pipeline_async(pipeline_id_str: str) -> None:
    """Submit a pipeline trigger to the async queue from a background thread."""
    loop = _loop
    if loop is None:
        # Fall back to asyncio.get_event_loop() for environments where
        # set_event_loop() was not called from lifespan (e.g. tests).
        # This may emit a DeprecationWarning on Python 3.10+ and will fail
        # on Python 3.12+ if no loop is running; callers should invoke
        # set_event_loop() at startup to avoid this path.
        import warnings
        try:
            loop = asyncio.get_event_loop()
            warnings.warn(
                "scheduler._loop is not set; falling back to asyncio.get_event_loop(). "
                "Call scheduler.set_event_loop(loop) at startup.",
                RuntimeWarning,
                stacklevel=2,
            )
        except RuntimeError:
            logger.warning("no_event_loop", pipeline_id=pipeline_id_str)
            return

    asyncio.run_coroutine_threadsafe(
        _async_trigger_pipeline(pipeline_id_str), loop
    ).result(timeout=30)


async def _async_trigger_pipeline(pipeline_id_str: str) -> None:
    """Async portion: load pipeline from DB and submit to queue."""
    from siphon.db import get_session_factory

    factory = get_session_factory()
    if factory is None:
        logger.warning("no_db_session_factory", pipeline_id=pipeline_id_str)
        return

    import json
    import uuid as uuid_mod
    from datetime import UTC, date, datetime

    from siphon.crypto import decrypt
    from siphon.models import ExtractRequest, Job
    from siphon.orm import Connection, JobRun, Pipeline
    from siphon.plugins.destinations import get as get_destination
    from siphon.plugins.sources import get as get_source

    pipeline_uuid = uuid_mod.UUID(pipeline_id_str)

    async with factory() as session:
        p = await session.get(Pipeline, pipeline_uuid)
        if p is None or p.dest_connection_id is None:
            logger.error("pipeline_not_found_or_missing_dest", pipeline_id=pipeline_id_str)
            return

        src_conn = await session.get(Connection, p.source_connection_id)
        dest_conn = await session.get(Connection, p.dest_connection_id)
        if src_conn is None or dest_conn is None:
            logger.error("pipeline_connection_not_found", pipeline_id=pipeline_id_str)
            return

        src_config = json.loads(decrypt(src_conn.encrypted_config))
        dest_config = json.loads(decrypt(dest_conn.encrypted_config))

        query = p.query
        if p.extraction_mode == "incremental" and p.incremental_key and p.last_watermark:
            from siphon.pipelines.watermark import inject_watermark
            dialect = src_config.get("connection", "").split("://")[0]
            query = inject_watermark(query, p.incremental_key, p.last_watermark, dialect)

        dest_path = p.destination_path.replace("{date}", str(date.today()))
        source_payload = {"type": "sql", "connection": src_config["connection"], "query": query}
        dest_payload = {
            "type": "s3_parquet",
            "path": dest_path,
            "endpoint": dest_config["endpoint"],
            "access_key": dest_config["access_key"],
            "secret_key": dest_config["secret_key"],
            "extraction_mode": p.extraction_mode,
            "partition_by": p.partition_by or "none",
        }

        try:
            req = ExtractRequest(**{"source": source_payload, "destination": dest_payload})
        except Exception as exc:
            logger.error("pipeline_invalid_config", pipeline_id=pipeline_id_str, error=str(exc))
            return

        has_dq = p.min_rows_expected is not None or p.max_rows_drop_pct is not None
        job = Job(
            job_id=str(uuid_mod.uuid4()),
            pipeline_id=pipeline_id_str,
            pipeline_schema_hash=p.last_schema_hash,
            pipeline_pii=p.pii_columns or None,
            pipeline_alert=(
                {"webhook_url": p.webhook_url, "alert_on": p.alert_on or ["failed"]}
                if p.webhook_url else None
            ),
            pipeline_dq={
                "min_rows_expected": p.min_rows_expected,
                "max_rows_drop_pct": p.max_rows_drop_pct,
                "prev_rows": None,
            } if has_dq else None,
            priority=p.priority,
            source_connection_id=str(src_conn.id),
            destination_path=dest_path,
        )

        now = datetime.now(tz=UTC)
        run = JobRun(
            job_id=job.job_id,
            pipeline_id=pipeline_uuid,
            status="queued",
            triggered_by="schedule",
            created_at=now,
            source_connection_id=src_conn.id,
            destination_path=dest_path,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        job.run_id = run.id

    try:
        get_source(req.source.type)
        get_destination(req.destination.type)
    except ValueError as exc:
        logger.error("pipeline_plugin_not_found", pipeline_id=pipeline_id_str, error=str(exc))
        return

    from siphon.main import queue
    await queue.submit(
        job,
        source_payload,
        dest_payload,
        max_concurrent=src_conn.max_concurrent_jobs,
    )
    logger.info("scheduled_trigger_queued", pipeline_id=pipeline_id_str, job_id=job.job_id)
