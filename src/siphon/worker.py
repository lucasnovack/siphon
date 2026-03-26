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
    """Synchronous extraction loop — runs in a ThreadPoolExecutor thread.

    Iterates source.extract_batches() and calls destination.write() for each chunk.
    Returns (rows_read, rows_written).
    """
    rows_read = 0
    rows_written = 0
    for i, batch in enumerate(source.extract_batches()):
        rows_read += batch.num_rows
        written = destination.write(batch, is_first_chunk=(i == 0))
        rows_written += written
    return rows_read, rows_written


async def run_job(
    job: Job,
    source: Source,
    destination: Destination,
    executor: ThreadPoolExecutor,
    timeout: int,
) -> None:
    """Execute a single extraction job. Updates job state in-place.

    Runs the blocking extraction loop in a thread pool to avoid blocking the
    event loop. Wraps execution in asyncio.wait_for for timeout enforcement.

    Known limitation: asyncio.wait_for cancels the coroutine but the underlying
    thread continues until I/O returns. The job is marked failed immediately,
    freeing the worker slot. The thread eventually exits when the DB connection
    times out at the network level.
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
        # Collect failed files from sources that support partial success (e.g. SFTPSource)
        failed_files = list(getattr(source, "failed_files", []))
        job.failed_files = failed_files
        if rows_read != rows_written:
            job.status = "failed"
            job.error = (
                f"Row count mismatch: {rows_read} rows read but {rows_written} rows written"
            )
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
            logger.warning(
                "Job %s partial success: %d files failed", job.job_id, len(failed_files)
            )
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
