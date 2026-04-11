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
from siphon.worker import _apply_pii_masking, run_job

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


# ── Phase 11 Task 8: staging lifecycle ───────────────────────────────────────


class _StagingDest(Destination):
    """Destination that records cleanup and promote calls."""
    def __init__(self):
        self.cleanup_called = False
        self.promote_called = False
        self.cleanup_call_order = None
        self.write_call_order = None
        self._call_count = 0

    def cleanup_staging(self):
        self._call_count += 1
        self.cleanup_called = True
        self.cleanup_call_order = self._call_count

    def write(self, table, is_first_chunk: bool = True) -> int:
        self._call_count += 1
        self.write_call_order = self._call_count
        return table.num_rows

    def promote(self):
        self._call_count += 1
        self.promote_called = True
        self.promote_call_order = self._call_count


async def test_staging_lifecycle_order(executor):
    """cleanup → extract → promote is called in order on success."""
    job = Job(job_id="staging-test")
    source = _OkSource()
    dest = _StagingDest()

    await run_job(source, dest, job, executor, timeout=5)

    assert job.status == "success"
    assert dest.cleanup_called
    assert dest.promote_called
    assert dest.cleanup_call_order < dest.write_call_order < dest.promote_call_order


async def test_promote_not_called_on_failure(executor):
    """promote is NOT called when job fails."""
    job = Job(job_id="fail-test")
    dest = _StagingDest()

    await run_job(_FailSource(), dest, job, executor, timeout=5)

    assert job.status == "failed"
    assert dest.cleanup_called
    assert not dest.promote_called


async def test_cleanup_called_even_without_staging_methods(executor):
    """Worker works fine with destinations that have no staging methods."""
    job = Job(job_id="no-staging")
    await run_job(_OkSource(), _OkDest(), job, executor, timeout=5)
    assert job.status == "success"


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


# ── Phase 11 Task 11: PII masking ────────────────────────────────────────────


def test_pii_masking_sha256():
    import hashlib
    table = pa.table({"email": ["alice@example.com", "bob@example.com"], "score": [9, 8]})
    result = _apply_pii_masking(table, {"email": "sha256"})
    expected_hash = hashlib.sha256(b"alice@example.com").hexdigest()
    assert result.column("email").to_pylist()[0] == expected_hash
    assert result.column("score").to_pylist() == [9, 8]  # unchanged


def test_pii_masking_redact():
    table = pa.table({"token": ["abc123", "xyz789"], "id": [1, 2]})
    result = _apply_pii_masking(table, {"token": "redact"})
    assert result.column("token").to_pylist() == [None, None]
    assert result.column("id").to_pylist() == [1, 2]


def test_pii_masking_skips_missing_columns():
    table = pa.table({"id": [1, 2], "name": ["a", "b"]})
    result = _apply_pii_masking(table, {"email": "sha256", "phone": "redact"})
    assert result.schema.names == ["id", "name"]  # no extra columns


def test_pii_masking_multiple_columns():
    import hashlib
    table = pa.table({"email": ["a@b.com"], "cpf": ["123.456.789-00"], "value": [100]})
    result = _apply_pii_masking(table, {"email": "sha256", "cpf": "sha256", "value": "redact"})
    assert result.column("email").to_pylist()[0] == hashlib.sha256(b"a@b.com").hexdigest()
    assert result.column("cpf").to_pylist()[0] == hashlib.sha256(b"123.456.789-00").hexdigest()
    assert result.column("value").to_pylist() == [None]


async def test_worker_applies_pii_masking(executor):
    """Worker calls _apply_pii_masking before write when pipeline_pii is set."""
    import hashlib

    class EmailSource(Source):
        def extract(self) -> pa.Table:
            return pa.table({"email": ["user@example.com"], "id": [1]})

    class CaptureDest(Destination):
        def __init__(self):
            self.written = None
        def write(self, table, is_first_chunk: bool = True) -> int:
            self.written = table
            return table.num_rows

    dest = CaptureDest()
    job = Job(job_id="pii-test", pipeline_pii={"email": "sha256"})
    await run_job(EmailSource(), dest, job, executor, timeout=5)

    assert job.status == "success"
    written_emails = dest.written.column("email").to_pylist()
    assert written_emails[0] == hashlib.sha256(b"user@example.com").hexdigest()


async def test_schema_hash_captured_before_pii_masking(executor):
    """schema_hash reflects original column types, not the masked string columns."""

    class EmailSource(Source):
        def extract(self) -> pa.Table:
            return pa.table({"email": ["user@example.com"], "score": [9.5]})

    class CaptureDest(Destination):
        def __init__(self):
            self.written_schema = None
        def write(self, table, is_first_chunk: bool = True) -> int:
            self.written_schema = table.schema
            return table.num_rows

    dest = CaptureDest()
    job = Job(job_id="schema-hash-test", pipeline_pii={"email": "sha256"})
    await run_job(EmailSource(), dest, job, executor, timeout=5)

    assert job.schema_hash is not None
    # schema_hash computed from original schema (email as string, score as double)
    from siphon.worker import _compute_schema_hash
    original_schema = pa.table({"email": ["x"], "score": [1.0]}).schema
    assert job.schema_hash == _compute_schema_hash(original_schema)


# ── Phase 12 Task 2: backfill watermark guard ─────────────────────────────────


async def test_backfill_job_does_not_update_watermark(executor):
    """When job.is_backfill=True, _update_pipeline_metadata must not update last_watermark."""
    from unittest.mock import AsyncMock, patch

    import uuid as _uuid
    job = Job(job_id="backfill-1", pipeline_id=str(_uuid.uuid4()), is_backfill=True)
    source = _OkSource()
    dest = _OkDest()

    with patch("siphon.worker._update_pipeline_metadata", new_callable=AsyncMock) as mock_update:
        with patch("siphon.worker._persist_job_run", new_callable=AsyncMock):
            await run_job(source, dest, job, executor, timeout=5, db_factory=AsyncMock())

    assert job.status == "success"
    mock_update.assert_awaited_once()


async def test_normal_job_calls_update_pipeline_metadata(executor):
    """Non-backfill jobs still call _update_pipeline_metadata."""
    from unittest.mock import AsyncMock, patch

    job = Job(job_id="normal-1", pipeline_id="some-uuid")
    assert not job.is_backfill

    with patch("siphon.worker._update_pipeline_metadata", new_callable=AsyncMock) as mock_update:
        with patch("siphon.worker._persist_job_run", new_callable=AsyncMock):
            await run_job(_OkSource(), _OkDest(), job, executor, timeout=5, db_factory=AsyncMock())
    mock_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_pipeline_metadata_skips_on_backfill():
    """_update_pipeline_metadata must return without touching DB when is_backfill=True."""
    import uuid as _uuid
    from unittest.mock import AsyncMock, MagicMock
    from siphon.worker import _update_pipeline_metadata

    job = Job(job_id="x", pipeline_id=str(_uuid.uuid4()), status="success", is_backfill=True)
    mock_session = AsyncMock()
    mock_factory = MagicMock()
    mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    await _update_pipeline_metadata(job, mock_factory)
    # DB session must never be entered for a backfill job
    mock_factory.return_value.__aenter__.assert_not_awaited()


# ── Phase 12 Task 6: webhook firing ─────────────────────────────────────────────

from siphon.worker import _fire_webhook


def test_fire_webhook_posts_json():
    """_fire_webhook sends a POST with the expected payload."""
    from unittest.mock import patch, MagicMock

    with patch("httpx.post") as mock_post:
        payload = {"event": "failed", "pipeline_id": "abc", "job_id": "j1"}
        _fire_webhook("https://hooks.example.com/test", payload)

    mock_post.assert_called_once_with(
        "https://hooks.example.com/test",
        json=payload,
        timeout=5,
    )


def test_fire_webhook_silences_errors():
    """_fire_webhook never raises — network failures are logged and swallowed."""
    from unittest.mock import patch

    with patch("httpx.post", side_effect=Exception("network down")):
        _fire_webhook("https://hooks.example.com/test", {"event": "failed"})
    # No exception raised


async def test_worker_fires_webhook_on_failure(executor):
    """When job fails and pipeline_alert is set with 'failed' in alert_on, webhook fires."""
    from unittest.mock import patch, MagicMock

    job = Job(
        job_id="wh-fail",
        pipeline_alert={"webhook_url": "https://hooks.example.com/wh", "alert_on": ["failed"]},
    )

    with patch("siphon.worker._fire_webhook") as mock_wh:
        await run_job(_FailSource(), _OkDest(), job, executor, timeout=5)

    assert job.status == "failed"
    mock_wh.assert_called_once()
    call_args = mock_wh.call_args
    assert call_args.args[0] == "https://hooks.example.com/wh"
    assert call_args.args[1]["event"] == "failed"


async def test_worker_does_not_fire_webhook_on_success_without_event(executor):
    """Webhook is NOT fired when job succeeds and alert_on=['failed']."""
    from unittest.mock import patch

    job = Job(
        job_id="wh-ok",
        pipeline_alert={"webhook_url": "https://hooks.example.com/wh", "alert_on": ["failed"]},
    )

    with patch("siphon.worker._fire_webhook") as mock_wh:
        await run_job(_OkSource(), _OkDest(), job, executor, timeout=5)

    assert job.status == "success"
    mock_wh.assert_not_called()


async def test_worker_fires_webhook_on_schema_change(executor):
    """Webhook fires with event='schema_changed' when schema hash changed."""
    import hashlib, json
    from unittest.mock import patch

    old_schema = [("x", "int64")]
    old_hash = hashlib.sha256(json.dumps(old_schema, sort_keys=True).encode()).hexdigest()

    job = Job(
        job_id="wh-schema",
        pipeline_schema_hash=old_hash,
        pipeline_alert={"webhook_url": "https://hooks.example.com/wh", "alert_on": ["schema_changed"]},
    )

    class NewSchemaSource(Source):
        def extract(self):
            return pa.table({"y": [1, 2, 3]})  # different schema

    with patch("siphon.worker._fire_webhook") as mock_wh:
        await run_job(NewSchemaSource(), _OkDest(), job, executor, timeout=5)

    mock_wh.assert_called_once()
    assert mock_wh.call_args.args[1]["event"] == "schema_changed"


def test_worker_fires_both_events_on_failed_schema_change():
    """When job fails AND schema changed, both 'failed' and 'schema_changed' fire independently."""
    from unittest.mock import patch
    from siphon.worker import _maybe_fire_webhook
    from siphon.models import Job as _Job

    job = _Job(
        job_id="wh-both-direct",
        pipeline_schema_hash="old_hash_aaaa",
        pipeline_alert={
            "webhook_url": "https://hooks.example.com/wh",
            "alert_on": ["failed", "schema_changed"],
        },
        status="failed",
        schema_hash="new_hash_bbbb",
    )

    with patch("siphon.worker._fire_webhook") as mock_wh:
        _maybe_fire_webhook(job)

    assert mock_wh.call_count == 2
    fired_events = {call.args[1]["event"] for call in mock_wh.call_args_list}
    assert fired_events == {"failed", "schema_changed"}
