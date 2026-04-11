# Phase 16 — Celery + Redis (Horizontal Scale) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-process asyncio queue with Celery + Redis so multiple worker pods can consume from the same queue.

**Architecture:** FastAPI API pod enqueues Celery tasks into Redis. Separate `siphon-worker` processes (Celery) consume tasks and call `run_job()` in `worker.py` (unchanged). Job state moves from the in-memory `_jobs` dict to the `job_runs` PostgreSQL table. `queue.py` becomes a thin wrapper over `celery_app.task.apply_async()`.

**Tech Stack:** Celery 5, Redis 7, kombu, Python, FastAPI, SQLAlchemy async

**Prerequisite:** Phase 15 must be merged. `Pipeline.priority` and `Job.priority` must exist.

---

## File Map

| Action | File |
|---|---|
| Create | `src/siphon/celery_app.py` |
| Create | `src/siphon/tasks.py` |
| Modify | `src/siphon/queue.py` — thin Celery wrapper |
| Modify | `src/siphon/main.py` — remove in-memory state endpoints, update lifespan |
| Modify | `src/siphon/runs/router.py` — `GET /jobs/{id}` reads from DB |
| Modify | `src/siphon/runs/router.py` — cancel via Celery revoke |
| Modify | `docker-compose.yml` — add redis + siphon-worker services |
| Modify | `pyproject.toml` — add celery[redis] dependency |
| Create | `tests/test_celery_tasks.py` |
| Modify | `tests/test_queue.py` — update for Celery-backed queue |

---

## Task 1: Add Celery dependency and celery_app.py

**Files:**
- Modify: `pyproject.toml`
- Create: `src/siphon/celery_app.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_celery_tasks.py`:

```python
# tests/test_celery_tasks.py
"""Tests for Celery app configuration and task serialization."""
import pytest


def test_celery_app_has_three_queues():
    """Celery app must declare high, normal, and low queues."""
    from siphon.celery_app import app
    queue_names = {q.name for q in app.conf.task_queues}
    assert "high" in queue_names
    assert "normal" in queue_names
    assert "low" in queue_names


def test_celery_app_uses_json_serializer():
    from siphon.celery_app import app
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]


def test_celery_app_acks_late():
    """task_acks_late=True ensures task is acked after completion, not before."""
    from siphon.celery_app import app
    assert app.conf.task_acks_late is True


def test_celery_app_prefetch_one():
    """worker_prefetch_multiplier=1 means one task at a time per worker."""
    from siphon.celery_app import app
    assert app.conf.worker_prefetch_multiplier == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_celery_tasks.py -v
```

Expected: FAIL — `siphon.celery_app` module does not exist.

- [ ] **Step 3: Add celery[redis] to pyproject.toml**

In `pyproject.toml`, in the `[project] dependencies` list, add:

```toml
"celery[redis]>=5.3",
```

Install:

```bash
uv sync
```

- [ ] **Step 4: Create celery_app.py**

Create `src/siphon/celery_app.py`:

```python
# src/siphon/celery_app.py
import os

from celery import Celery
from kombu import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("siphon", broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_queues=(
        Queue("high"),
        Queue("normal"),
        Queue("low"),
    ),
    task_default_queue="normal",
    broker_connection_retry_on_startup=True,
)

# Auto-discover tasks from siphon.tasks
app.autodiscover_tasks(["siphon"])
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_celery_tasks.py -v
```

Expected: all 4 pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/siphon/celery_app.py tests/test_celery_tasks.py
git commit -m "feat(phase-16): add celery_app.py with Redis broker and three priority queues"
```

---

## Task 2: Create tasks.py — the Celery task

**Files:**
- Create: `src/siphon/tasks.py`
- Modify: `tests/test_celery_tasks.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_celery_tasks.py`:

```python
def test_job_dict_roundtrip():
    """Job can be serialized to dict and reconstructed for Celery transport."""
    from siphon.models import Job
    from siphon.tasks import _job_to_dict, _job_from_dict

    job = Job(
        job_id="test-123",
        priority="high",
        pipeline_id="pipe-uuid",
        run_id=42,
    )
    d = _job_to_dict(job)
    assert isinstance(d, dict)
    assert d["job_id"] == "test-123"
    assert d["priority"] == "high"

    restored = _job_from_dict(d)
    assert restored.job_id == "test-123"
    assert restored.priority == "high"
    assert restored.run_id == 42


def test_run_pipeline_task_is_registered():
    """run_pipeline_task must be registered in the Celery app."""
    from siphon.celery_app import app
    assert "siphon.tasks.run_pipeline_task" in app.tasks
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_celery_tasks.py::test_job_dict_roundtrip tests/test_celery_tasks.py::test_run_pipeline_task_is_registered -v
```

Expected: FAIL.

- [ ] **Step 3: Create tasks.py**

Create `src/siphon/tasks.py`:

```python
# src/siphon/tasks.py
import asyncio
import dataclasses
from datetime import UTC, datetime

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
    # _actual_schema is transient — drop it
    d.pop("_actual_schema", None)
    return d


def _job_from_dict(d: dict) -> Job:
    """Reconstruct a Job dataclass from a Celery-transported dict."""
    # Parse datetime fields back
    for key in ("created_at", "started_at", "finished_at"):
        if d.get(key) is not None:
            d[key] = datetime.fromisoformat(d[key])
    return Job(**{k: v for k, v in d.items() if k in {f.name for f in dataclasses.fields(Job)}})


@app.task(
    bind=True,
    name="siphon.tasks.run_pipeline_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_pipeline_task(self, job_dict: dict, source_dict: dict, destination_dict: dict) -> None:
    """Celery task: reconstruct Job + plugins, call run_job(), persist result."""
    from siphon.plugins.sources import get as get_source
    from siphon.plugins.destinations import get as get_destination

    job = _job_from_dict(job_dict)
    structlog.contextvars.bind_contextvars(job_id=job.job_id, pipeline_id=job.pipeline_id)

    source_type = source_dict.pop("type")
    dest_type = destination_dict.pop("type")

    try:
        source_cls = get_source(source_type)
        dest_cls = get_destination(dest_type)
    except ValueError as exc:
        logger.error("Unknown plugin type", error=str(exc))
        return

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
        raise self.retry(exc=exc)
    finally:
        loop.close()


async def _run_job_async(source, destination, job, db_factory) -> None:
    """Async wrapper so run_job (which is async) can be called from a sync Celery task."""
    from concurrent.futures import ThreadPoolExecutor
    import os

    max_workers = int(os.getenv("SIPHON_MAX_WORKERS", "1"))
    job_timeout = int(os.getenv("SIPHON_JOB_TIMEOUT", "3600"))

    from siphon import worker
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        await worker.run_job(source, destination, job, executor, job_timeout, db_factory=db_factory)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_celery_tasks.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/siphon/tasks.py tests/test_celery_tasks.py
git commit -m "feat(phase-16): add run_pipeline_task Celery task with Job serialization"
```

---

## Task 3: Replace queue.py with Celery-backed thin wrapper

**Files:**
- Modify: `src/siphon/queue.py`
- Modify: `tests/test_queue.py`

The in-memory queue is replaced with Celery. The `JobQueue` class becomes a thin wrapper that calls `run_pipeline_task.apply_async()`. The `_jobs` dict is kept in memory for the `/jobs/{id}` endpoint only during the transition (Phase 16 completes the migration to DB-only in Task 4).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_celery_tasks.py`:

```python
def test_celery_queue_submit_calls_apply_async(monkeypatch):
    """JobQueue.submit() must call run_pipeline_task.apply_async() via Celery."""
    from unittest.mock import MagicMock, patch
    import pyarrow as pa
    from siphon.models import Job
    from siphon.plugins.sources.base import Source
    from siphon.plugins.destinations.base import Destination
    from siphon.queue import JobQueue

    class _FakeSrc(Source):
        type = "sql"
        def extract(self): return pa.table({"x": [1]})
        def model_dump(self, **kw): return {"type": "sql", "connection": "x://", "query": "SELECT 1"}

    class _FakeDest(Destination):
        type = "s3_parquet"
        def write(self, t, is_first_chunk=True): return t.num_rows
        def model_dump(self, **kw): return {"type": "s3_parquet", "path": "s3://b/p", "endpoint": "http://minio", "access_key": "k", "secret_key": "s"}

    applied = []
    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock(side_effect=lambda *a, **kw: applied.append(kw))
        q = JobQueue()
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            q.submit(Job(job_id="j-1", priority="high"), _FakeSrc(), _FakeDest())
        )

    assert len(applied) == 1
    assert applied[0]["queue"] == "high"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_celery_tasks.py::test_celery_queue_submit_calls_apply_async -v
```

Expected: FAIL.

- [ ] **Step 3: Rewrite queue.py as Celery wrapper**

Replace `src/siphon/queue.py` with:

```python
# src/siphon/queue.py
"""Thin Celery-backed queue wrapper.

submit() serializes source/destination configs and dispatches run_pipeline_task
to the appropriate Celery queue (high/normal/low) based on job.priority.
Job state is tracked in job_runs (PostgreSQL) by the Celery worker.
"""
import os

import structlog
from fastapi import HTTPException

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source
from siphon.tasks import _job_to_dict, run_pipeline_task

logger = structlog.get_logger()

_MAX_QUEUE = int(os.getenv("SIPHON_MAX_QUEUE", "50"))


class JobQueue:
    """Celery-backed job queue.

    submit()    → serialize + apply_async to Celery; raises 429 if Celery is unreachable
    is_full     → always False (Celery manages its own backpressure)
    is_draining → always False (no in-process drain needed)
    """

    @property
    def is_full(self) -> bool:
        return False

    @property
    def is_draining(self) -> bool:
        return False

    @property
    def stats(self) -> dict:
        return {"backend": "celery"}

    def start(self) -> None:
        logger.info("JobQueue (Celery) ready")

    def get_job(self, job_id: str) -> Job | None:
        """In Phase 16, job state lives in job_runs DB. Returns None always."""
        return None

    async def submit(
        self,
        job: Job,
        source: Source,
        destination: Destination,
        max_concurrent: int = 2,
    ) -> None:
        """Dispatch a job to the Celery queue matching job.priority."""
        queue_name = job.priority if job.priority in ("high", "normal", "low") else "normal"

        # Serialize source and destination configs for JSON transport
        source_dict = source.model_dump() if hasattr(source, "model_dump") else {}
        dest_dict = destination.model_dump() if hasattr(destination, "model_dump") else {}

        try:
            run_pipeline_task.apply_async(
                args=[_job_to_dict(job), source_dict, dest_dict],
                queue=queue_name,
            )
        except Exception as exc:
            logger.error("Failed to enqueue job via Celery", job_id=job.job_id, error=str(exc))
            raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}") from exc

        logger.info("Job %s dispatched to Celery queue=%s", job.job_id, queue_name)

    async def drain(self, timeout: float) -> None:
        """No-op: Celery workers handle their own graceful shutdown."""
        logger.info("Celery queue drain requested (no-op — workers drain independently)")
```

- [ ] **Step 4: Note: model_dump() on Source/Destination**

The `Source` and `Destination` ABCs don't have `model_dump()`. In Phase 16, the plugin instances are constructed from Pydantic config objects — the config dict is what needs to be serialized, not the plugin instance.

The serialization must happen in the caller (trigger endpoint / scheduler) **before** constructing the plugin. Update `pipelines/router.py` trigger endpoint:

In `trigger_pipeline`, instead of constructing source/destination and passing them to `submit()`, pass the raw config dicts to `submit()`. The Celery task reconstructs the plugins.

Change the `submit()` signature to accept config dicts directly:

Update `queue.py` submit():
```python
async def submit(
    self,
    job: Job,
    source_config: dict,         # raw config dict (includes "type" key)
    destination_config: dict,    # raw config dict (includes "type" key)
    max_concurrent: int = 2,
) -> None:
    queue_name = job.priority if job.priority in ("high", "normal", "low") else "normal"
    try:
        run_pipeline_task.apply_async(
            args=[_job_to_dict(job), source_config, destination_config],
            queue=queue_name,
        )
    except Exception as exc:
        logger.error("Failed to enqueue job via Celery", job_id=job.job_id, error=str(exc))
        raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}") from exc
    logger.info("Job %s dispatched to Celery queue=%s", job.job_id, queue_name)
```

Update callers — `pipelines/router.py` trigger:
```python
# Instead of constructing source/destination before submit:
source_config = {"type": req.source.type, **req.source.model_dump(exclude={"type"})}
dest_config = {"type": req.destination.type, **req.destination.model_dump(exclude={"type"})}
await q.submit(job, source_config, dest_config, max_concurrent=src_conn.max_concurrent_jobs)
```

Do the same in `main.py` and `scheduler.py`.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_celery_tasks.py tests/test_queue.py -v 2>&1 | tail -30
```

Expected: all pass. Note: existing `test_queue.py` tests that relied on in-memory behavior (blocking sources, drain) will need to be updated to mock Celery — see Step 6.

- [ ] **Step 6: Update legacy test_queue.py tests**

The old queue tests that used blocking sources no longer apply to the Celery-backed queue. Remove or replace tests that relied on in-process execution:

Tests to remove (they test in-process threading behavior that no longer exists):
- `test_submit_job_eventually_succeeds`
- `test_submit_raises_429_when_full`
- `test_is_full_true_when_at_capacity`
- `test_drain_waits_for_active_jobs`
- `test_drain_rejects_new_submissions`
- `test_drain_timeout_marks_running_jobs_failed`
- `test_evict_expired_removes_old_completed_jobs`
- `test_evict_expired_keeps_running_jobs`
- `test_high_priority_job_dispatched_before_low` (moves to integration test)
- `test_concurrency_limit_per_connection` (moves to integration test)

Tests to keep and update:
- `test_submit_accepts_job` → update to mock `run_pipeline_task.apply_async` and assert it was called
- `test_get_job_returns_none_for_unknown` → still valid (returns None)
- `test_stats_reflects_queue_state` → update expected response

Replace `tests/test_queue.py` with:

```python
# tests/test_queue.py
"""Tests for the Celery-backed JobQueue wrapper."""
import asyncio
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from siphon.models import Job
from siphon.plugins.destinations.base import Destination
from siphon.plugins.sources.base import Source
from siphon.queue import JobQueue


class _FakeSrc(Source):
    def extract(self) -> pa.Table:
        return pa.table({"x": [1]})


class _FakeDest(Destination):
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return table.num_rows


@pytest.fixture
def q():
    return JobQueue()


async def test_submit_dispatches_to_celery(q):
    """submit() must call run_pipeline_task.apply_async with the correct queue."""
    job = Job(job_id="j-normal", priority="normal")
    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock()
        await q.submit(job, {}, {})
    mock_task.apply_async.assert_called_once()
    _, kwargs = mock_task.apply_async.call_args
    assert kwargs["queue"] == "normal"


async def test_submit_uses_high_queue_for_high_priority(q):
    job = Job(job_id="j-high", priority="high")
    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock()
        await q.submit(job, {}, {})
    _, kwargs = mock_task.apply_async.call_args
    assert kwargs["queue"] == "high"


async def test_submit_uses_low_queue_for_low_priority(q):
    job = Job(job_id="j-low", priority="low")
    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock()
        await q.submit(job, {}, {})
    _, kwargs = mock_task.apply_async.call_args
    assert kwargs["queue"] == "low"


async def test_submit_raises_503_when_celery_unreachable(q):
    """If apply_async raises, submit() must raise HTTP 503."""
    from fastapi import HTTPException
    job = Job(job_id="j-err", priority="normal")
    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock(side_effect=Exception("Redis down"))
        with pytest.raises(HTTPException) as exc_info:
            await q.submit(job, {}, {})
    assert exc_info.value.status_code == 503


async def test_get_job_returns_none(q):
    """In Celery mode, get_job always returns None (state is in DB)."""
    assert q.get_job("anything") is None


async def test_stats_returns_backend_key(q):
    stats = q.stats
    assert stats["backend"] == "celery"


async def test_is_full_always_false(q):
    assert q.is_full is False
```

- [ ] **Step 7: Run all tests**

```bash
pytest tests/test_queue.py tests/test_celery_tasks.py -v 2>&1 | tail -30
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/siphon/queue.py tests/test_queue.py
git commit -m "feat(phase-16): replace in-memory queue with Celery-backed thin wrapper"
```

---

## Task 4: Job state from DB — update GET /jobs/{id} and cancel

**Files:**
- Modify: `src/siphon/runs/router.py`
- Modify: `src/siphon/main.py`

Currently `GET /jobs/{id}` reads from `queue._jobs`. Since Celery workers run in separate processes, the in-memory dict is gone. State now lives in `job_runs`.

- [ ] **Step 1: Write the failing test**

In `tests/test_runs.py`, add:

```python
def test_get_job_by_job_id_reads_from_db(client):
    """GET /jobs/{id} must read from job_runs table, not in-memory queue."""
    tc, db = client
    run = MagicMock()
    run.job_id = "test-job-123"
    run.status = "success"
    run.rows_read = 500
    run.rows_written = 500
    run.started_at = None
    run.finished_at = None
    run.error = None
    db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=run)
    ))
    resp = tc.get("/api/v1/jobs/test-job-123")
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_runs.py::test_get_job_by_job_id_reads_from_db -v
```

Expected: FAIL (endpoint reads from in-memory queue, not DB).

- [ ] **Step 3: Update GET /jobs/{id} in main.py or runs router**

Find `GET /jobs/{id}` in `src/siphon/main.py`. Replace the in-memory lookup:

```python
# Old:
job = queue.get_job(job_id)
if job is None:
    raise HTTPException(404, "Job not found")
return job.to_status()

# New:
from sqlalchemy import select
from siphon.orm import JobRun
from siphon.models import JobStatus

async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobStatus:
    result = await db.execute(
        select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.id.desc()).limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "Job not found")
    duration_ms = None
    if run.started_at and run.finished_at:
        duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    return JobStatus(
        job_id=run.job_id,
        status=run.status,
        rows_read=run.rows_read,
        rows_written=run.rows_written,
        duration_ms=duration_ms,
        error=run.error,
    )
```

- [ ] **Step 4: Update cancel endpoint**

Find `POST /runs/{id}/cancel` in `src/siphon/runs/router.py`. Currently it sets `job.status = "cancelled"` on the in-memory `Job`. Replace with:

```python
# Revoke the Celery task (if still running) and update DB
from siphon.celery_app import app as celery_app
from siphon.orm import JobRun

run = await db.get(JobRun, run_id)
if run is None:
    raise HTTPException(404, "Run not found")
if run.status not in ("queued", "running"):
    raise HTTPException(409, f"Cannot cancel job in status '{run.status}'")

# Attempt Celery revoke (best-effort — task may have already completed)
celery_app.control.revoke(run.job_id, terminate=True, signal="SIGTERM")

run.status = "failed"
run.error = "Cancelled by user"
await db.commit()
return {"status": "cancelled"}
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_runs.py -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/siphon/main.py src/siphon/runs/router.py tests/test_runs.py
git commit -m "feat(phase-16): GET /jobs/{id} and cancel read from DB; cancel via Celery revoke"
```

---

## Task 5: Update docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add Redis and siphon-worker services**

In `docker-compose.yml`, add after the `siphon` service:

```yaml
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  siphon-worker:
    build: .
    command: >
      celery -A siphon.celery_app worker
      --loglevel=info
      -Q high,normal,low
      --concurrency=4
      --without-gossip
      --without-mingle
    environment:
      DATABASE_URL: ${DATABASE_URL}
      SIPHON_ENCRYPTION_KEY: ${SIPHON_ENCRYPTION_KEY}
      REDIS_URL: redis://redis:6379/0
    depends_on:
      redis:
        condition: service_healthy
      db:
        condition: service_healthy
    stop_grace_period: 300s
```

Also add `REDIS_URL: redis://redis:6379/0` to the `siphon` (API) service environment.

- [ ] **Step 2: Verify docker-compose config parses**

```bash
docker compose config --quiet
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(phase-16): add redis and siphon-worker to docker-compose"
```

---

## Task 6: Full test suite + Phase 16 wrap-up

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -q 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 2: Ruff lint check**

```bash
ruff check src/ tests/
```

Expected: no violations.

- [ ] **Step 3: Smoke test (optional — requires Docker)**

```bash
docker compose up -d redis db
REDIS_URL=redis://localhost:6379/0 DATABASE_URL=postgresql+asyncpg://siphon:siphon@localhost:5432/siphon \
  celery -A siphon.celery_app worker --loglevel=info -Q high,normal,low --concurrency=1 &
curl -s http://localhost:8000/health/ready | python3 -m json.tool
```

- [ ] **Step 4: Tag phase completion**

```bash
git commit --allow-empty -m "chore: phase 16 complete — Celery + Redis horizontal scale"
```
