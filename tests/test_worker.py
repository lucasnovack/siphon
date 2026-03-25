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
