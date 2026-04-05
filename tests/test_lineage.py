# tests/test_lineage.py
"""Verifica que source_connection_id e destination_path são capturados e persistidos."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pytest

from siphon.models import Job


_PIPELINE_UUID = "11111111-1111-1111-1111-111111111111"
_CONN_UUID = "22222222-2222-2222-2222-222222222222"


def _make_batch() -> pa.RecordBatch:
    schema = pa.schema([pa.field("id", pa.int64())])
    return pa.record_batch([pa.array([1, 2, 3], type=pa.int64())], schema=schema)


@pytest.mark.asyncio
async def test_lineage_fields_persisted_to_job_run():
    """source_connection_id e destination_path são escritos em job_runs."""
    from siphon.worker import run_job

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([_make_batch()])

    mock_dest = MagicMock()
    mock_dest.write.return_value = 3

    mock_pipeline = MagicMock()
    mock_pipeline.last_schema_hash = None
    mock_pipeline.last_schema = None
    mock_pipeline.last_watermark = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pipeline

    # Capture the JobRun object that gets added to the session
    added_objects = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    job = Job(
        job_id="test-lineage",
        pipeline_id=_PIPELINE_UUID,
        source_connection_id=_CONN_UUID,
        destination_path="bronze/orders/",
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30, db_factory=mock_factory)

    assert job.status == "success"
    # Find the JobRun object that was added (INSERT path since no run_id)
    from siphon.orm import JobRun
    job_run_objects = [o for o in added_objects if isinstance(o, JobRun)]
    assert len(job_run_objects) == 1
    run_obj = job_run_objects[0]
    import uuid
    assert run_obj.source_connection_id == uuid.UUID(_CONN_UUID)
    assert run_obj.destination_path == "bronze/orders/"


@pytest.mark.asyncio
async def test_lineage_fields_persisted_to_job_run_update_path():
    """source_connection_id and destination_path are set on the existing run row (UPDATE path)."""
    from siphon.worker import _persist_job_run

    job = Job(
        job_id="test-lineage-update",
        status="success",
        run_id=42,
        source_connection_id=_CONN_UUID,
        destination_path="bronze/events/",
    )

    import uuid as _uuid
    mock_run = MagicMock()
    mock_run.id = 42

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_run

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    await _persist_job_run(job, mock_factory)

    assert mock_run.source_connection_id == _uuid.UUID(_CONN_UUID)
    assert mock_run.destination_path == "bronze/events/"


def test_job_dataclass_has_lineage_fields():
    """Job dataclass tem os campos de lineage com default None."""
    job = Job(job_id="test")
    assert job.source_connection_id is None
    assert job.destination_path is None

    job2 = Job(
        job_id="test2",
        source_connection_id=_CONN_UUID,
        destination_path="bronze/table/",
    )
    assert job2.source_connection_id == _CONN_UUID
    assert job2.destination_path == "bronze/table/"
