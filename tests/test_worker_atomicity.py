# tests/test_worker_atomicity.py
"""Verifica que destination.promote() é chamado ANTES das operações de DB."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa
import pytest

from siphon.models import Job


_PIPELINE_UUID = "11111111-1111-1111-1111-111111111111"


def _make_batch() -> pa.RecordBatch:
    schema = pa.schema([pa.field("id", pa.int64())])
    return pa.record_batch([pa.array([1, 2, 3], type=pa.int64())], schema=schema)


@pytest.mark.asyncio
async def test_promote_called_before_db_writes():
    """promote() deve ser chamado antes de _persist_job_run e _update_pipeline_metadata."""
    from siphon.worker import run_job

    call_order = []

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([_make_batch()])

    mock_dest = MagicMock()
    mock_dest.write.return_value = 3
    mock_dest.promote.side_effect = lambda: call_order.append("promote")

    mock_pipeline = MagicMock()
    mock_pipeline.last_schema_hash = None
    mock_pipeline.last_schema = None
    mock_pipeline.last_watermark = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pipeline

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    async def fake_persist(job, db_factory):
        call_order.append("persist_job_run")

    async def fake_update_metadata(job, db_factory):
        call_order.append("update_pipeline_metadata")

    job = Job(job_id="test-atomicity", pipeline_id=_PIPELINE_UUID)

    with patch("siphon.worker._persist_job_run", side_effect=fake_persist), \
         patch("siphon.worker._update_pipeline_metadata", side_effect=fake_update_metadata):
        with ThreadPoolExecutor(max_workers=1) as executor:
            await run_job(mock_source, mock_dest, job, executor, timeout=30, db_factory=mock_factory)

    assert call_order[0] == "promote", f"promote should be first, got: {call_order}"
    assert "persist_job_run" in call_order
    assert "update_pipeline_metadata" in call_order
    promote_idx = call_order.index("promote")
    persist_idx = call_order.index("persist_job_run")
    assert promote_idx < persist_idx, f"promote ({promote_idx}) must come before persist ({persist_idx})"
