# src/siphon/worker.py
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source

logger = logging.getLogger(__name__)


def _sync_extract_and_write(
    job: Job,
    source: Source,
    destination: Destination,
) -> tuple[int, int]:
    rows_read = 0
    rows_written = 0
    for i, batch in enumerate(source.extract_batches()):
        rows_read += batch.num_rows
        written = destination.write(batch, is_first_chunk=(i == 0))
        rows_written += written
    return rows_read, rows_written


async def _persist_job_run(job: Job, db_factory) -> None:
    """Write a JobRun row to PostgreSQL. Errors are logged but never propagated."""
    try:
        from siphon.orm import JobRun

        duration_ms = None
        if job.started_at and job.finished_at:
            duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

        run = JobRun(
            job_id=job.job_id,
            status=job.status,
            rows_read=job.rows_read,
            rows_written=job.rows_written,
            duration_ms=duration_ms,
            error=job.error,
            schema_changed=False,
            started_at=job.started_at,
            finished_at=job.finished_at,
            created_at=datetime.now(tz=UTC),
        )
        async with db_factory() as session:
            session.add(run)
            await session.commit()
    except Exception as exc:
        logger.warning("Failed to persist job_run for %s: %s", job.job_id, exc)


async def run_job(
    source: Source,
    destination: Destination,
    job: Job,
    executor: ThreadPoolExecutor,
    timeout: int,
    db_factory=None,
) -> None:
    """Execute a single extraction job. Updates job state in-place.

    db_factory: optional async_sessionmaker from db.py. When provided, persists
    a JobRun row after the job completes (success or failure).
    """
    loop = asyncio.get_running_loop()
    job.status = "running"
    job.started_at = datetime.now(tz=UTC)
    logger.info("Job %s started", job.job_id)

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
            job.error = f"Row count mismatch: {rows_read} rows read but {rows_written} rows written"
            job.logs.append(f"ERROR: {job.error}")
            logger.error(
                "Job %s row count mismatch: read=%d written=%d",
                job.job_id, rows_read, rows_written,
            )
            return
        if failed_files:
            job.status = "partial_success"
            job.logs.append(
                f"Partial success: {rows_read} rows written, {len(failed_files)} files failed"
            )
            logger.warning("Job %s partial success: %d files failed", job.job_id, len(failed_files))
        else:
            job.status = "success"
            job.logs.append(f"Completed: {rows_read} rows read, {rows_written} rows written")
            logger.info("Job %s succeeded: %d rows", job.job_id, rows_read)

    except TimeoutError:
        job.status = "failed"
        job.error = f"Job exceeded timeout of {timeout}s"
        job.logs.append(f"ERROR: {job.error}")
        logger.error("Job %s timed out after %ss", job.job_id, timeout)

    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.logs.append(f"ERROR: {job.error}")
        logger.error("Job %s failed: %s", job.job_id, exc)

    finally:
        job.finished_at = datetime.now(tz=UTC)
        if db_factory is not None:
            await _persist_job_run(job, db_factory)
