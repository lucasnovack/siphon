# src/siphon/main.py
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.router import limiter
from siphon.auth.router import router as auth_router
from siphon.db import get_session_factory
from siphon.models import ExtractRequest, Job, JobStatus, LogsResponse
from siphon.plugins.destinations import get as get_destination
from siphon.plugins.sources import get as get_source
from siphon.queue import JobQueue
from siphon.pipelines.router import router as pipelines_router
from siphon.users.router import router as users_router

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
DRAIN_TIMEOUT = int(os.getenv("SIPHON_DRAIN_TIMEOUT", "3600"))
ENABLE_SYNC_EXTRACT: bool = os.getenv("SIPHON_ENABLE_SYNC_EXTRACT", "false").lower() == "true"

# ── Startup warnings ──────────────────────────────────────────────────────────
if not os.getenv("SIPHON_API_KEY"):
    logging.warning("SIPHON_API_KEY not set — API authentication is disabled")
if not os.getenv("SIPHON_ALLOWED_HOSTS"):
    logging.warning("SIPHON_ALLOWED_HOSTS not set — all hosts are permitted (SSRF risk)")
if not os.getenv("SIPHON_JWT_SECRET"):
    logging.warning("SIPHON_JWT_SECRET not set — JWT tokens are signed with a dev secret (insecure)")

# ── Queue singleton ───────────────────────────────────────────────────────────
queue: JobQueue = JobQueue()

# ── Service start time ────────────────────────────────────────────────────────
_START_TIME = datetime.now(tz=UTC)


# ── Admin bootstrap ───────────────────────────────────────────────────────────
async def _create_admin_if_missing() -> None:
    """Create the bootstrap admin user if SIPHON_ADMIN_EMAIL is set and no users exist."""
    email = os.getenv("SIPHON_ADMIN_EMAIL")
    password = os.getenv("SIPHON_ADMIN_PASSWORD")
    if not email or not password:
        return
    factory = get_session_factory()
    if factory is None:
        return
    from sqlalchemy import select

    from siphon.auth.jwt_utils import hash_password
    from siphon.orm import User

    async with factory() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            return  # users already exist
        now = datetime.now(tz=UTC)
        admin = User(
            email=email,
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(admin)
        await session.commit()
        logger.info("Bootstrap admin created: %s", email)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await _create_admin_if_missing()
    queue.start()
    yield
    await queue.drain(timeout=DRAIN_TIMEOUT)


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Siphon", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Suppress credential leakage in uvicorn error logs for 422 responses
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(pipelines_router)


# ── Middleware ────────────────────────────────────────────────────────────────
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
    safe_errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": safe_errors},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _make_job_and_plugins(req: ExtractRequest) -> tuple[Job, object, object]:
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
async def create_job(
    req: ExtractRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> dict:
    """Submit an async extraction job. Returns immediately with job_id."""
    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)
    return {"job_id": job.job_id, "status": job.status}


@app.post("/extract")
async def extract_sync(
    req: ExtractRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> dict:
    """Synchronous extraction — disabled by default. Set SIPHON_ENABLE_SYNC_EXTRACT=true."""
    if not ENABLE_SYNC_EXTRACT:
        raise HTTPException(status_code=404, detail="Not Found")

    import asyncio

    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)

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
async def get_job(
    job_id: str,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> JobStatus:
    job = queue.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job.to_status()


@app.get("/jobs/{job_id}/logs", response_model=LogsResponse)
async def get_job_logs(
    job_id: str,
    since: int = 0,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> LogsResponse:
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
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
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
    return JSONResponse(status_code=200, content={"status": "ok", "accepting_jobs": True})


@app.get("/health")
async def health_debug() -> dict:
    uptime = int((datetime.now(tz=UTC) - _START_TIME).total_seconds())
    accepting = not (queue.is_full or queue.is_draining)
    return {
        "status": "ok" if accepting else "degraded",
        "accepting_jobs": accepting,
        "queue": queue.stats,
        "uptime_seconds": uptime,
    }


@app.get("/metrics")
async def metrics_reserved() -> dict:
    return {}
