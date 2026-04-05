# tests/test_worker_phase14.py
"""Worker integration tests for phase 14 schema validation and registry."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pytest

from siphon.models import Job


_PIPELINE_UUID = "11111111-1111-1111-1111-111111111111"


def _make_batch(schema: pa.Schema, rows: int = 5) -> pa.RecordBatch:
    arrays = []
    for field in schema:
        if pa.types.is_integer(field.type):
            arrays.append(pa.array(list(range(rows)), type=field.type))
        else:
            arrays.append(pa.array([f"v{i}" for i in range(rows)], type=field.type))
    return pa.record_batch(arrays, schema=schema)


@pytest.mark.asyncio
async def test_last_schema_saved_after_success():
    """run_job saves last_schema to the pipeline after a successful extraction."""
    from siphon.worker import run_job

    schema = pa.schema([pa.field("id", pa.int64()), pa.field("name", pa.string())])
    batch = _make_batch(schema)

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([batch])
    mock_dest = MagicMock()
    mock_dest.write.return_value = batch.num_rows

    job = Job(job_id="test-schema-save", pipeline_id=_PIPELINE_UUID)

    mock_pipeline = MagicMock()
    mock_pipeline.last_schema_hash = None
    mock_pipeline.last_schema = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pipeline

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30, db_factory=mock_factory)

    assert job.status == "success"
    assert mock_pipeline.last_schema is not None
    assert isinstance(mock_pipeline.last_schema, list)
    assert mock_pipeline.last_schema[0]["name"] == "id"


@pytest.mark.asyncio
async def test_schema_validation_fails_on_missing_column():
    """Job fails when expected_schema has a column not present in actual data."""
    from siphon.worker import run_job

    actual_schema = pa.schema([pa.field("id", pa.int64())])
    batch = _make_batch(actual_schema)

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([batch])
    mock_dest = MagicMock()
    mock_dest.write.return_value = batch.num_rows

    job = Job(
        job_id="test-schema-fail",
        pipeline_id=None,
        pipeline_expected_schema=[
            {"name": "id", "type": "int64"},
            {"name": "name", "type": "string"},  # missing from actual
        ],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30)

    assert job.status == "failed"
    assert "name" in job.error
    assert "missing" in job.error.lower()


@pytest.mark.asyncio
async def test_schema_validation_passes_with_extra_column():
    """Job succeeds when actual data has extra columns beyond expected_schema."""
    from siphon.worker import run_job

    actual_schema = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("extra", pa.string()),  # not in expected
    ])
    batch = _make_batch(actual_schema)

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([batch])
    mock_dest = MagicMock()
    mock_dest.write.return_value = batch.num_rows

    job = Job(
        job_id="test-schema-extra",
        pipeline_id=None,
        pipeline_expected_schema=[{"name": "id", "type": "int64"}],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30)

    assert job.status == "success"
