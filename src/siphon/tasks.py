# src/siphon/tasks.py
import asyncio
import dataclasses
from datetime import datetime

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
    """Celery task: reconstruct Job + plugins, call run_job(), persist result."""
    # NOTE: asyncio.new_event_loop() is used here because Celery tasks are synchronous.
    # This is only compatible with Celery's prefork (default) worker pool.
    # Do NOT run with gevent or eventlet pools — they monkey-patch asyncio and will deadlock.
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

    from siphon.db import get_session_factory

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            _run_job_async(source, destination, job, get_session_factory())
        )
    except Exception as exc:
        logger.error("Task failed, retrying", error=str(exc))
        raise self.retry(exc=exc) from exc
    finally:
        loop.close()


async def _run_job_async(source, destination, job, db_factory) -> None:
    """Async wrapper so run_job (which is async) can be called from a sync Celery task."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    max_workers = int(os.getenv("SIPHON_MAX_WORKERS", "1"))
    job_timeout = int(os.getenv("SIPHON_JOB_TIMEOUT", "3600"))

    from siphon import worker

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        await worker.run_job(source, destination, job, executor, job_timeout, db_factory=db_factory)
