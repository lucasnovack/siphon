# Phase 15 — Cleanup + Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove BigQuery and Snowflake destinations, add per-source concurrency limits, and add job priority ordering.

**Architecture:** Destinations autodiscovery means deleting plugin files removes them from the registry automatically. Concurrency limiting uses a new `max_concurrent_jobs` field on `Connection` tracked in-queue via `_active_by_connection`. Priority ordering replaces the current fire-and-forget `ensure_future` dispatch with a real `asyncio.PriorityQueue` + background dispatcher loop.

**Tech Stack:** Python, FastAPI, SQLAlchemy async, Alembic, asyncio, requests.Session

---

## File Map

| Action | File |
|---|---|
| Delete | `src/siphon/plugins/destinations/bigquery_dest.py` |
| Delete | `src/siphon/plugins/destinations/snowflake_dest.py` |
| Delete | `tests/test_bigquery_destination.py` |
| Delete | `tests/test_snowflake_destination.py` |
| Modify | `src/siphon/models.py` — remove BQ/Snowflake configs, `DestinationConfig` union, add `Job.priority` |
| Create | `alembic/versions/008_add_connection_concurrency.py` |
| Modify | `src/siphon/orm.py` — add `Connection.max_concurrent_jobs` |
| Modify | `src/siphon/connections/router.py` — add `max_concurrent_jobs` to schemas + endpoints |
| Modify | `tests/test_connections.py` — cover `max_concurrent_jobs` |
| Create | `alembic/versions/009_add_pipeline_priority.py` |
| Modify | `src/siphon/orm.py` — add `Pipeline.priority` |
| Modify | `src/siphon/pipelines/router.py` — add `priority` to schemas + endpoints |
| Modify | `tests/test_pipelines.py` — cover `priority` field |
| Modify | `src/siphon/queue.py` — PriorityQueue + dispatcher loop + concurrency tracking |
| Modify | `src/siphon/main.py` — update `submit()` callers |
| Modify | `src/siphon/scheduler.py` — update `submit()` caller |
| Modify | `tests/test_queue.py` — priority ordering + concurrency limiting tests |
| Modify | `src/siphon/plugins/sources/http_rest.py` — requests.Session singleton |
| Modify | `tests/test_http_rest_source.py` — session reuse test |

---

## Task 1: Remove BigQuery and Snowflake destinations

**Files:**
- Delete: `src/siphon/plugins/destinations/bigquery_dest.py`
- Delete: `src/siphon/plugins/destinations/snowflake_dest.py`
- Delete: `tests/test_bigquery_destination.py`
- Delete: `tests/test_snowflake_destination.py`
- Modify: `src/siphon/models.py`

- [ ] **Step 1: Delete destination files and tests**

```bash
rm src/siphon/plugins/destinations/bigquery_dest.py
rm src/siphon/plugins/destinations/snowflake_dest.py
rm tests/test_bigquery_destination.py
rm tests/test_snowflake_destination.py
```

- [ ] **Step 2: Update models.py — remove BQ and Snowflake config classes and update the union**

In `src/siphon/models.py`, remove the entire `BigQueryDestinationConfig` class (lines ~98–112) and the entire `SnowflakeDestinationConfig` class (lines ~114–130). Then update the `DestinationConfig` type alias:

```python
# Replace this block:
DestinationConfig = Annotated[
    S3ParquetDestinationConfig | BigQueryDestinationConfig | SnowflakeDestinationConfig,
    Field(discriminator="type"),
]

# With:
DestinationConfig = S3ParquetDestinationConfig
```

Since `S3ParquetDestinationConfig` is now the only destination, the discriminator union is no longer needed.

- [ ] **Step 3: Run tests to confirm deletion is clean**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all remaining tests pass, no import errors about bigquery or snowflake.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(phase-15): remove BigQuery and Snowflake destinations — S3/Parquet only"
```

---

## Task 2: Migration 008 — max_concurrent_jobs on connections

**Files:**
- Create: `alembic/versions/008_add_connection_concurrency.py`
- Modify: `src/siphon/orm.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_connections.py`, add:

```python
def test_connection_response_includes_max_concurrent_jobs(client):
    """ConnectionResponse must expose max_concurrent_jobs."""
    tc, db = client
    row = _make_conn_row()
    row.max_concurrent_jobs = 3
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
    ))
    resp = tc.get("/api/v1/connections")
    assert resp.status_code == 200
    assert resp.json()[0]["max_concurrent_jobs"] == 3
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_connections.py::test_connection_response_includes_max_concurrent_jobs -v
```

Expected: FAIL — `max_concurrent_jobs` not in response schema.

- [ ] **Step 3: Create migration 008**

Create `alembic/versions/008_add_connection_concurrency.py`:

```python
"""add max_concurrent_jobs to connections

Revision ID: 008
Revises: 007
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connections",
        sa.Column(
            "max_concurrent_jobs",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )


def downgrade() -> None:
    op.drop_column("connections", "max_concurrent_jobs")
```

- [ ] **Step 4: Add field to Connection ORM**

In `src/siphon/orm.py`, add to the `Connection` class after `key_version`:

```python
max_concurrent_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
```

- [ ] **Step 5: Update ConnectionCreate, ConnectionUpdate, ConnectionResponse and _to_response**

In `src/siphon/connections/router.py`:

Add to `ConnectionCreate`:
```python
max_concurrent_jobs: int = 2
```

Add to `ConnectionUpdate`:
```python
max_concurrent_jobs: int | None = None
```

Add to `ConnectionResponse`:
```python
max_concurrent_jobs: int
```

Update `_to_response()`:
```python
def _to_response(conn: Connection) -> ConnectionResponse:
    return ConnectionResponse(
        id=str(conn.id),
        name=conn.name,
        type=conn.conn_type,
        key_version=conn.key_version,
        max_concurrent_jobs=conn.max_concurrent_jobs,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
    )
```

In `create_connection`, when building `conn = Connection(...)`, add:
```python
max_concurrent_jobs=body.max_concurrent_jobs,
```

In `update_connection`, when updating fields, add:
```python
if body.max_concurrent_jobs is not None:
    conn.max_concurrent_jobs = body.max_concurrent_jobs
```

Also update `_make_conn_row()` in `tests/test_connections.py` to add `row.max_concurrent_jobs = 2`.

- [ ] **Step 6: Run the new test**

```bash
pytest tests/test_connections.py -v 2>&1 | tail -20
```

Expected: all pass including `test_connection_response_includes_max_concurrent_jobs`.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/008_add_connection_concurrency.py src/siphon/orm.py src/siphon/connections/router.py tests/test_connections.py
git commit -m "feat(phase-15): add max_concurrent_jobs to connections (migration 008)"
```

---

## Task 3: Migration 009 — priority on pipelines

**Files:**
- Create: `alembic/versions/009_add_pipeline_priority.py`
- Modify: `src/siphon/orm.py`
- Modify: `src/siphon/models.py` — add `Job.priority`
- Modify: `src/siphon/pipelines/router.py`
- Modify: `tests/test_pipelines.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_pipelines.py`, add at the bottom:

```python
def test_pipeline_create_accepts_priority(client):
    """PipelineCreate must accept a priority field and persist it."""
    tc, db = client

    src_conn = MagicMock()
    src_conn.id = uuid.uuid4()
    src_conn.conn_type = "sql"
    src_conn.max_concurrent_jobs = 2

    dest_conn = MagicMock()
    dest_conn.id = uuid.uuid4()

    db.get = AsyncMock(side_effect=lambda model, id_: src_conn if model.__name__ == "Connection" else dest_conn)
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    resp = tc.post("/api/v1/pipelines", json={
        "name": "test-priority",
        "source_connection_id": str(src_conn.id),
        "query": "SELECT 1",
        "destination_path": "s3://bucket/path",
        "priority": "high",
    })
    assert resp.status_code == 201


def test_pipeline_response_includes_priority(client):
    """PipelineResponse must include priority field."""
    tc, db = client
    row = _make_pipeline_row()
    row.priority = "high"
    db.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row])))
    ))
    resp = tc.get("/api/v1/pipelines")
    assert resp.status_code == 200
    assert resp.json()[0]["priority"] == "high"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_pipelines.py::test_pipeline_create_accepts_priority tests/test_pipelines.py::test_pipeline_response_includes_priority -v
```

Expected: FAIL.

- [ ] **Step 3: Create migration 009**

Create `alembic/versions/009_add_pipeline_priority.py`:

```python
"""add priority to pipelines

Revision ID: 009
Revises: 008
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipelines",
        sa.Column(
            "priority",
            sa.String(10),
            nullable=False,
            server_default="normal",
        ),
    )


def downgrade() -> None:
    op.drop_column("pipelines", "priority")
```

- [ ] **Step 4: Add priority to Pipeline ORM**

In `src/siphon/orm.py`, add to the `Pipeline` class (after `partition_by`):

```python
priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="normal")
```

- [ ] **Step 5: Add priority to pipeline Pydantic schemas**

In `src/siphon/pipelines/router.py`:

Add to `PipelineCreate`:
```python
priority: Literal["low", "normal", "high"] = "normal"
```

Add to `PipelineUpdate`:
```python
priority: Literal["low", "normal", "high"] | None = None
```

Add to `PipelineResponse` (wherever that class is defined in the router):
```python
priority: str
```

In the `_to_response()` or equivalent helper that builds `PipelineResponse`, add:
```python
priority=p.priority,
```

In `create_pipeline`, when constructing `Pipeline(...)`, add:
```python
priority=body.priority,
```

In `update_pipeline`, add:
```python
if body.priority is not None:
    pipeline.priority = body.priority
```

- [ ] **Step 6: Add priority to Job dataclass**

In `src/siphon/models.py`, add to the `Job` dataclass (after `job_id`):

```python
priority: str = "normal"   # "low" | "normal" | "high"
```

In `trigger_pipeline` in `pipelines/router.py`, when constructing `Job(...)`, add:
```python
priority=pipeline.priority,
```

Do the same in `scheduler.py` when constructing `Job(...)`.

Also update `_make_pipeline_row()` in `tests/test_pipelines.py` to add `row.priority = "normal"`.

- [ ] **Step 7: Run the new tests**

```bash
pytest tests/test_pipelines.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions/009_add_pipeline_priority.py src/siphon/orm.py src/siphon/models.py src/siphon/pipelines/router.py src/siphon/scheduler.py tests/test_pipelines.py
git commit -m "feat(phase-15): add priority field to pipelines and Job (migration 009)"
```

---

## Task 4: Priority queue + concurrency limiting in queue.py

**Files:**
- Modify: `src/siphon/queue.py`
- Modify: `src/siphon/main.py`
- Modify: `src/siphon/scheduler.py`
- Modify: `tests/test_queue.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_queue.py`:

```python
async def test_high_priority_job_dispatched_before_low():
    """High-priority job must be dispatched before a low-priority job when both are queued."""
    import asyncio
    dispatch_order = []
    gate = threading.Event()

    class _RecordingSource(Source):
        def __init__(self, name):
            self.name = name
        def extract(self) -> pa.Table:
            dispatch_order.append(self.name)
            gate.wait(timeout=5)
            return pa.table({"x": [1]})

    q = JobQueue(max_workers=1, max_queue=5, job_timeout=10)
    q.start()
    try:
        # Fill the one worker slot with a blocking job
        blocker = Job(job_id="j-blocker", priority="normal")
        await q.submit(blocker, _RecordingSource("blocker"), _NopDest())
        await asyncio.sleep(0.1)  # let blocker start

        # Enqueue low then high — high must come out first
        low_job = Job(job_id="j-low", priority="low")
        high_job = Job(job_id="j-high", priority="high")
        await q.submit(low_job, _RecordingSource("low"), _NopDest())
        await q.submit(high_job, _RecordingSource("high"), _NopDest())

        gate.set()  # release blocker + both queued jobs
        for _ in range(80):
            await asyncio.sleep(0.1)
            if high_job.status not in ("queued", "running") and low_job.status not in ("queued", "running"):
                break

        # blocker first (already running), then high before low
        assert dispatch_order[1] == "high", f"Expected 'high' second, got {dispatch_order}"
        assert dispatch_order[2] == "low", f"Expected 'low' third, got {dispatch_order}"
    finally:
        gate.set()
        q._executor.shutdown(wait=False)


async def test_concurrency_limit_per_connection():
    """Jobs sharing a source_connection_id must respect max_concurrent limit."""
    active_count = []
    conn_id = "conn-abc"

    class _CountingSource(Source):
        def extract(self) -> pa.Table:
            active_count.append(1)
            import time
            time.sleep(0.2)  # simulate work
            active_count.append(-1)
            return pa.table({"x": [1]})

    q = JobQueue(max_workers=4, max_queue=10, job_timeout=10)
    q.start()
    try:
        jobs = []
        for i in range(4):
            job = Job(job_id=f"j-{i}", priority="normal")
            job.source_connection_id = conn_id
            await q.submit(job, _CountingSource(), _NopDest(), max_concurrent=2)
            jobs.append(job)

        for _ in range(60):
            await asyncio.sleep(0.1)
            if all(j.status not in ("queued", "running") for j in jobs):
                break

        # Max concurrent active for this connection must never exceed 2
        peak = 0
        running = 0
        for delta in active_count:
            running += delta
            peak = max(peak, running)
        assert peak <= 2, f"Peak concurrency was {peak}, expected <= 2"
    finally:
        q._executor.shutdown(wait=False)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_queue.py::test_high_priority_job_dispatched_before_low tests/test_queue.py::test_concurrency_limit_per_connection -v
```

Expected: FAIL.

- [ ] **Step 3: Rewrite queue.py**

Replace `src/siphon/queue.py` with:

```python
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
        # Priority queue: (priority_int, counter, job_id)
        self._pqueue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        # Maps job_id → (job, source, destination, max_concurrent)
        self._pending: dict[str, tuple[Job, Source, Destination, int]] = {}
        # Active job count per source_connection_id
        self._active_by_connection: dict[str, int] = {}
        self._pqueue_counter: int = 0

    def start(self) -> None:
        """Start the thread pool and dispatcher. Call once at service startup."""
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        asyncio.ensure_future(self._evict_loop())
        asyncio.ensure_future(self._dispatcher_loop())
        logger.info(
            "JobQueue started: max_workers=%d, max_queue=%d, job_ttl=%ds",
            self._max_workers,
            self._max_queue,
            self._job_ttl,
        )

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
```

- [ ] **Step 4: Update submit() callers — main.py**

In `src/siphon/main.py`, the two `queue.submit(job, source, destination)` calls (lines ~277 and ~293) don't have connection context. Leave them with the default:

```python
await queue.submit(job, source, destination)  # max_concurrent defaults to 2
```

No change needed here since `max_concurrent=2` is the default.

- [ ] **Step 5: Update submit() caller — pipelines/router.py**

In `src/siphon/pipelines/router.py`, in `trigger_pipeline`, after loading `src_conn`, pass `max_concurrent`:

```python
await q.submit(job, source, destination, max_concurrent=src_conn.max_concurrent_jobs)
```

- [ ] **Step 6: Update submit() caller — scheduler.py**

In `src/siphon/scheduler.py`, in `_async_trigger_pipeline`, after loading the source connection, pass `max_concurrent`. Find the connection load and update:

```python
await queue.submit(job, source, destination, max_concurrent=src_conn.max_concurrent_jobs)
```

where `src_conn` is the `Connection` ORM object loaded for `p.source_connection_id`.

- [ ] **Step 7: Run new queue tests**

```bash
pytest tests/test_queue.py -v 2>&1 | tail -30
```

Expected: all pass including the two new tests.

- [ ] **Step 8: Run full test suite**

```bash
pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/siphon/queue.py src/siphon/main.py src/siphon/pipelines/router.py src/siphon/scheduler.py tests/test_queue.py
git commit -m "feat(phase-15): priority queue + per-connection concurrency limiting"
```

---

## Task 5: requests.Session singleton in HTTPRestSource

**Files:**
- Modify: `src/siphon/plugins/sources/http_rest.py`
- Modify: `src/siphon/main.py`
- Modify: `tests/test_http_rest_source.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_http_rest_source.py`, add:

```python
def test_http_rest_uses_module_session(monkeypatch):
    """All requests must go through the module-level _session, not requests.get directly."""
    import siphon.plugins.sources.http_rest as m
    call_count = []

    original_get = m._session.get
    def counting_get(*args, **kwargs):
        call_count.append(1)
        return original_get(*args, **kwargs)

    monkeypatch.setattr(m._session, "get", counting_get)

    from unittest.mock import MagicMock
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=[{"id": 1}])
    monkeypatch.setattr(m._session, "get", lambda *a, **kw: (call_count.append(1), mock_resp)[1])

    source = m.HTTPRestSource(url="http://fake.test/data")
    list(source.extract_batches())

    assert len(call_count) == 1, "Expected exactly one call through _session"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_http_rest_source.py::test_http_rest_uses_module_session -v
```

Expected: FAIL — module has no `_session` attribute.

- [ ] **Step 3: Add _session singleton to http_rest.py**

At the top of `src/siphon/plugins/sources/http_rest.py`, after the imports, add:

```python
import requests

_session = requests.Session()
```

Then replace every `requests.get(` call in the file with `_session.get(` and every `requests.post(` call with `_session.post(`. There are calls in:
- `_fetch_oauth2_token()` — one `requests.post()`
- `extract_batches()` — multiple `requests.get()` calls (for each pagination branch)

- [ ] **Step 4: Close the session on app shutdown**

In `src/siphon/main.py`, in the lifespan `finally` block (after `await queue.drain(...)`), add:

```python
from siphon.plugins.sources.http_rest import _session as _http_session
_http_session.close()
```

- [ ] **Step 5: Run the new test**

```bash
pytest tests/test_http_rest_source.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/siphon/plugins/sources/http_rest.py src/siphon/main.py tests/test_http_rest_source.py
git commit -m "perf(phase-15): reuse requests.Session singleton in HTTPRestSource"
```

---

## Task 6: Full test suite + Phase 15 wrap-up

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all tests pass, 0 failures.

- [ ] **Step 2: Ruff lint check**

```bash
ruff check src/ tests/
```

Expected: no violations.

- [ ] **Step 3: Tag phase completion**

```bash
git log --oneline -8
```

Verify all Phase 15 commits are present, then:

```bash
git commit --allow-empty -m "chore: phase 15 complete — cleanup + performance"
```
