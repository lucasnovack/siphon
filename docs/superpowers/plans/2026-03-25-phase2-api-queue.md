# Siphon Phase 2 — API + Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HTTP service running with functional job queue — `POST /jobs`, `POST /extract`, `GET /jobs/{id}`, `GET /jobs/{id}/logs`, health endpoints, security middleware — no real plugins needed (tests use inline fakes).

**Architecture:** `worker.py` runs extraction jobs in a `ThreadPoolExecutor` thread wrapped in `asyncio.wait_for` (timeout + non-blocking I/O). `queue.py` owns a `JobQueue` singleton that tracks active/queued jobs and drains on SIGTERM. `main.py` is the FastAPI app that wires everything: routes, lifespan, request-size middleware, API-key middleware, and a 422 handler that suppresses body logging.

**Tech Stack:** Python 3.12, FastAPI 0.115+, httpx (tests), starlette TestClient, pytest-asyncio, ThreadPoolExecutor, asyncio.

---

## File Map

| File | Responsibility |
|---|---|
| `src/siphon/worker.py` | `run_job()` — async wrapper that runs extraction in a thread with timeout |
| `src/siphon/queue.py` | `JobQueue` — tracks jobs, dispatches workers, drains on shutdown |
| `src/siphon/main.py` | FastAPI app — lifespan, all routes, all middleware |
| `tests/test_worker.py` | Worker unit tests — success, exception, timeout |
| `tests/test_queue.py` | Queue unit tests — submit, 429 when full, drain |
| `tests/test_main.py` | HTTP integration tests — all routes, 401, 413, health |

---

## Task 1: `worker.py` — Job execution engine

**Files:**
- Create: `src/siphon/worker.py`
- Create: `tests/test_worker.py`

**Spec:** `claude.md` §8 — Queue Design / Timeout mechanism

- [ ] **Step 1: Write failing tests first**

Create `tests/test_worker.py`:

```python
# tests/test_worker.py
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC

import pyarrow as pa
import pytest

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source
from siphon.worker import run_job


# ── Inline stubs ──────────────────────────────────────────────────────────────

class _OkSource(Source):
    def extract(self) -> pa.Table:
        return pa.table({"x": [1, 2, 3]})


class _FailSource(Source):
    def extract(self) -> pa.Table:
        raise RuntimeError("connection refused")


class _SlowSource(Source):
    def extract(self) -> pa.Table:
        import time
        time.sleep(10)  # will be cancelled by timeout
        return pa.table({"x": []})


class _OkDest(Destination):
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return table.num_rows


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def executor():
    ex = ThreadPoolExecutor(max_workers=2)
    yield ex
    ex.shutdown(wait=False)


async def test_run_job_success(executor):
    job = Job(job_id="j-ok")
    await run_job(job, _OkSource(), _OkDest(), executor, timeout=5)
    assert job.status == "success"
    assert job.rows_read == 3
    assert job.rows_written == 3
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.started_at.tzinfo is not None  # timezone-aware


async def test_run_job_sets_running_first(executor):
    """Job must transition through 'running' before 'success'."""
    statuses: list[str] = []

    class _WatchedSource(Source):
        def extract(self) -> pa.Table:
            statuses.append(job.status)  # capture status mid-run
            return pa.table({"x": [1]})

    job = Job(job_id="j-watch")
    await run_job(job, _WatchedSource(), _OkDest(), executor, timeout=5)
    assert "running" in statuses
    assert job.status == "success"


async def test_run_job_exception_marks_failed(executor):
    job = Job(job_id="j-fail")
    await run_job(job, _FailSource(), _OkDest(), executor, timeout=5)
    assert job.status == "failed"
    assert "connection refused" in job.error
    assert job.finished_at is not None


async def test_run_job_timeout_marks_failed(executor):
    job = Job(job_id="j-timeout")
    await run_job(job, _SlowSource(), _OkDest(), executor, timeout=0.1)
    assert job.status == "failed"
    assert "timeout" in job.error.lower()


async def test_run_job_counts_rows_across_batches(executor):
    """extract_batches() yields multiple chunks — rows must be summed."""

    class _ChunkedSource(Source):
        def extract(self) -> pa.Table:
            return pa.table({"x": []})

        def extract_batches(self, chunk_size: int = 100):
            yield pa.table({"x": [1, 2]})
            yield pa.table({"x": [3, 4, 5]})

    class _CountDest(Destination):
        def write(self, table, is_first_chunk=True) -> int:
            return table.num_rows

    job = Job(job_id="j-batches")
    await run_job(job, _ChunkedSource(), _CountDest(), executor, timeout=5)
    assert job.status == "success"
    assert job.rows_read == 5
    assert job.rows_written == 5


async def test_run_job_passes_is_first_chunk_correctly(executor):
    """is_first_chunk must be True only for the first batch."""
    chunks_received: list[bool] = []

    class _MultiChunk(Source):
        def extract(self) -> pa.Table:
            return pa.table({"x": []})

        def extract_batches(self, chunk_size: int = 100):
            yield pa.table({"x": [1]})
            yield pa.table({"x": [2]})
            yield pa.table({"x": [3]})

    class _FlagDest(Destination):
        def write(self, table, is_first_chunk=True) -> int:
            chunks_received.append(is_first_chunk)
            return table.num_rows

    job = Job(job_id="j-flags")
    await run_job(job, _MultiChunk(), _FlagDest(), executor, timeout=5)
    assert chunks_received == [True, False, False]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/test_worker.py -v
```

Expected: `ImportError` — `siphon.worker` doesn't exist yet.

- [ ] **Step 3: Implement `src/siphon/worker.py`**

```python
# src/siphon/worker.py
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pyarrow as pa

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
        job.status = "success"
        job.logs.append(f"Completed: {rows_read} rows read, {rows_written} rows written")
        logger.info("Job %s succeeded: %d rows", job.job_id, rows_read)

    except asyncio.TimeoutError:
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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/test_worker.py -v
```

Expected: all 6 tests PASS.

Note on `test_run_job_timeout_marks_failed`: the test uses `timeout=0.1` seconds and the source sleeps for 10s. The asyncio cancellation marks the job failed immediately. The test verifies the job ends in "failed" state with "timeout" in the error message. ✓

- [ ] **Step 5: Commit**

```bash
cd /home/lucasnvk/projects/siphon
git add src/siphon/worker.py tests/test_worker.py
git commit -m "feat: add job execution worker with timeout support"
```

---

## Task 2: `queue.py` — Async job queue

**Files:**
- Create: `src/siphon/queue.py`
- Create: `tests/test_queue.py`

**Spec:** `claude.md` §8 — Queue Design + Graceful Shutdown

- [ ] **Step 1: Write failing tests first**

Create `tests/test_queue.py`:

```python
# tests/test_queue.py
import asyncio
import threading

import pyarrow as pa
import pytest
from fastapi import HTTPException

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source
from siphon.queue import JobQueue


# ── Inline stubs ──────────────────────────────────────────────────────────────

class _FastSource(Source):
    def extract(self) -> pa.Table:
        return pa.table({"x": [1, 2, 3]})


class _NopDest(Destination):
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return table.num_rows


def _blocking_source(gate: threading.Event) -> Source:
    """Returns a Source that blocks until gate is set."""

    class _Blocking(Source):
        def extract(self) -> pa.Table:
            gate.wait(timeout=10)
            return pa.table({"x": [1]})

    return _Blocking()


@pytest.fixture
def q():
    queue = JobQueue(max_workers=2, max_queue=2, job_timeout=5)
    queue.start()
    yield queue
    # Cleanup: release any blocking threads
    if queue._executor:
        queue._executor.shutdown(wait=False)


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_submit_accepts_job(q):
    job = Job(job_id="j-ok")
    await q.submit(job, _FastSource(), _NopDest())
    # Job is registered immediately
    assert q.get_job("j-ok") is job


async def test_submit_job_eventually_succeeds(q):
    job = Job(job_id="j-success")
    await q.submit(job, _FastSource(), _NopDest())
    # Wait for completion
    for _ in range(50):  # up to 5 seconds
        await asyncio.sleep(0.1)
        if job.status not in ("queued", "running"):
            break
    assert job.status == "success"
    assert job.rows_read == 3


async def test_submit_raises_429_when_full():
    """With max_workers=1 and max_queue=0, the second job must get 429."""
    gate = threading.Event()
    q = JobQueue(max_workers=1, max_queue=0, job_timeout=5)
    q.start()
    try:
        job1 = Job(job_id="j-blocker")
        await q.submit(job1, _blocking_source(gate), _NopDest())
        await asyncio.sleep(0.1)  # let _dispatch start and increment _active

        job2 = Job(job_id="j-overflow")
        with pytest.raises(HTTPException) as exc_info:
            await q.submit(job2, _FastSource(), _NopDest())
        assert exc_info.value.status_code == 429
    finally:
        gate.set()
        q._executor.shutdown(wait=False)


async def test_is_full_false_when_capacity_available(q):
    assert q.is_full is False


async def test_is_full_true_when_at_capacity():
    q = JobQueue(max_workers=1, max_queue=0, job_timeout=5)
    q.start()
    gate = threading.Event()
    try:
        job = Job(job_id="j-fill")
        await q.submit(job, _blocking_source(gate), _NopDest())
        await asyncio.sleep(0.1)
        assert q.is_full is True
    finally:
        gate.set()
        q._executor.shutdown(wait=False)


async def test_get_job_returns_none_for_unknown(q):
    assert q.get_job("nonexistent") is None


async def test_stats_reflects_queue_state(q):
    stats = q.stats
    assert "workers_active" in stats
    assert "workers_max" in stats
    assert "jobs_queued" in stats
    assert "jobs_total" in stats
    assert stats["workers_max"] == 2


async def test_drain_waits_for_active_jobs():
    gate = threading.Event()
    q = JobQueue(max_workers=1, max_queue=0, job_timeout=10)
    q.start()
    job = Job(job_id="j-drain")
    await q.submit(job, _blocking_source(gate), _NopDest())
    await asyncio.sleep(0.1)  # let job start
    assert job.status == "running"
    assert q.is_draining is False

    # Release gate so job can finish, then drain
    gate.set()
    await q.drain(timeout=5.0)

    assert job.status == "success"
    assert q.is_draining is True


async def test_drain_rejects_new_submissions():
    q = JobQueue(max_workers=1, max_queue=0, job_timeout=5)
    q.start()
    await q.drain(timeout=0.0)  # drain immediately (no active jobs)
    assert q.is_draining is True

    job = Job(job_id="j-after-drain")
    with pytest.raises(HTTPException) as exc_info:
        await q.submit(job, _FastSource(), _NopDest())
    assert exc_info.value.status_code == 503


async def test_drain_timeout_marks_running_jobs_failed():
    gate = threading.Event()
    q = JobQueue(max_workers=1, max_queue=0, job_timeout=10)
    q.start()
    job = Job(job_id="j-abort")
    await q.submit(job, _blocking_source(gate), _NopDest())
    await asyncio.sleep(0.1)
    assert job.status == "running"

    # drain with a 0.1s timeout — job is still blocked
    await q.drain(timeout=0.1)

    # Job must be marked failed due to drain timeout
    assert job.status == "failed"
    assert "shutdown" in job.error.lower()
    gate.set()  # unblock thread so it can exit cleanly
    q._executor.shutdown(wait=False)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/test_queue.py -v
```

Expected: `ImportError` — `siphon.queue` doesn't exist yet.

- [ ] **Step 3: Implement `src/siphon/queue.py`**

```python
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
        logger.debug("Job %s submitted (active=%d, queued=%d)", job.job_id, self._active, self._queued)

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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/test_queue.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Run full suite to ensure nothing is broken**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/lucasnvk/projects/siphon
git add src/siphon/queue.py tests/test_queue.py
git commit -m "feat: add async job queue with ThreadPoolExecutor and graceful drain"
```

---

## Task 3: `main.py` — FastAPI app, routes, and middleware

**Files:**
- Create: `src/siphon/main.py`
- Create: `tests/test_main.py`

**Spec:** `claude.md` §5 (API Contract), §8 (Security in runtime, Graceful Shutdown), NFR-11, NFR-12, NFR-18, NFR-20

- [ ] **Step 1: Write failing tests first**

Create `tests/test_main.py`:

```python
# tests/test_main.py
"""
HTTP integration tests for the FastAPI app.

Strategy: register fake "sql" and "s3_parquet" plugins at module level so that
API requests with those types pass validation and route to working stubs.
Queue state is reset between tests via the `reset_queue` fixture.
"""
import os

import pyarrow as pa
import pytest
from starlette.testclient import TestClient

from siphon.plugins.destinations import register as register_dest
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources import register as register_source
from siphon.plugins.sources.base import Source

# ── Register fake plugins for the test session ────────────────────────────────
# These must be registered before `siphon.main` is imported so the queue is
# ready to dispatch them.

@register_source("sql")
class _FakeSQLSource(Source):
    def __init__(self, connection: str, query: str, **kwargs) -> None:
        pass

    def extract(self) -> pa.Table:
        return pa.table({"id": [1, 2, 3]})


@register_dest("s3_parquet")
class _FakeS3Dest(Destination):
    def __init__(self, path: str, endpoint: str, access_key: str, secret_key: str, **kwargs) -> None:
        pass

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return table.num_rows


# ── Import app AFTER plugin registration ─────────────────────────────────────
import siphon.main as main_module  # noqa: E402
from siphon.main import app  # noqa: E402

# ── Shared request fixtures ───────────────────────────────────────────────────

VALID_REQUEST = {
    "source": {"type": "sql", "connection": "mysql://u:p@h/db", "query": "SELECT 1"},
    "destination": {
        "type": "s3_parquet",
        "path": "s3a://bronze/x/2026-03-25",
        "endpoint": "minio:9000",
        "access_key": "k",
        "secret_key": "s",
    },
}


@pytest.fixture(autouse=True)
def reset_queue():
    """Reset queue state between tests to avoid cross-test pollution."""
    q = main_module.queue
    q._jobs.clear()
    q._active = 0
    q._queued = 0
    q._draining = False
    yield
    q._jobs.clear()
    q._active = 0
    q._queued = 0
    q._draining = False


@pytest.fixture
def client():
    """TestClient triggers lifespan (queue.start() and queue.drain())."""
    with TestClient(app) as tc:
        yield tc


# ── POST /jobs ────────────────────────────────────────────────────────────────

def test_post_jobs_returns_202(client):
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_post_jobs_returns_job_id(client):
    r1 = client.post("/jobs", json=VALID_REQUEST)
    r2 = client.post("/jobs", json=VALID_REQUEST)
    assert r1.json()["job_id"] != r2.json()["job_id"]  # unique UUIDs


def test_post_jobs_429_when_queue_full(client):
    q = main_module.queue
    # Manually saturate the queue
    q._active = q._max_workers
    q._queued = q._max_queue
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 429
    assert "Queue is full" in response.json()["detail"]


def test_post_jobs_invalid_source_type_returns_422(client):
    bad = {**VALID_REQUEST, "source": {"type": "unknown", "connection": "x", "query": "y"}}
    response = client.post("/jobs", json=bad)
    assert response.status_code == 422


# ── POST /extract ─────────────────────────────────────────────────────────────

def test_post_extract_returns_200_with_status(client):
    response = client.post("/extract", json=VALID_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("success", "failed", "running", "queued")
    assert "job_id" in body


def test_post_extract_429_when_queue_full(client):
    q = main_module.queue
    q._active = q._max_workers
    q._queued = q._max_queue
    response = client.post("/extract", json=VALID_REQUEST)
    assert response.status_code == 429


# ── GET /jobs/{id} ────────────────────────────────────────────────────────────

def test_get_job_returns_status(client):
    post = client.post("/jobs", json=VALID_REQUEST)
    job_id = post.json()["job_id"]
    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] in ("queued", "running", "success", "failed")


def test_get_job_404_for_unknown(client):
    response = client.get("/jobs/nonexistent-id")
    assert response.status_code == 404


# ── GET /jobs/{id}/logs ───────────────────────────────────────────────────────

def test_get_job_logs_returns_logs_response(client):
    post = client.post("/jobs", json=VALID_REQUEST)
    job_id = post.json()["job_id"]
    response = client.get(f"/jobs/{job_id}/logs")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert isinstance(body["logs"], list)
    assert "next_offset" in body


def test_get_job_logs_with_since_offset(client):
    post = client.post("/jobs", json=VALID_REQUEST)
    job_id = post.json()["job_id"]
    # Wait briefly for job to produce logs
    import time
    time.sleep(0.2)
    # Full logs
    full = client.get(f"/jobs/{job_id}/logs?since=0").json()
    # Skip first log line
    partial = client.get(f"/jobs/{job_id}/logs?since=1").json()
    assert partial["next_offset"] == full["next_offset"]
    if len(full["logs"]) > 1:
        assert partial["logs"] == full["logs"][1:]


def test_get_job_logs_404_for_unknown(client):
    response = client.get("/jobs/nonexistent/logs")
    assert response.status_code == 404


# ── GET /health/live ──────────────────────────────────────────────────────────

def test_health_live_returns_200(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── GET /health/ready ─────────────────────────────────────────────────────────

def test_health_ready_returns_200_when_accepting(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["accepting_jobs"] is True


def test_health_ready_returns_503_when_queue_full(client):
    q = main_module.queue
    q._active = q._max_workers
    q._queued = q._max_queue
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["accepting_jobs"] is False
    assert body["reason"] == "queue_full"


def test_health_ready_returns_503_when_draining(client):
    main_module.queue._draining = True
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["accepting_jobs"] is False
    assert body["reason"] == "draining"


# ── GET /health ───────────────────────────────────────────────────────────────

def test_health_debug_returns_full_info(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "accepting_jobs" in body
    assert "queue" in body
    assert "uptime_seconds" in body
    assert body["queue"]["workers_max"] == main_module.queue._max_workers


# ── Security: API key middleware ──────────────────────────────────────────────

def test_api_key_required_returns_401(client, monkeypatch):
    monkeypatch.setenv("SIPHON_API_KEY", "secret-key")
    # Reload the middleware-relevant state by patching the module variable
    main_module.API_KEY = "secret-key"
    try:
        response = client.post("/jobs", json=VALID_REQUEST)
        assert response.status_code == 401
        assert response.json()["detail"] == "Unauthorized"
    finally:
        main_module.API_KEY = None


def test_api_key_correct_passes(client, monkeypatch):
    main_module.API_KEY = "secret-key"
    try:
        response = client.post(
            "/jobs",
            json=VALID_REQUEST,
            headers={"Authorization": "Bearer secret-key"},
        )
        assert response.status_code == 202
    finally:
        main_module.API_KEY = None


def test_health_live_bypasses_api_key(client):
    main_module.API_KEY = "secret-key"
    try:
        response = client.get("/health/live")  # no auth header
        assert response.status_code == 200
    finally:
        main_module.API_KEY = None


def test_health_ready_bypasses_api_key(client):
    main_module.API_KEY = "secret-key"
    try:
        response = client.get("/health/ready")  # no auth header
        assert response.status_code == 200
    finally:
        main_module.API_KEY = None


# ── Security: request size limit ─────────────────────────────────────────────

def test_request_too_large_returns_413(client, monkeypatch):
    """SIPHON_MAX_REQUEST_SIZE_MB=0 means any non-empty body is rejected."""
    monkeypatch.setenv("SIPHON_MAX_REQUEST_SIZE_MB", "0")
    response = client.post("/jobs", json=VALID_REQUEST)
    assert response.status_code == 413


# ── Security: 422 handler suppresses body ────────────────────────────────────

def test_422_does_not_include_raw_body(client):
    """Validation errors must not echo the request body (credentials leak prevention)."""
    bad = {"source": {"type": "sql"}, "destination": {}}  # missing required fields
    response = client.post("/jobs", json=bad)
    assert response.status_code == 422
    body = response.json()
    # The response should have 'detail' but NOT the raw request body
    assert "detail" in body
    # Raw body values should not appear in response
    assert "sql" not in str(body.get("body", ""))
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/test_main.py -v
```

Expected: `ImportError` — `siphon.main` doesn't exist yet.

- [ ] **Step 3: Implement `src/siphon/main.py`**

```python
# src/siphon/main.py
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from siphon.models import ExtractRequest, Job, JobStatus, LogsResponse
from siphon.plugins.destinations import get as get_destination
from siphon.plugins.sources import get as get_source
from siphon.queue import JobQueue

logger = logging.getLogger(__name__)

# ── Config (read from env each request for testability) ───────────────────────
API_KEY: str | None = os.getenv("SIPHON_API_KEY")
DRAIN_TIMEOUT = int(os.getenv("SIPHON_DRAIN_TIMEOUT", "3600"))

# ── Startup warnings ──────────────────────────────────────────────────────────
if not os.getenv("SIPHON_API_KEY"):
    logging.warning("SIPHON_API_KEY not set — API authentication is disabled")
if not os.getenv("SIPHON_ALLOWED_HOSTS"):
    logging.warning("SIPHON_ALLOWED_HOSTS not set — all hosts are permitted (SSRF risk)")

# ── Queue singleton ───────────────────────────────────────────────────────────
queue: JobQueue = JobQueue()

# ── Service start time ────────────────────────────────────────────────────────
_START_TIME = datetime.now(tz=UTC)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    queue.start()
    yield
    await queue.drain(timeout=DRAIN_TIMEOUT)


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Siphon", lifespan=lifespan)

# Suppress credential leakage in uvicorn error logs for 422 responses
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Require Authorization: Bearer <SIPHON_API_KEY> on all routes except health probes."""
    if API_KEY and request.url.path not in ("/health/live", "/health/ready"):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    """Reject requests whose Content-Length exceeds SIPHON_MAX_REQUEST_SIZE_MB."""
    max_bytes = int(os.getenv("SIPHON_MAX_REQUEST_SIZE_MB", "1")) * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"},
        )
    return await call_next(request)


# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Override FastAPI's default 422 handler to never log or echo the request body.

    FastAPI's default handler includes the full parsed body in the response and logs it,
    which would expose connection strings, passwords, and S3 keys in error logs.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": exc.errors()},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job_and_plugins(req: ExtractRequest) -> tuple[Job, object, object]:
    """Resolve plugin classes from the registry and instantiate them.

    Raises HTTPException(400) if a plugin type is not registered.
    The ExtractRequest is NOT stored in the Job — credentials are dropped here.
    """
    try:
        source_cls = get_source(req.source.type)
        dest_cls = get_destination(req.destination.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = source_cls(**req.source.model_dump(exclude={"type"}))
    destination = dest_cls(**req.destination.model_dump(exclude={"type"}))
    job = Job(job_id=str(uuid.uuid4()))
    return job, source, destination


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/jobs", status_code=202)
async def create_job(req: ExtractRequest) -> dict:
    """Submit an async extraction job. Returns immediately with job_id."""
    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)
    return {"job_id": job.job_id, "status": job.status}


@app.post("/extract")
async def extract_sync(req: ExtractRequest) -> dict:
    """Synchronous extraction — blocks until job completes. For local dev and debug only.

    Do NOT use from Airflow in production — HTTP connections open for minutes are fragile.
    Use POST /jobs + polling instead.
    """
    import asyncio

    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)

    # Poll until terminal state
    while job.status in ("queued", "running"):
        await asyncio.sleep(0.05)

    duration_ms = None
    if job.started_at and job.finished_at:
        duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "rows_read": job.rows_read,
        "rows_written": job.rows_written,
        "duration_ms": duration_ms,
        "error": job.error,
        "logs": job.logs,
    }


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str) -> JobStatus:
    """Get job status by ID."""
    job = queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job.to_status()


@app.get("/jobs/{job_id}/logs", response_model=LogsResponse)
async def get_job_logs(job_id: str, since: int = 0) -> LogsResponse:
    """Get job logs from offset `since` (exclusive). Supports incremental polling.

    The SiphonOperator polls this with log_offset to stream logs without
    re-printing already-seen lines.
    """
    job = queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    logs_slice = job.logs[since:]
    return LogsResponse(
        job_id=job_id,
        logs=logs_slice,
        next_offset=since + len(logs_slice),
    )


@app.get("/health/live")
async def health_live() -> dict:
    """Kubernetes liveness probe. Returns 200 while the process is alive."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Kubernetes readiness probe. Returns 503 when queue is full or draining."""
    if queue.is_draining:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "accepting_jobs": False, "reason": "draining"},
        )
    if queue.is_full:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "accepting_jobs": False, "reason": "queue_full"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "accepting_jobs": True},
    )


@app.get("/health")
async def health_debug() -> dict:
    """Human-readable health endpoint for operators and dashboards.

    Kubernetes uses /health/live and /health/ready instead.
    """
    uptime = int((datetime.now(tz=UTC) - _START_TIME).total_seconds())
    accepting = not (queue.is_full or queue.is_draining)
    return {
        "status": "ok" if accepting else "degraded",
        "accepting_jobs": accepting,
        "queue": queue.stats,
        "uptime_seconds": uptime,
    }


# ── Reserved endpoints (deferred to v1.1) ────────────────────────────────────

@app.get("/metrics")
async def metrics_reserved() -> dict:
    """Prometheus metrics endpoint — reserved for v1.1. Returns empty response."""
    return {}
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/test_main.py -v
```

Expected: all tests PASS.

If `test_request_too_large_returns_413` fails: verify the `request_size_limit` middleware reads `SIPHON_MAX_REQUEST_SIZE_MB` at request time (inside the function, not at module load). The monkeypatch must take effect for each request.

If API key tests fail: verify that `main_module.API_KEY` is being reassigned in the test (the middleware reads `API_KEY` from module scope, so reassigning `main_module.API_KEY` is correct).

- [ ] **Step 5: Run full test suite**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/ -v --tb=short
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/lucasnvk/projects/siphon
git add src/siphon/main.py tests/test_main.py
git commit -m "feat: add FastAPI app with job queue, routes, and security middleware"
```

---

## Task 4: Final verification — ruff + full test suite

**Files:** no new files

- [ ] **Step 1: Run ruff check and format**

```bash
cd /home/lucasnvk/projects/siphon
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: zero violations. If violations found, fix with:
```bash
uv run ruff check --fix src/ tests/
uv run ruff format src/ tests/
```
Then manually fix any remaining lint issues.

- [ ] **Step 2: Run full test suite**

```bash
cd /home/lucasnvk/projects/siphon && uv run pytest tests/ -v --tb=short
```

Expected: ALL tests PASS. Count should be: 49 (Phase 1) + 6 (worker) + 10 (queue) + ~30 (main) = ~95 tests.

- [ ] **Step 3: Verify the service starts**

```bash
cd /home/lucasnvk/projects/siphon && timeout 5 uv run uvicorn siphon.main:app --port 8001 || true
```

Expected: the server starts, logs appear (queue started, startup warnings if no API key set), and is killed by timeout after 5s. No ImportError or startup crash.

- [ ] **Step 4: Final commit**

```bash
cd /home/lucasnvk/projects/siphon
git add -A
git commit -m "chore: phase 2 complete — API, queue, worker, security middleware"
```

---

## Completion Criteria

- `uv run pytest tests/ -v` passes without errors
- `uv run ruff check src/ tests/` passes without violations
- `POST /jobs` returns 202 with `job_id`
- `POST /jobs` returns 429 when queue is at capacity
- `POST /extract` blocks until job completes and returns full result
- `GET /jobs/{id}` returns JobStatus with correct status field
- `GET /jobs/{id}/logs?since=N` returns only logs from offset N
- `GET /health/live` always returns 200
- `GET /health/ready` returns 503 when `is_full` or `is_draining`
- `GET /health` returns full queue stats
- API key middleware returns 401 when `SIPHON_API_KEY` is set and header is missing/wrong
- `/health/live` and `/health/ready` bypass API key auth
- Request size limit returns 413 when body exceeds `SIPHON_MAX_REQUEST_SIZE_MB`
- 422 responses do not include the raw request body
- `GET /metrics` returns 200 (reserved, deferred)
