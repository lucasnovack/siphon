# tests/test_worker_phase9.py
"""Tests for Phase 9 additions to worker.py:
- Schema hash computation
- Data quality checks
- Schema change detection
- _persist_job_run UPDATE vs INSERT
- _update_pipeline_metadata
"""
import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa
import pytest

from siphon.models import Job
from siphon.worker import (
    _check_data_quality,
    _compute_schema_hash,
    _sync_extract_and_write,
    run_job,
)


# ── Schema hash ───────────────────────────────────────────────────────────────


def test_compute_schema_hash_deterministic():
    schema = pa.schema([("id", pa.int64()), ("name", pa.utf8())])
    h1 = _compute_schema_hash(schema)
    h2 = _compute_schema_hash(schema)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_compute_schema_hash_differs_on_type_change():
    s1 = pa.schema([("id", pa.int64())])
    s2 = pa.schema([("id", pa.int32())])
    assert _compute_schema_hash(s1) != _compute_schema_hash(s2)


def test_compute_schema_hash_differs_on_name_change():
    s1 = pa.schema([("id", pa.int64())])
    s2 = pa.schema([("user_id", pa.int64())])
    assert _compute_schema_hash(s1) != _compute_schema_hash(s2)


# ── Data quality ──────────────────────────────────────────────────────────────


def test_dq_passes_when_no_constraints():
    assert _check_data_quality({}, 0) is None
    assert _check_data_quality({}, 1_000_000) is None


def test_dq_min_rows_passes():
    assert _check_data_quality({"min_rows_expected": 10}, 10) is None
    assert _check_data_quality({"min_rows_expected": 10}, 100) is None


def test_dq_min_rows_fails():
    err = _check_data_quality({"min_rows_expected": 10}, 5)
    assert err is not None
    assert "5" in err
    assert "10" in err


def test_dq_max_drop_pct_passes():
    dq = {"max_rows_drop_pct": 20, "prev_rows": 100}
    assert _check_data_quality(dq, 85) is None  # 15% drop — ok


def test_dq_max_drop_pct_fails():
    dq = {"max_rows_drop_pct": 20, "prev_rows": 100}
    err = _check_data_quality(dq, 70)  # 30% drop — fail
    assert err is not None
    assert "30" in err


def test_dq_max_drop_skipped_when_no_prev_rows():
    dq = {"max_rows_drop_pct": 5, "prev_rows": None}
    assert _check_data_quality(dq, 0) is None


# ── _sync_extract_and_write with DQ ──────────────────────────────────────────


def _make_source(rows=5):
    table = pa.table({"id": list(range(rows)), "name": ["a"] * rows})
    src = MagicMock()
    src.extract_batches.return_value = iter([table])
    return src


def _make_dest():
    dest = MagicMock()
    dest.write.side_effect = lambda batch, is_first_chunk=True: batch.num_rows
    return dest


def test_sync_extract_sets_schema_hash():
    job = Job(job_id="j1")
    src = _make_source(3)
    dest = _make_dest()
    rows_read, rows_written = _sync_extract_and_write(job, src, dest)
    assert rows_read == 3
    assert rows_written == 3
    assert job.schema_hash is not None
    assert len(job.schema_hash) == 64


def test_sync_extract_with_dq_passes():
    job = Job(job_id="j2", pipeline_dq={"min_rows_expected": 3, "max_rows_drop_pct": None, "prev_rows": None})
    src = _make_source(5)
    dest = _make_dest()
    rows_read, rows_written = _sync_extract_and_write(job, src, dest)
    assert rows_read == 5


def test_sync_extract_with_dq_blocks_write_on_failure():
    job = Job(job_id="j3", pipeline_dq={"min_rows_expected": 100, "max_rows_drop_pct": None, "prev_rows": None})
    src = _make_source(5)
    dest = _make_dest()
    with pytest.raises(ValueError, match="Data quality"):
        _sync_extract_and_write(job, src, dest)
    # Write must NOT have been called
    dest.write.assert_not_called()


# ── Schema change detection in run_job ───────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_detects_schema_change():
    table = pa.table({"id": [1, 2]})
    src = MagicMock()
    src.extract_batches.return_value = iter([table])
    dest = MagicMock()
    dest.write.side_effect = lambda b, is_first_chunk=True: b.num_rows

    old_hash = "a" * 64  # deliberately wrong hash
    job = Job(job_id="j4", pipeline_schema_hash=old_hash)

    executor = ThreadPoolExecutor(max_workers=1)
    await run_job(src, dest, job, executor, timeout=30)

    assert job.status == "success"
    assert job.schema_hash != old_hash
    assert any("Schema change" in log for log in job.logs)


@pytest.mark.asyncio
async def test_run_job_no_schema_change_warning_when_hashes_match():
    table = pa.table({"id": [1, 2]})
    schema = table.schema
    fields = [(f.name, str(f.type)) for f in schema]
    correct_hash = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()

    src = MagicMock()
    src.extract_batches.return_value = iter([table])
    dest = MagicMock()
    dest.write.side_effect = lambda b, is_first_chunk=True: b.num_rows

    job = Job(job_id="j5", pipeline_schema_hash=correct_hash)
    executor = ThreadPoolExecutor(max_workers=1)
    await run_job(src, dest, job, executor, timeout=30)

    assert job.status == "success"
    assert not any("Schema change" in log for log in job.logs)


# ── _persist_job_run UPDATE path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_job_run_updates_existing_row():
    from siphon.worker import _persist_job_run

    existing_run = MagicMock()
    existing_run.status = "queued"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_run
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_session)

    job = Job(
        job_id="j6",
        run_id=42,
        status="success",
        rows_read=100,
        rows_written=100,
        started_at=datetime.now(tz=UTC),
        finished_at=datetime.now(tz=UTC),
    )
    await _persist_job_run(job, factory)

    assert existing_run.status == "success"
    assert existing_run.rows_read == 100
    mock_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_persist_job_run_inserts_when_no_run_id():
    from siphon.worker import _persist_job_run

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=mock_session)

    job = Job(
        job_id="j7",
        run_id=None,  # no pre-existing row
        status="success",
        rows_read=50,
        rows_written=50,
        started_at=datetime.now(tz=UTC),
        finished_at=datetime.now(tz=UTC),
    )
    await _persist_job_run(job, factory)

    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited()


# ── DQ failure marks job as failed ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_dq_failure_marks_failed_no_write():
    src = MagicMock()
    src.extract_batches.return_value = iter([pa.table({"id": [1]})])
    dest = MagicMock()
    dest.write.side_effect = lambda b, is_first_chunk=True: b.num_rows

    job = Job(
        job_id="j8",
        pipeline_dq={"min_rows_expected": 1000, "max_rows_drop_pct": None, "prev_rows": None},
    )
    executor = ThreadPoolExecutor(max_workers=1)
    await run_job(src, dest, job, executor, timeout=30)

    assert job.status == "failed"
    assert "Data quality" in job.error
    dest.write.assert_not_called()
