# tests/test_celery_tasks.py
"""Tests for Celery app configuration and task serialization."""
import pytest


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
    from siphon.tasks import _job_to_dict, _job_from_dict

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


def test_run_pipeline_task_is_registered():
    """run_pipeline_task must be registered in the Celery app."""
    from siphon.celery_app import app
    assert "siphon.tasks.run_pipeline_task" in app.tasks
