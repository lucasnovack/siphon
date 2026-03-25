# src/siphon/main.py
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from siphon.models import ExtractRequest, Job, JobStatus, LogsResponse
from siphon.plugins.destinations import get as get_destination
from siphon.plugins.sources import get as get_source
from siphon.queue import JobQueue

logger = logging.getLogger(__name__)

# ── Config (module-level for API key; read per-request for size limit) ────────
API_KEY: str | None = os.getenv("SIPHON_API_KEY")
DRAIN_TIMEOUT = int(os.getenv("SIPHON_DRAIN_TIMEOUT", "3600"))

# ── Startup warnings ──────────────────────────────────────────────────────────
if not os.getenv("SIPHON_API_KEY"):
    logging.warning("SIPHON_API_KEY not set — API authentication is disabled")
if not os.getenv("SIPHON_ALLOWED_HOSTS"):
    logging.warning("SIPHON_ALLOWED_HOSTS not set — all hosts are permitted (SSRF risk)")

# ── Queue singleton ───────────────────────────────────────────────────────────
queue: JobQueue = JobQueue()

# ── Service start time ────────────────────────────────────────────────────────
_START_TIME = datetime.now(tz=UTC)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    queue.start()
    yield
    await queue.drain(timeout=DRAIN_TIMEOUT)


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Siphon", lifespan=lifespan)

# Suppress credential leakage in uvicorn error logs for 422 responses
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


# ── Middleware ────────────────────────────────────────────────────────────────

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    """Require Authorization: Bearer <SIPHON_API_KEY> on all routes except health probes."""
    if API_KEY and request.url.path not in ("/health/live", "/health/ready"):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def request_size_limit(request: Request, call_next):
    """Reject requests whose Content-Length exceeds SIPHON_MAX_REQUEST_SIZE_MB."""
    max_bytes = int(os.getenv("SIPHON_MAX_REQUEST_SIZE_MB", "1")) * 1024 * 1024
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_bytes:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large"},
        )
    return await call_next(request)


# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Override FastAPI's default 422 handler to never log or echo the request body.

    FastAPI's default handler includes the full parsed body in the response and logs it,
    which would expose connection strings, passwords, and S3 keys in error logs.
    """
    # Strip "input" from each error to prevent credential leakage.
    # Pydantic v2 includes the parsed field value in "input" which can contain
    # connection strings, passwords, and S3 keys verbatim.
    safe_errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": safe_errors},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job_and_plugins(req: ExtractRequest) -> tuple[Job, object, object]:
    """Resolve plugin classes from the registry and instantiate them.

    Raises HTTPException(400) if a plugin type is not registered.
    The ExtractRequest is NOT stored in the Job — credentials are dropped here.
    """
    try:
        source_cls = get_source(req.source.type)
        dest_cls = get_destination(req.destination.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = source_cls(**req.source.model_dump(exclude={"type"}))
    destination = dest_cls(**req.destination.model_dump(exclude={"type"}))
    job = Job(job_id=str(uuid.uuid4()))
    return job, source, destination


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/jobs", status_code=202)
async def create_job(req: ExtractRequest) -> dict:
    """Submit an async extraction job. Returns immediately with job_id."""
    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)
    return {"job_id": job.job_id, "status": job.status}


@app.post("/extract")
async def extract_sync(req: ExtractRequest) -> dict:
    """Synchronous extraction — blocks until job completes. For local dev and debug only.

    Do NOT use from Airflow in production — HTTP connections open for minutes are fragile.
    Use POST /jobs + polling instead.
    """
    import asyncio

    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)

    # Poll until terminal state
    while job.status in ("queued", "running"):
        await asyncio.sleep(0.05)

    duration_ms = None
    if job.started_at and job.finished_at:
        duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "rows_read": job.rows_read,
        "rows_written": job.rows_written,
        "duration_ms": duration_ms,
        "error": job.error,
        "logs": job.logs,
    }


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str) -> JobStatus:
    """Get job status by ID."""
    job = queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job.to_status()


@app.get("/jobs/{job_id}/logs", response_model=LogsResponse)
async def get_job_logs(job_id: str, since: int = 0) -> LogsResponse:
    """Get job logs from offset `since` (exclusive). Supports incremental polling.

    The SiphonOperator polls this with log_offset to stream logs without
    re-printing already-seen lines.
    """
    job = queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    logs_slice = job.logs[since:]
    return LogsResponse(
        job_id=job_id,
        logs=logs_slice,
        next_offset=since + len(logs_slice),
    )


@app.get("/health/live")
async def health_live() -> dict:
    """Kubernetes liveness probe. Returns 200 while the process is alive."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Kubernetes readiness probe. Returns 503 when queue is full or draining."""
    if queue.is_draining:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "accepting_jobs": False, "reason": "draining"},
        )
    if queue.is_full:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "accepting_jobs": False, "reason": "queue_full"},
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ok", "accepting_jobs": True},
    )


@app.get("/health")
async def health_debug() -> dict:
    """Human-readable health endpoint for operators and dashboards.

    Kubernetes uses /health/live and /health/ready instead.
    """
    uptime = int((datetime.now(tz=UTC) - _START_TIME).total_seconds())
    accepting = not (queue.is_full or queue.is_draining)
    return {
        "status": "ok" if accepting else "degraded",
        "accepting_jobs": accepting,
        "queue": queue.stats,
        "uptime_seconds": uptime,
    }


# ── Reserved endpoints (deferred to v1.1) ────────────────────────────────────

@app.get("/metrics")
async def metrics_reserved() -> dict:
    """Prometheus metrics endpoint — reserved for v1.1. Returns empty response."""
    return {}
