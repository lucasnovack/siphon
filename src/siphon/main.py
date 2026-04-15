# src/siphon/main.py
import logging
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from siphon.auth.deps import Principal, get_current_principal
from siphon.auth.router import limiter
from siphon.auth.router import router as auth_router
from siphon.connections.router import router as connections_router
from siphon.db import get_db, get_session_factory
from siphon.gdpr.router import router as gdpr_router
from siphon.models import ExtractRequest, Job, JobStatus
from siphon.pipelines.router import router as pipelines_router
from siphon.plugins.destinations import get as get_destination
from siphon.plugins.sources import get as get_source
from siphon.preview.router import router as preview_router
from siphon.queue import JobQueue
from siphon.runs.router import router as runs_router
from siphon.users.router import router as users_router

# ── Observability ─────────────────────────────────────────────────────────────


def _otel_trace_processor(_logger, _method, event_dict: dict) -> dict:
    """Inject OTEL trace_id and span_id into every structlog event."""
    from opentelemetry import trace
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _configure_logging() -> None:
    """Configure structlog with JSON output (prod) or colored output (dev/TTY).

    All stdlib loggers (uvicorn, SQLAlchemy, APScheduler) are redirected to the
    same pipeline via ProcessorFormatter so they also emit structured JSON.
    """
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _otel_trace_processor,
    ]
    renderer = (
        structlog.dev.ConsoleRenderer()
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


def _configure_otel(app) -> None:
    """Set up OpenTelemetry tracing.

    When OTEL_EXPORTER_OTLP_ENDPOINT is set, spans are exported via gRPC OTLP.
    Otherwise a NullSpanExporter is used — trace_id is still generated and
    appears in logs, but no network calls are made.
    """
    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

    class _NoOpExporter(SpanExporter):
        def export(self, spans):
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    provider = TracerProvider()
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    else:
        provider.add_span_processor(BatchSpanProcessor(_NoOpExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)


logger = structlog.get_logger()
_configure_logging()

# ── Config ────────────────────────────────────────────────────────────────────
ENABLE_SYNC_EXTRACT: bool = os.getenv("SIPHON_ENABLE_SYNC_EXTRACT", "false").lower() == "true"

# ── Startup warnings ──────────────────────────────────────────────────────────
if not os.getenv("SIPHON_API_KEY"):
    logger.warning("SIPHON_API_KEY not set — API authentication is disabled")
if not os.getenv("SIPHON_ALLOWED_HOSTS"):
    logger.warning(
        "ssrf_protection_disabled",
        warning="SIPHON_ALLOWED_HOSTS is not set — all outbound SQL/SFTP hosts are allowed. Set this in production."
    )
if not os.getenv("SIPHON_JWT_SECRET"):
    logger.warning("SIPHON_JWT_SECRET not set — JWT tokens are signed with a dev secret (insecure)")

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
        logger.info("bootstrap_admin_created", email=email)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    from siphon.auth.jwt_utils import validate_jwt_secret
    validate_jwt_secret()
    await _create_admin_if_missing()
    from siphon.scheduler import start_scheduler
    start_scheduler()
    yield
    from siphon.scheduler import stop_scheduler
    stop_scheduler()
    from siphon.plugins.sources.http_rest import _session as _http_session
    _http_session.close()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Siphon", lifespan=lifespan)
_configure_otel(app)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(connections_router)
app.include_router(pipelines_router)
app.include_router(preview_router)
app.include_router(runs_router)
app.include_router(gdpr_router)


# ── Middleware ────────────────────────────────────────────────────────────────
@app.middleware("http")
async def _bind_request_id(request: Request, call_next):
    """Bind a unique request_id to structlog context for log correlation."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add standard security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


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
_SECRET_FIELD_RE = re.compile(
    r'("(?:password|secret|secret_key|access_key|connection|token|credentials_json)"\s*:\s*)"[^"]*"',
    re.IGNORECASE,
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    redacted = _SECRET_FIELD_RE.sub(r'\1"***"', body.decode(errors="replace"))
    logger.error(
        "request_validation_failed",
        method=request.method,
        path=request.url.path,
        body=redacted,
        errors=exc.errors(),
    )
    safe_errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": safe_errors},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _make_job_and_plugins(req: ExtractRequest) -> Job:
    try:
        get_source(req.source.type)
        get_destination(req.destination.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Job(job_id=str(uuid.uuid4()))


# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/jobs", status_code=202)
async def create_job(
    req: ExtractRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> dict:
    """Submit an async extraction job. Returns immediately with job_id."""
    job = _make_job_and_plugins(req)
    source_config = req.source.model_dump()
    dest_config = req.destination.model_dump()
    await queue.submit(job, source_config, dest_config)
    return {"job_id": job.job_id, "status": job.status}


@app.post("/extract")
async def extract_sync(
    req: ExtractRequest,
    _: Principal = Depends(get_current_principal),  # noqa: B008
) -> dict:
    """Submit an extraction job and return immediately with status "queued".

    Despite the route name, this endpoint is async: it dispatches to Celery and
    does NOT block until completion. Poll GET /runs/{job_id} for the result.
    Disabled by default — set SIPHON_ENABLE_SYNC_EXTRACT=true to enable.
    """
    if not ENABLE_SYNC_EXTRACT:
        raise HTTPException(status_code=404, detail="Not Found")

    job = _make_job_and_plugins(req)
    source_config = req.source.model_dump()
    dest_config = req.destination.model_dump()
    await queue.submit(job, source_config, dest_config)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "rows_read": job.rows_read,
        "rows_written": job.rows_written,
        "duration_ms": None,
        "error": job.error,
        "logs": job.logs,
    }


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(
    job_id: str,
    _: Principal = Depends(get_current_principal),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> JobStatus:
    from sqlalchemy import select

    from siphon.orm import JobRun

    result = await db.execute(
        select(JobRun).where(JobRun.job_id == job_id).order_by(JobRun.id.desc()).limit(1)
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    duration_ms = None
    if run.started_at and run.finished_at:
        duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)

    return JobStatus(
        job_id=run.job_id,
        status=run.status,
        rows_read=run.rows_read,
        rows_written=run.rows_written,
        duration_ms=duration_ms,
        error=run.error,
    )


@app.get("/health/live")
async def health_live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    from siphon.celery_app import app as celery_app

    try:
        celery_app.control.inspect(timeout=1).ping()
        broker_ok = True
    except Exception:
        broker_ok = False

    if not broker_ok:
        raise HTTPException(status_code=503, detail="Celery broker unavailable")

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
async def health_debug(
    principal: Principal = Depends(get_current_principal),  # noqa: B008
) -> dict:
    principal.require_admin()
    uptime = int((datetime.now(tz=UTC) - _START_TIME).total_seconds())
    accepting = not (queue.is_full or queue.is_draining)
    return {
        "status": "ok" if accepting else "degraded",
        "accepting_jobs": accepting,
        "queue": queue.stats,
        "uptime_seconds": uptime,
    }


@app.get("/metrics")
async def metrics_endpoint(
    principal: Principal = Depends(get_current_principal),  # noqa: B008
) -> Response:
    """Expose Prometheus metrics in text format."""
    principal.require_admin()
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from siphon.metrics import queue_depth

    queue_depth.set(queue.stats.get("queued", 0) + queue.stats.get("active", 0))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── SPA static files (Phase 10) ───────────────────────────────────────────────
# Served last so all /api/* and /jobs/* routes take precedence.
_FRONTEND_DIR = os.getenv("SIPHON_FRONTEND_DIR", "/app/frontend/dist")

if os.path.isdir(_FRONTEND_DIR):
    # Serve JS/CSS/assets normally; fall back to index.html for unknown paths
    # so React Router can handle client-side navigation.
    app.mount("/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="spa")
else:

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_not_built(full_path: str) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=503,
            content={"detail": "Frontend not built. Run `pnpm build` inside frontend/."},
        )
