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
