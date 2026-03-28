# tests/test_worker.py
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pytest

from siphon.models import Job
from siphon.orm import JobRun
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
    await run_job(_OkSource(), _OkDest(), job, executor, timeout=5)
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
    await run_job(_WatchedSource(), _OkDest(), job, executor, timeout=5)
    assert "running" in statuses
    assert job.status == "success"


async def test_run_job_exception_marks_failed(executor):
    job = Job(job_id="j-fail")
    await run_job(_FailSource(), _OkDest(), job, executor, timeout=5)
    assert job.status == "failed"
    assert "connection refused" in job.error
    assert job.finished_at is not None


async def test_run_job_timeout_marks_failed(executor):
    job = Job(job_id="j-timeout")
    await run_job(_SlowSource(), _OkDest(), job, executor, timeout=0.1)
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
    await run_job(_ChunkedSource(), _CountDest(), job, executor, timeout=5)
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
    await run_job(_MultiChunk(), _FlagDest(), job, executor, timeout=5)
    assert chunks_received == [True, False, False]


# ── Phase 8 addition: job_runs persistence ────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_persists_job_run_on_success():
    """When db_factory is provided, run_job persists a JobRun after success."""
    class _GoodSource(Source):
        def extract(self): return pa.table({"x": [1, 2, 3]})

    class _GoodDest(Destination):
        def write(self, table, is_first_chunk=True): return table.num_rows

    job = Job(job_id=str(uuid.uuid4()))

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    ex = ThreadPoolExecutor(max_workers=1)
    await run_job(_GoodSource(), _GoodDest(), job, ex, timeout=30, db_factory=mock_factory)

    assert job.status == "success"
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()
    added_obj = mock_session.add.call_args[0][0]
    assert isinstance(added_obj, JobRun)
    assert added_obj.job_id == job.job_id
    assert added_obj.status == "success"
    assert added_obj.rows_read == 3
    ex.shutdown(wait=False)


@pytest.mark.asyncio
async def test_run_job_persists_job_run_on_failure():
    """Persistence happens even when the job fails."""
    class _FailSrc(Source):
        def extract(self): raise RuntimeError("source exploded")

    class _NoopDest(Destination):
        def write(self, table, is_first_chunk=True): return 0

    job = Job(job_id=str(uuid.uuid4()))

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    ex = ThreadPoolExecutor(max_workers=1)
    await run_job(_FailSrc(), _NoopDest(), job, ex, timeout=30, db_factory=mock_factory)

    assert job.status == "failed"
    mock_session.add.assert_called_once()
    added_obj = mock_session.add.call_args[0][0]
    assert added_obj.status == "failed"
    assert added_obj.error == "source exploded"
    ex.shutdown(wait=False)


@pytest.mark.asyncio
async def test_run_job_skips_persistence_when_no_db_factory():
    """When db_factory=None, run_job completes without error."""
    class _GoodSource(Source):
        def extract(self): return pa.table({"x": [1]})

    class _GoodDest(Destination):
        def write(self, table, is_first_chunk=True): return table.num_rows

    job = Job(job_id=str(uuid.uuid4()))
    ex = ThreadPoolExecutor(max_workers=1)
    await run_job(_GoodSource(), _GoodDest(), job, ex, timeout=30, db_factory=None)
    assert job.status == "success"
    ex.shutdown(wait=False)


@pytest.mark.asyncio
async def test_run_job_persistence_failure_does_not_affect_job_status():
    """DB write failure must not change job.status — extraction already succeeded."""
    class _GoodSource(Source):
        def extract(self): return pa.table({"x": [1]})

    class _GoodDest(Destination):
        def write(self, table, is_first_chunk=True): return table.num_rows

    job = Job(job_id=str(uuid.uuid4()))

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock(side_effect=Exception("DB is down"))

    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    ex = ThreadPoolExecutor(max_workers=1)
    await run_job(_GoodSource(), _GoodDest(), job, ex, timeout=30, db_factory=mock_factory)

    assert job.status == "success"
    ex.shutdown(wait=False)
