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

import logging
import os
import uuid

logger = logging.getLogger(__name__)

_scheduler = None  # module-level singleton, started in lifespan


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

        jobstore = SQLAlchemyJobStore(url=database_url)
        _scheduler = BackgroundScheduler(jobstores={"default": jobstore})
        _scheduler.start()
        logger.info("Scheduler started with PostgreSQL jobstore")
    except Exception as exc:
        logger.error("Failed to start scheduler: %s", exc)
        _scheduler = None


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully.  Called from ``main.py`` lifespan on shutdown."""
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        except Exception as exc:
            logger.warning("Error stopping scheduler: %s", exc)
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
            logger.info("Rescheduled job %s cron=%s", job_id, cron)
        else:
            _scheduler.add_job(
                _fire_pipeline,
                trigger="cron",
                id=job_id,
                args=[job_id],
                replace_existing=True,
                **trigger_kwargs,
            )
            logger.info("Scheduled job %s cron=%s", job_id, cron)
    except Exception as exc:
        logger.error("sync_schedule failed for pipeline %s: %s", pipeline_id, exc)


async def remove_schedule(pipeline_id: uuid.UUID) -> None:
    """Remove the APScheduler job for *pipeline_id*, if any."""
    if _scheduler is None:
        return
    _remove_job(str(pipeline_id))


# ── Internal helpers ──────────────────────────────────────────────────────────


def _remove_job(job_id: str) -> None:
    try:
        _scheduler.remove_job(job_id)
        logger.info("Removed scheduled job %s", job_id)
    except Exception:
        pass  # job may not exist


def _parse_cron(cron: str) -> dict:
    """Parse a 5-field cron string into APScheduler CronTrigger kwargs."""
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (expected 5 fields): {cron!r}")
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


def _fire_pipeline(pipeline_id_str: str) -> None:
    """Scheduled callback: acquire advisory lock then enqueue a pipeline trigger.

    Runs in the APScheduler background thread.  Uses a synchronous DB connection
    (psycopg2 / pg8000) for the advisory lock check, then submits to the async
    queue via ``asyncio.run_coroutine_threadsafe``.
    """
    try:
        _fire_with_advisory_lock(pipeline_id_str)
    except Exception as exc:
        logger.error("Scheduled fire failed for pipeline %s: %s", pipeline_id_str, exc)


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
                    "Advisory lock not acquired for pipeline %s — skipping", pipeline_id_str
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


def _enqueue_pipeline_async(pipeline_id_str: str) -> None:
    """Submit a pipeline trigger to the async queue from a background thread."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        logger.warning("No event loop available to enqueue pipeline %s", pipeline_id_str)
        return

    asyncio.run_coroutine_threadsafe(
        _async_trigger_pipeline(pipeline_id_str), loop
    ).result(timeout=30)


async def _async_trigger_pipeline(pipeline_id_str: str) -> None:
    """Async portion: load pipeline from DB and submit to queue."""
    from siphon.db import get_session_factory

    factory = get_session_factory()
    if factory is None:
        logger.warning("No DB session factory; cannot trigger pipeline %s", pipeline_id_str)
        return

    import uuid as uuid_mod

    from sqlalchemy import select

    from siphon.models import ExtractRequest, Job
    from siphon.orm import Connection, JobRun, Pipeline
    from siphon.plugins.destinations import get as get_destination
    from siphon.plugins.sources import get as get_source

    import json
    from datetime import UTC, datetime, date

    from siphon.crypto import decrypt

    pipeline_uuid = uuid_mod.UUID(pipeline_id_str)

    async with factory() as session:
        p = await session.get(Pipeline, pipeline_uuid)
        if p is None or p.dest_connection_id is None:
            logger.error("Scheduled pipeline %s not found or missing dest_connection", pipeline_id_str)
            return

        src_conn = await session.get(Connection, p.source_connection_id)
        dest_conn = await session.get(Connection, p.dest_connection_id)
        if src_conn is None or dest_conn is None:
            logger.error("Scheduled pipeline %s: connection not found", pipeline_id_str)
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
        }

        try:
            req = ExtractRequest(**{"source": source_payload, "destination": dest_payload})
        except Exception as exc:
            logger.error("Scheduled pipeline %s invalid config: %s", pipeline_id_str, exc)
            return

        has_dq = p.min_rows_expected is not None or p.max_rows_drop_pct is not None
        job = Job(
            job_id=str(uuid_mod.uuid4()),
            pipeline_id=pipeline_id_str,
            pipeline_schema_hash=p.last_schema_hash,
            pipeline_dq={
                "min_rows_expected": p.min_rows_expected,
                "max_rows_drop_pct": p.max_rows_drop_pct,
                "prev_rows": None,
            } if has_dq else None,
        )

        now = datetime.now(tz=UTC)
        run = JobRun(
            job_id=job.job_id,
            pipeline_id=pipeline_uuid,
            status="queued",
            triggered_by="schedule",
            created_at=now,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        job.run_id = run.id

    try:
        source_cls = get_source(req.source.type)
        dest_cls = get_destination(req.destination.type)
    except ValueError as exc:
        logger.error("Scheduled pipeline %s: %s", pipeline_id_str, exc)
        return

    source = source_cls(**req.source.model_dump(exclude={"type"}))
    destination = dest_cls(**req.destination.model_dump(exclude={"type"}))

    from siphon.main import queue
    await queue.submit(job, source, destination)
    logger.info("Scheduled trigger queued: pipeline=%s job=%s", pipeline_id_str, job.job_id)
