# src/siphon/celery_app.py
import os

from celery import Celery
from kombu import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery("siphon", broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_queues=(
        Queue("high"),
        Queue("normal"),
        Queue("low"),
    ),
    task_default_queue="normal",
    broker_connection_retry_on_startup=True,
)

# Auto-discover tasks from siphon.tasks
app.autodiscover_tasks(["siphon"])
