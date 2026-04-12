# src/siphon/queue.py
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException

from siphon import worker
from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source

logger = structlog.get_logger()

_PRIORITY = {"high": 0, "normal": 1, "low": 2}


class JobQueue:
    """Async job queue with priority ordering and per-connection concurrency limits.

    - submit()     → enqueue job; raises 429 if full, 503 if draining
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
        job_ttl: int = int(os.getenv("SIPHON_JOB_TTL_SECONDS", "3600")),
    ) -> None:
        self._max_workers = max_workers
        self._max_queue = max_queue
        self._job_timeout = job_timeout
        self._job_ttl = job_ttl
        self._executor: ThreadPoolExecutor | None = None
        self._jobs: dict[str, Job] = {}
        self._active: int = 0
        self._queued: int = 0
        self._total: int = 0
        self._draining: bool = False
        # Priority queue items: (priority_int, counter, job_id)
        self._pqueue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        # Maps job_id → (job, source, destination, max_concurrent)
        self._pending: dict[str, tuple[Job, Source, Destination, int]] = {}
        # Active job count per source_connection_id
        self._active_by_connection: dict[str, int] = {}
        self._pqueue_counter: int = 0
        self._loops_started: bool = False

    def start(self) -> None:
        """Start the thread pool. Call once at service startup (or on lifespan restart)."""
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        # Reset state so that _ensure_loops_started() will re-schedule background tasks
        # on the current event loop (important when lifespan is restarted in tests).
        self._draining = False
        self._loops_started = False
        self._pqueue = asyncio.PriorityQueue()
        self._pending = {}
        self._active_by_connection = {}
        self._pqueue_counter = 0
        logger.info(
            "JobQueue started: max_workers=%d, max_queue=%d, job_ttl=%ds",
            self._max_workers,
            self._max_queue,
            self._job_ttl,
        )

    def _ensure_loops_started(self) -> None:
        """Start background coroutines on the running event loop (called from async context)."""
        if not self._loops_started:
            self._loops_started = True
            asyncio.ensure_future(self._evict_loop())
            asyncio.ensure_future(self._dispatcher_loop())

    @property
    def is_full(self) -> bool:
        return (self._active + self._queued) >= (self._max_workers + self._max_queue)

    @property
    def is_draining(self) -> bool:
        return self._draining

    @property
    def stats(self) -> dict:
        return {
            "workers_active": self._active,
            "workers_max": self._max_workers,
            "jobs_queued": self._queued,
            "jobs_total": self._total,
        }

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _evict_expired(self) -> None:
        now = datetime.now(tz=UTC)
        terminal = ("success", "failed", "partial_success")
        to_remove = [
            jid
            for jid, job in list(self._jobs.items())
            if job.status in terminal
            and job.finished_at is not None
            and (now - job.finished_at).total_seconds() > self._job_ttl
        ]
        for jid in to_remove:
            del self._jobs[jid]
        if to_remove:
            logger.debug("Evicted %d expired job(s) from memory", len(to_remove))

    async def _evict_loop(self) -> None:
        while not self._draining:
            await asyncio.sleep(300)
            self._evict_expired()

    async def _dispatcher_loop(self) -> None:
        """Pull jobs from the priority queue and dispatch when capacity is available."""
        while not self._draining:
            if self._active >= self._max_workers:
                await asyncio.sleep(0.05)
                continue

            try:
                priority, counter, job_id = self._pqueue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)
                continue

            entry = self._pending.pop(job_id, None)
            if entry is None:
                # Job was cancelled or already dispatched
                continue

            job, source, destination, max_concurrent = entry
            conn_id = job.source_connection_id

            # Check per-connection concurrency limit
            if conn_id and self._active_by_connection.get(conn_id, 0) >= max_concurrent:
                # Requeue with same priority and counter (preserves relative order)
                await self._pqueue.put((priority, counter, job_id))
                self._pending[job_id] = entry
                await asyncio.sleep(0.1)
                continue

            self._queued -= 1
            self._active += 1
            if conn_id:
                self._active_by_connection[conn_id] = (
                    self._active_by_connection.get(conn_id, 0) + 1
                )
            asyncio.ensure_future(self._dispatch(job, source, destination, conn_id))

    async def submit(
        self,
        job: Job,
        source: Source,
        destination: Destination,
        max_concurrent: int = 2,
    ) -> None:
        """Enqueue a job for execution.

        Raises:
            HTTPException(503): service is draining (shutting down)
            HTTPException(429): queue is at capacity
        """
        self._ensure_loops_started()
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
        priority_int = _PRIORITY.get(job.priority, 1)
        counter = self._pqueue_counter
        self._pqueue_counter += 1

        self._jobs[job.job_id] = job
        self._pending[job.job_id] = (job, source, destination, max_concurrent)
        self._total += 1
        self._queued += 1

        await self._pqueue.put((priority_int, counter, job.job_id))
        logger.debug(
            "Job %s enqueued (priority=%s, active=%d, queued=%d)",
            job.job_id, job.priority, self._active, self._queued,
        )

    async def _dispatch(
        self,
        job: Job,
        source: Source,
        destination: Destination,
        conn_id: str | None,
    ) -> None:
        try:
            from siphon.db import get_session_factory
            await worker.run_job(
                source, destination, job, self._executor, self._job_timeout,
                db_factory=get_session_factory(),
            )
        finally:
            self._active -= 1
            if conn_id:
                self._active_by_connection[conn_id] = max(
                    0, self._active_by_connection.get(conn_id, 1) - 1
                )

    async def drain(self, timeout: float) -> None:
        """Stop accepting new jobs and wait for active jobs to finish."""
        self._draining = True
        logger.info("Draining queue (timeout=%.1fs, active=%d)", timeout, self._active)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while self._active > 0:
            remaining = deadline - loop.time()
            if remaining <= 0:
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
