# src/siphon/queue.py
"""Thin Celery-backed queue wrapper.

submit() serializes source/destination configs and dispatches run_pipeline_task
to the appropriate Celery queue (high/normal/low) based on job.priority.
Job state is tracked in job_runs (PostgreSQL) by the Celery worker.
"""
import structlog
from fastapi import HTTPException

from siphon.models import Job
from siphon.tasks import _job_to_dict, run_pipeline_task

logger = structlog.get_logger()


class JobQueue:
    """Celery-backed job queue.

    submit()    → serialize + apply_async to Celery; raises 503 if Celery is unreachable
    is_full     → always False (Celery manages its own backpressure)
    is_draining → always False (no in-process drain needed)
    """

    @property
    def is_full(self) -> bool:
        return False

    @property
    def is_draining(self) -> bool:
        return False

    @property
    def stats(self) -> dict:
        return {"backend": "celery"}

    def start(self) -> None:
        logger.info("JobQueue (Celery) ready")

    def get_job(self, job_id: str) -> Job | None:
        """In Phase 16, job state lives in job_runs DB. Returns None always."""
        return None

    async def submit(
        self,
        job: Job,
        source_config: dict,
        destination_config: dict,
        max_concurrent: int = 2,
    ) -> None:
        """Dispatch a job to the Celery queue matching job.priority."""
        queue_name = job.priority if job.priority in ("high", "normal", "low") else "normal"

        try:
            run_pipeline_task.apply_async(
                args=[_job_to_dict(job), source_config, destination_config],
                queue=queue_name,
                task_id=job.job_id,
            )
        except Exception as exc:
            logger.error("Failed to enqueue job via Celery", job_id=job.job_id, error=str(exc))
            raise HTTPException(status_code=503, detail=f"Queue unavailable: {exc}") from exc

        logger.info("Job dispatched to Celery", job_id=job.job_id, queue=queue_name)

    async def drain(self, timeout: float) -> None:
        """No-op: Celery workers handle their own graceful shutdown."""
        logger.info("Celery queue drain requested (no-op)")
