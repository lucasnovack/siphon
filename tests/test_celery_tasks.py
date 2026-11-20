# tests/test_celery_tasks.py
"""Tests for Celery app configuration and task serialization."""


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


def test_celery_app_rejects_on_worker_lost():
    """task_reject_on_worker_lost=True ensures tasks re-queue on worker crash."""
    from siphon.celery_app import app
    assert app.conf.task_reject_on_worker_lost is True


def test_celery_app_prefetch_one():
    """worker_prefetch_multiplier=1 means one task at a time per worker."""
    from siphon.celery_app import app
    assert app.conf.worker_prefetch_multiplier == 1


def test_celery_app_default_queue_is_normal():
    from siphon.celery_app import app
    assert app.conf.task_default_queue == "normal"


def test_celery_app_retries_on_startup():
    from siphon.celery_app import app
    assert app.conf.broker_connection_retry_on_startup is True


def test_job_dict_roundtrip():
    """Job can be serialized to dict and reconstructed for Celery transport."""
    from siphon.models import Job
    from siphon.tasks import _job_from_dict, _job_to_dict

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


def test_job_dict_roundtrip_with_datetimes():
    """_job_to_dict/_job_from_dict must correctly round-trip datetime fields."""
    from datetime import UTC, datetime

    from siphon.models import Job
    from siphon.tasks import _job_from_dict, _job_to_dict

    job = Job(
        job_id="dt-test",
        priority="normal",
        pipeline_id="pipe-dt",
        created_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
        started_at=datetime(2024, 1, 15, 10, 30, 5, tzinfo=UTC),
    )
    d = _job_to_dict(job)
    assert isinstance(d["started_at"], str), "started_at should be ISO string in dict"
    assert isinstance(d["created_at"], str), "created_at should be ISO string in dict"

    restored = _job_from_dict(d)
    assert restored.started_at == job.started_at
    assert restored.created_at == job.created_at


def test_run_pipeline_task_is_registered():
    """run_pipeline_task must be registered in the Celery app."""
    from siphon.celery_app import app
    assert "siphon.tasks.run_pipeline_task" in app.tasks


def test_celery_queue_submit_calls_apply_async():
    """JobQueue.submit() must call run_pipeline_task.apply_async() with correct queue."""
    import asyncio
    from unittest.mock import MagicMock, patch

    from siphon.models import Job
    from siphon.queue import JobQueue

    applied = []

    with patch("siphon.queue.run_pipeline_task") as mock_task:
        mock_task.apply_async = MagicMock(side_effect=lambda *a, **kw: applied.append(kw))
        q = JobQueue()
        asyncio.get_event_loop().run_until_complete(
            q.submit(Job(job_id="j-1", priority="high"), {}, {})
        )

    assert len(applied) == 1
    assert applied[0]["queue"] == "high"
    assert applied[0].get("task_id") == "j-1"


async def test_run_job_async_raises_when_concurrency_limit_reached():
    """_run_job_async raises ConcurrencyLimitReached when running jobs >= max_concurrent_jobs."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    import pytest

    from siphon.models import Job
    from siphon.tasks import ConcurrencyLimitReached, _run_job_async

    conn_id = str(uuid.uuid4())
    job = Job(job_id="job-new", source_connection_id=conn_id)

    mock_session = AsyncMock()
    # 1st scalar: Connection.max_concurrent_jobs -> 2; 2nd scalar: running_count -> 2
    mock_session.scalar = AsyncMock(side_effect=[2, 2])
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_factory = MagicMock(return_value=mock_ctx)
    mock_engine = AsyncMock()

    with (
        patch.dict("os.environ", {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=mock_factory),
        patch("siphon.worker.run_job", new_callable=AsyncMock) as mock_run_job,
    ):
        with pytest.raises(ConcurrencyLimitReached, match="2/2"):
            await _run_job_async(MagicMock(), MagicMock(), job, max_concurrent=2)
        mock_run_job.assert_not_awaited()
    mock_engine.dispose.assert_awaited_once()


async def test_run_job_async_proceeds_when_below_concurrency_limit():
    """_run_job_async runs the job when running jobs < max_concurrent_jobs."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from siphon.models import Job
    from siphon.tasks import _run_job_async

    conn_id = str(uuid.uuid4())
    job = Job(job_id="job-ok", source_connection_id=conn_id)

    mock_session = AsyncMock()
    # 1st scalar: Connection.max_concurrent_jobs -> 2; 2nd scalar: running_count -> 1
    mock_session.scalar = AsyncMock(side_effect=[2, 1])
    mock_exec_result = MagicMock()
    mock_exec_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_exec_result)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_factory = MagicMock(return_value=mock_ctx)
    mock_engine = AsyncMock()

    with (
        patch.dict("os.environ", {"DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db"}),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=mock_factory),
        patch("siphon.worker.run_job", new_callable=AsyncMock) as mock_run_job,
    ):
        await _run_job_async(MagicMock(), MagicMock(), job, max_concurrent=2)
        mock_run_job.assert_awaited_once()
    mock_engine.dispose.assert_awaited_once()


def test_run_pipeline_task_retries_with_backoff_on_concurrency_limit():
    """run_pipeline_task retries with exponential backoff when ConcurrencyLimitReached is raised."""
    from unittest.mock import MagicMock, patch

    import pytest

    from siphon.models import Job
    from siphon.tasks import ConcurrencyLimitReached, _job_to_dict, run_pipeline_task

    class _RetryCalled(Exception):
        pass

    job_dict = _job_to_dict(Job(job_id="j-retry", source_connection_id="conn-1"))
    fake_source_cls = MagicMock(return_value=MagicMock())
    fake_dest_cls = MagicMock(return_value=MagicMock())

    with (
        patch("siphon.plugins.sources.get", return_value=fake_source_cls),
        patch("siphon.plugins.destinations.get", return_value=fake_dest_cls),
        patch("siphon.tasks._run_job_async", side_effect=ConcurrencyLimitReached("busy")),
        patch.object(run_pipeline_task, "retry", side_effect=_RetryCalled()) as mock_retry,
        pytest.raises(_RetryCalled),
    ):
        run_pipeline_task.run(job_dict, {"type": "sql"}, {"type": "s3_parquet"}, max_concurrent=1)

    mock_retry.assert_called_once()
    _, kwargs = mock_retry.call_args
    assert kwargs["countdown"] == 15
    assert kwargs["max_retries"] == 20


async def test_run_job_async_uses_nullpool_and_redis_semaphore():
    """_run_job_async uses NullPool and acquires/releases a Redis slot when REDIS_URL is set."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    from sqlalchemy.pool import NullPool

    from siphon.models import Job
    from siphon.tasks import _run_job_async

    conn_id = str(uuid.uuid4())
    job = Job(job_id="job-redis", source_connection_id=conn_id)

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=2)
    mock_exec_result = MagicMock()
    mock_exec_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_exec_result)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_factory = MagicMock(return_value=mock_ctx)
    mock_engine = AsyncMock()

    env = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    with (
        patch.dict("os.environ", env, clear=True),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine) as mock_create,
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=mock_factory),
        patch("siphon.concurrency.acquire_connection_slot", return_value=(True, 1)) as mock_acq,
        patch("siphon.concurrency.release_connection_slot") as mock_rel,
        patch("siphon.worker.run_job", new_callable=AsyncMock) as mock_run_job,
    ):
        await _run_job_async(MagicMock(), MagicMock(), job, max_concurrent=2)
        mock_run_job.assert_awaited_once()

    assert mock_create.call_args.kwargs["poolclass"] is NullPool
    mock_acq.assert_called_once()
    mock_rel.assert_called_once_with(conn_id, "job-redis")
    mock_engine.dispose.assert_awaited_once()


async def test_run_job_async_raises_on_redis_concurrency_limit():
    """_run_job_async raises ConcurrencyLimitReached when Redis semaphore denies the slot."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock, patch

    import pytest

    from siphon.models import Job
    from siphon.tasks import ConcurrencyLimitReached, _run_job_async

    conn_id = str(uuid.uuid4())
    job = Job(job_id="job-busy", source_connection_id=conn_id)

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=2)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_session
    mock_factory = MagicMock(return_value=mock_ctx)
    mock_engine = AsyncMock()

    env = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    with (
        patch.dict("os.environ", env, clear=True),
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=mock_engine),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=mock_factory),
        patch("siphon.concurrency.acquire_connection_slot", return_value=(False, 2)),
        patch("siphon.concurrency.release_connection_slot") as mock_rel,
        patch("siphon.worker.run_job", new_callable=AsyncMock) as mock_run_job,
    ):
        with pytest.raises(ConcurrencyLimitReached, match="2/2"):
            await _run_job_async(MagicMock(), MagicMock(), job, max_concurrent=2)
        mock_run_job.assert_not_awaited()

    mock_rel.assert_not_called()
    mock_engine.dispose.assert_awaited_once()
