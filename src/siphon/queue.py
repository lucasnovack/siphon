# src/siphon/queue.py
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import HTTPException

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source
from siphon import worker

logger = logging.getLogger(__name__)


class JobQueue:
    """In-memory async job queue backed by a ThreadPoolExecutor.

    - submit()     → dispatch immediately if capacity available; HTTP 429 if full
    - drain()      → stop accepting new jobs; wait for active jobs to finish
    - get_job()    → retrieve job state by ID
    - is_full      → True when (active + queued) >= (max_workers + max_queue)
    - is_draining  → True after drain() has been called
    """

    def __init__(
        self,
        max_workers: int = int(os.getenv("SIPHON_MAX_WORKERS", "10")),
        max_queue: int = int(os.getenv("SIPHON_MAX_QUEUE", "50")),
        job_timeout: int = int(os.getenv("SIPHON_JOB_TIMEOUT", "3600")),
    ) -> None:
        self._max_workers = max_workers
        self._max_queue = max_queue
        self._job_timeout = job_timeout
        self._executor: ThreadPoolExecutor | None = None
        self._jobs: dict[str, Job] = {}
        self._active: int = 0
        self._queued: int = 0
        self._total: int = 0
        self._draining: bool = False

    def start(self) -> None:
        """Start the thread pool. Call once at service startup."""
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        logger.info(
            "JobQueue started: max_workers=%d, max_queue=%d",
            self._max_workers,
            self._max_queue,
        )

    @property
    def is_full(self) -> bool:
        """True when no more jobs can be accepted (active + queued at capacity)."""
        return (self._active + self._queued) >= (self._max_workers + self._max_queue)

    @property
    def is_draining(self) -> bool:
        """True after drain() has been called — no new jobs accepted."""
        return self._draining

    @property
    def stats(self) -> dict:
        """Queue statistics for /health endpoint."""
        return {
            "workers_active": self._active,
            "workers_max": self._max_workers,
            "jobs_queued": self._queued,
            "jobs_total": self._total,
        }

    def get_job(self, job_id: str) -> Job | None:
        """Retrieve job state by ID. Returns None if not found."""
        return self._jobs.get(job_id)

    async def submit(self, job: Job, source: Source, destination: Destination) -> None:
        """Submit a job for execution.

        Raises:
            HTTPException(503): service is draining (shutting down)
            HTTPException(429): queue is at capacity
        """
        if self._draining:
            raise HTTPException(status_code=503, detail="Service is shutting down")
        if self.is_full:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Queue is full. Max workers: {self._max_workers}, "
                    f"queued: {self._queued}. Retry later."
                ),
            )
        self._jobs[job.job_id] = job
        self._total += 1
        self._queued += 1
        asyncio.ensure_future(self._dispatch(job, source, destination))
        logger.debug(
            "Job %s submitted (active=%d, queued=%d)", job.job_id, self._active, self._queued
        )

    async def _dispatch(self, job: Job, source: Source, destination: Destination) -> None:
        """Internal: moves job from queued to active, runs it, then decrements active."""
        self._queued -= 1
        self._active += 1
        try:
            await worker.run_job(job, source, destination, self._executor, self._job_timeout)
        finally:
            self._active -= 1

    async def drain(self, timeout: float) -> None:
        """Stop accepting new jobs and wait for active jobs to finish.

        If timeout elapses before all jobs complete, remaining running jobs are
        marked as failed with status "Service shutdown before job completed".
        """
        self._draining = True
        logger.info("Draining queue (timeout=%.1fs, active=%d)", timeout, self._active)

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        while self._active > 0:
            remaining = deadline - loop.time()
            if remaining <= 0:
                # Timeout — mark all still-running jobs as failed
                aborted = 0
                for j in self._jobs.values():
                    if j.status in ("queued", "running"):
                        j.status = "failed"
                        j.error = "Service shutdown before job completed"
                        aborted += 1
                logger.warning("Drain timeout: %d job(s) aborted", aborted)
                break
            await asyncio.sleep(min(0.2, remaining))

        drained = sum(1 for j in self._jobs.values() if j.status in ("success", "failed"))
        logger.info("Graceful shutdown complete. %d jobs drained.", drained)

        if self._executor:
            self._executor.shutdown(wait=False)
