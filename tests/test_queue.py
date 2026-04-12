# tests/test_queue.py
"""Tests for the Celery-backed JobQueue wrapper."""
from unittest.mock import MagicMock, patch

import pytest

from siphon.models import Job
from siphon.queue import JobQueue


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
