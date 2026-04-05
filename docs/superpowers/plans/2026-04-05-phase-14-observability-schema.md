# Phase 14 — Observability & Schema Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured JSON logging (structlog), OpenTelemetry tracing (no-op by default), schema registry (Arrow schema as JSONB), and DQ schema validation (expected_schema contract) to close four remaining production gaps.

**Architecture:** Two vertical slices. Observability: `main.py` configures structlog + OTEL, `worker.py` binds `job_id`/`pipeline_id` as contextvars and wraps each job in an OTEL span, all other modules swap `logging.getLogger` → `structlog.get_logger`. Schema: migration 006 adds `last_schema` + `expected_schema` JSONB columns to `pipelines`, worker serializes + validates Arrow schema, pipeline API exposes both fields.

**Tech Stack:** `structlog>=24.4`, `opentelemetry-sdk>=1.24`, `opentelemetry-instrumentation-fastapi>=0.45b0` (main deps); `opentelemetry-exporter-otlp-proto-grpc>=1.24` (optional `[otel-otlp]` group); PyArrow (already installed).

---

## File Map

| File | Action | Change |
|------|--------|--------|
| `pyproject.toml` | Modify | Add structlog + OTEL deps |
| `src/siphon/main.py` | Modify | `_otel_trace_processor`, `_configure_logging`, `_configure_otel`, `_bind_request_id` middleware |
| `src/siphon/worker.py` | Modify | structlog, contextvars binding, OTEL span, `_schema_to_dict`, `_check_schema`, update `_update_pipeline_metadata` |
| `src/siphon/models.py` | Modify | `Job.pipeline_expected_schema` field |
| `src/siphon/orm.py` | Modify | `Pipeline.last_schema`, `Pipeline.expected_schema` JSONB fields |
| `src/siphon/pipelines/router.py` | Modify | `last_schema`/`expected_schema` in Pydantic models, `_to_response`, `trigger_pipeline` |
| `src/siphon/scheduler.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/auth/deps.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/auth/jwt_utils.py` | Modify | `_logging.getLogger` → `structlog.get_logger` |
| `src/siphon/utils/retry.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/queue.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/db.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/plugins/destinations/s3_parquet.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/plugins/destinations/bigquery_dest.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/plugins/destinations/snowflake_dest.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/plugins/sources/sql.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/plugins/sources/sftp.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `src/siphon/plugins/sources/http_rest.py` | Modify | `logging.getLogger` → `structlog.get_logger` |
| `alembic/versions/006_add_schema_registry.py` | Create | Add `last_schema`, `expected_schema` columns |
| `tests/test_logging.py` | Create | `_otel_trace_processor` unit tests, contextvars binding |
| `tests/test_schema_registry.py` | Create | `_schema_to_dict` round-trip, `_check_schema` rules |
| `tests/test_worker_phase14.py` | Create | Worker DQ schema validation integration |

---

## Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add structlog and OTEL to pyproject.toml**

In `pyproject.toml`, add to the `dependencies` list (keep alphabetical order):

```toml
dependencies = [
    "apscheduler[sqlalchemy]>=3.10",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "bcrypt>=3.2,<4.0",
    "connectorx>=0.3",
    "cryptography>=42.0",
    "fastapi>=0.115",
    "fastavro>=1.9",
    "opentelemetry-instrumentation-fastapi>=0.45b0",
    "opentelemetry-sdk>=1.24",
    "oracledb>=2.4",
    "pandas>=2.2",
    "paramiko>=3.5",
    "passlib[bcrypt]>=1.7",
    "prometheus-client>=0.20",
    "psycopg2-binary>=2.9",
    "pyarrow>=18.0",
    "pydantic>=2.9",
    "pyjwt>=2.8",
    "python-dateutil>=2.9",
    "requests>=2.32",
    "slowapi>=0.1",
    "sqlalchemy[asyncio]>=2.0",
    "structlog>=24.4",
    "uvicorn[standard]>=0.32",
]
```

Also add an `[otel-otlp]` optional dependency group after `[snowflake]`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.8",
]
gcp = [
    "google-cloud-bigquery[pandas]>=3.27",
]
snowflake = [
    "snowflake-connector-python[pandas]>=3.12",
]
otel-otlp = [
    "opentelemetry-exporter-otlp-proto-grpc>=1.24",
]
```

- [ ] **Step 2: Sync dependencies**

```bash
uv sync
```

Expected: resolves without conflicts. `structlog` and `opentelemetry-*` packages appear in `.venv/lib/`.

- [ ] **Step 3: Verify imports work**

```bash
uv run python -c "import structlog; from opentelemetry import trace; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(phase-14): add structlog and opentelemetry deps"
```

---

## Task 2: _otel_trace_processor + structlog/OTEL setup in main.py

**Files:**
- Modify: `src/siphon/main.py`
- Create: `tests/test_logging.py`

- [ ] **Step 1: Write failing tests for _otel_trace_processor**

Create `tests/test_logging.py`:

```python
# tests/test_logging.py
"""Tests for structlog configuration and OTEL trace processor."""
from unittest.mock import MagicMock, patch

import structlog


def test_otel_processor_injects_trace_id_when_span_active():
    """_otel_trace_processor adds trace_id and span_id to event dict when a valid span exists."""
    from siphon.main import _otel_trace_processor

    mock_span = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.is_valid = True
    mock_ctx.trace_id = 0x4BF92F3577B34DA6A3CE929D0E0E4736
    mock_ctx.span_id = 0x00F067AA0BA902B7
    mock_span.get_span_context.return_value = mock_ctx

    event_dict = {"event": "hello"}
    with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
        result = _otel_trace_processor(None, None, event_dict)

    assert result["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert result["span_id"] == "00f067aa0ba902b7"


def test_otel_processor_noop_when_no_active_span():
    """_otel_trace_processor leaves event dict unchanged when no valid span is active."""
    from siphon.main import _otel_trace_processor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry import trace

    # Ensure no span is active
    trace.set_tracer_provider(TracerProvider())
    event_dict = {"event": "hello"}
    result = _otel_trace_processor(None, None, event_dict)

    assert "trace_id" not in result
    assert "span_id" not in result


def test_structlog_contextvars_bind_and_clear():
    """Bound contextvars appear in structlog event dicts and are cleared independently."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(job_id="abc-123", pipeline_id="pipe-456")

    bound = structlog.contextvars.get_contextvars()
    assert bound["job_id"] == "abc-123"
    assert bound["pipeline_id"] == "pipe-456"

    structlog.contextvars.clear_contextvars()
    assert structlog.contextvars.get_contextvars() == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_logging.py -v
```

Expected: `FAILED` — `ImportError: cannot import name '_otel_trace_processor' from 'siphon.main'`

- [ ] **Step 3: Add _otel_trace_processor, _configure_logging, _configure_otel, and _bind_request_id to main.py**

At the top of `src/siphon/main.py`, replace:

```python
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
```

with:

```python
import logging
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
```

Then, immediately after the imports and before the `logger = ...` line, add these four functions:

```python
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
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, NullSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    provider = TracerProvider()
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    else:
        provider.add_span_processor(BatchSpanProcessor(NullSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
```

Replace:

```python
logger = logging.getLogger(__name__)
```

with:

```python
logger = structlog.get_logger()
```

Call `_configure_logging()` at module level, right after the `logger` line:

```python
logger = structlog.get_logger()
_configure_logging()
```

Update the startup warning calls to use the structlog logger (keyword args style):

```python
if not os.getenv("SIPHON_API_KEY"):
    logger.warning("SIPHON_API_KEY not set — API authentication is disabled")
if not os.getenv("SIPHON_ALLOWED_HOSTS"):
    logger.warning("SIPHON_ALLOWED_HOSTS not set — all hosts are permitted (SSRF risk)")
if not os.getenv("SIPHON_JWT_SECRET"):
    logger.warning("SIPHON_JWT_SECRET not set — JWT tokens are signed with a dev secret (insecure)")
```

Remove this line (now handled inside `_configure_logging`):

```python
# Suppress credential leakage in uvicorn error logs for 422 responses
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
```

After `app = FastAPI(title="Siphon", lifespan=lifespan)`, call:

```python
_configure_otel(app)
```

Add the request_id middleware after the existing middlewares:

```python
@app.middleware("http")
async def _bind_request_id(request: Request, call_next):
    """Bind a unique request_id to structlog context for log correlation."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
    return await call_next(request)
```

Update the existing `validation_exception_handler` to use keyword-style structlog call:

```python
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
```

Update `_create_admin_if_missing` log call:

```python
logger.info("bootstrap_admin_created", email=email)
```

- [ ] **Step 4: Run the logging tests**

```bash
uv run pytest tests/test_logging.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 352 passed (same count as before), 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/siphon/main.py tests/test_logging.py
git commit -m "feat(phase-14): structlog + OTEL setup; _otel_trace_processor"
```

---

## Task 3: Migrate All Modules from logging to structlog

**Files:**
- Modify: `src/siphon/scheduler.py`, `src/siphon/auth/deps.py`, `src/siphon/auth/jwt_utils.py`, `src/siphon/utils/retry.py`, `src/siphon/queue.py`, `src/siphon/db.py`, `src/siphon/pipelines/router.py`, `src/siphon/plugins/destinations/s3_parquet.py`, `src/siphon/plugins/destinations/bigquery_dest.py`, `src/siphon/plugins/destinations/snowflake_dest.py`, `src/siphon/plugins/sources/sql.py`, `src/siphon/plugins/sources/sftp.py`, `src/siphon/plugins/sources/http_rest.py`

The change in each file is the same two-line swap. No call-site changes are needed — structlog's `BoundLogger` accepts positional `%s`-style args for backward compatibility.

- [ ] **Step 1: Apply the swap to all 13 files**

In each file listed below, find and replace:

```python
import logging
...
logger = logging.getLogger(__name__)
```

→

```python
import structlog
...
logger = structlog.get_logger()
```

Files to update (one by one):

1. `src/siphon/scheduler.py` — line 18: `logger = logging.getLogger(__name__)`
2. `src/siphon/auth/deps.py` — line 17: `logger = logging.getLogger(__name__)`
3. `src/siphon/utils/retry.py` — line 6: `logger = logging.getLogger(__name__)`
4. `src/siphon/queue.py` — line 15: `logger = logging.getLogger(__name__)`
5. `src/siphon/db.py` — line 8: `logger = logging.getLogger(__name__)`
6. `src/siphon/pipelines/router.py` — line 21: `logger = logging.getLogger(__name__)`
7. `src/siphon/plugins/destinations/s3_parquet.py` — line 13
8. `src/siphon/plugins/destinations/bigquery_dest.py` — line 11
9. `src/siphon/plugins/destinations/snowflake_dest.py` — line 10
10. `src/siphon/plugins/sources/sql.py` — line 17
11. `src/siphon/plugins/sources/sftp.py` — line 18
12. `src/siphon/plugins/sources/http_rest.py` — line 12

For `src/siphon/auth/jwt_utils.py`, the existing code uses a local alias `_logging`:

```python
import logging as _logging
...
_logging.getLogger(__name__).critical(...)
```

Replace with:

```python
import structlog as _structlog
...
_structlog.get_logger().critical(...)
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 352 passed, 0 failures.

- [ ] **Step 3: Check ruff passes**

```bash
uv run ruff check src/
```

Expected: no violations.

- [ ] **Step 4: Commit**

```bash
git add src/
git commit -m "refactor(phase-14): migrate all modules to structlog.get_logger"
```

---

## Task 4: Worker — Context Binding and OTEL Span

**Files:**
- Modify: `src/siphon/worker.py`

- [ ] **Step 1: Replace logging import and logger in worker.py**

Replace:

```python
import logging
...
logger = logging.getLogger(__name__)
```

with:

```python
import structlog
from opentelemetry import trace as _otel_trace
...
logger = structlog.get_logger()
_tracer = _otel_trace.get_tracer("siphon.worker")
```

- [ ] **Step 2: Bind contextvars and wrap run_job with OTEL span**

In `run_job`, add context binding and span at the start of the function body. Replace the current opening of `run_job`:

```python
async def run_job(
    source: Source,
    destination: Destination,
    job: Job,
    executor: ThreadPoolExecutor,
    timeout: int,
    db_factory=None,
) -> None:
    loop = asyncio.get_running_loop()
    job.status = "running"
    job.started_at = datetime.now(tz=UTC)
    logger.info("Job %s started (pipeline=%s)", job.job_id, job.pipeline_id or "none")
```

with:

```python
async def run_job(
    source: Source,
    destination: Destination,
    job: Job,
    executor: ThreadPoolExecutor,
    timeout: int,
    db_factory=None,
) -> None:
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        job_id=job.job_id,
        pipeline_id=job.pipeline_id,
    )
    with _tracer.start_as_current_span("siphon.job") as span:
        span.set_attribute("job_id", job.job_id)
        if job.pipeline_id:
            span.set_attribute("pipeline_id", job.pipeline_id)
        await _run_job_inner(source, destination, job, executor, timeout, db_factory)
```

Then rename the existing body of `run_job` (everything below the original opening) into a new private coroutine `_run_job_inner` with the same signature. The entire content of the current `run_job` body (loop = ..., job.status = ..., try/except/finally) moves into `_run_job_inner`:

```python
async def _run_job_inner(
    source: Source,
    destination: Destination,
    job: Job,
    executor: ThreadPoolExecutor,
    timeout: int,
    db_factory=None,
) -> None:
    loop = asyncio.get_running_loop()
    job.status = "running"
    job.started_at = datetime.now(tz=UTC)
    logger.info("job_started", pipeline=job.pipeline_id or "none")

    try:
        rows_read, rows_written = await asyncio.wait_for(
            loop.run_in_executor(executor, _sync_extract_and_write, job, source, destination),
            timeout=timeout,
        )
        # ... rest of existing body unchanged (copy verbatim) ...
```

Update remaining `logger.info/warning/error` calls in `_run_job_inner` to use keyword args (optional but preferred):

```python
# Before:
logger.info("Job %s succeeded: %d rows", job.job_id, rows_read)
# After:
logger.info("job_succeeded", rows=rows_read)
```

```python
# Before:
logger.error("Job %s timed out after %ss", job.job_id, timeout)
# After:
logger.error("job_timed_out", timeout_s=timeout)
```

```python
# Before:
logger.error("Job %s failed (DQ): %s", job.job_id, exc)
# After:
logger.error("job_failed_dq", error=str(exc))
```

```python
# Before:
logger.error("Job %s failed: %s", job.job_id, exc)
# After:
logger.error("job_failed", error=str(exc))
```

```python
# Before:
logger.warning("Job %s partial success: %d files failed", job.job_id, len(failed_files))
# After:
logger.warning("job_partial_success", failed_files=len(failed_files))
```

```python
# Before:
logger.error("Job %s row count mismatch: read=%d written=%d", job.job_id, rows_read, rows_written)
# After:
logger.error("job_row_count_mismatch", rows_read=rows_read, rows_written=rows_written)
```

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 352 passed, 0 failures.

- [ ] **Step 4: Commit**

```bash
git add src/siphon/worker.py
git commit -m "feat(phase-14): bind job_id/pipeline_id context; OTEL span per job"
```

---

## Task 5: Migration 006 + ORM Fields

**Files:**
- Create: `alembic/versions/006_add_schema_registry.py`
- Modify: `src/siphon/orm.py`

- [ ] **Step 1: Write failing ORM test**

In `tests/test_orm.py`, add at the end:

```python
def test_pipeline_has_last_schema_and_expected_schema():
    """Pipeline ORM model has the two new JSONB fields."""
    from siphon.orm import Pipeline
    import sqlalchemy as sa

    cols = {c.key: c for c in Pipeline.__table__.columns}
    assert "last_schema" in cols
    assert "expected_schema" in cols
    # Both are nullable JSONB
    assert cols["last_schema"].nullable is True
    assert cols["expected_schema"].nullable is True
```

- [ ] **Step 2: Run to confirm it fails**

```bash
uv run pytest tests/test_orm.py::test_pipeline_has_last_schema_and_expected_schema -v
```

Expected: FAILED — `AssertionError: assert 'last_schema' in cols`

- [ ] **Step 3: Add ORM fields to Pipeline**

In `src/siphon/orm.py`, add two lines after `pii_columns`:

```python
    pii_columns: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_schema: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    expected_schema: Mapped[list | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 4: Run ORM test**

```bash
uv run pytest tests/test_orm.py::test_pipeline_has_last_schema_and_expected_schema -v
```

Expected: PASSED

- [ ] **Step 5: Create migration 006**

Create `alembic/versions/006_add_schema_registry.py`:

```python
"""Add last_schema and expected_schema to pipelines

Revision ID: 006
Revises: 005
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipelines", sa.Column("last_schema", JSONB, nullable=True))
    op.add_column("pipelines", sa.Column("expected_schema", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("pipelines", "expected_schema")
    op.drop_column("pipelines", "last_schema")
```

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 353 passed (1 new), 0 failures.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/006_add_schema_registry.py src/siphon/orm.py tests/test_orm.py
git commit -m "feat(phase-14): migration 006 + ORM last_schema/expected_schema on Pipeline"
```

---

## Task 6: _schema_to_dict and _check_schema

**Files:**
- Modify: `src/siphon/worker.py`
- Create: `tests/test_schema_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_schema_registry.py`:

```python
# tests/test_schema_registry.py
"""Tests for _schema_to_dict and _check_schema in worker.py."""
import pyarrow as pa
import pytest


def test_schema_to_dict_round_trip():
    """_schema_to_dict converts Arrow schema to list of dicts preserving name, type, nullable."""
    from siphon.worker import _schema_to_dict

    schema = pa.schema([
        pa.field("id", pa.int64(), nullable=False),
        pa.field("name", pa.string(), nullable=True),
        pa.field("score", pa.float64(), nullable=True),
    ])
    result = _schema_to_dict(schema)

    assert result == [
        {"name": "id", "type": "int64", "nullable": False},
        {"name": "name", "type": "string", "nullable": True},
        {"name": "score", "type": "double", "nullable": True},
    ]


def test_schema_to_dict_empty_schema():
    """Empty schema returns empty list."""
    from siphon.worker import _schema_to_dict

    result = _schema_to_dict(pa.schema([]))
    assert result == []


def test_check_schema_passes_when_expected_matches():
    """_check_schema returns None when actual schema satisfies expected."""
    from siphon.worker import _check_schema

    actual = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("name", pa.string()),
    ])
    expected = [
        {"name": "id", "type": "int64"},
        {"name": "name", "type": "string"},
    ]
    assert _check_schema(actual, expected) is None


def test_check_schema_error_on_missing_column():
    """_check_schema returns error string when expected column is absent from actual."""
    from siphon.worker import _check_schema

    actual = pa.schema([pa.field("id", pa.int64())])
    expected = [{"name": "id", "type": "int64"}, {"name": "name", "type": "string"}]

    result = _check_schema(actual, expected)
    assert result is not None
    assert "name" in result


def test_check_schema_error_on_type_mismatch():
    """_check_schema returns error string when column type differs from expected."""
    from siphon.worker import _check_schema

    actual = pa.schema([pa.field("id", pa.string())])  # string, not int64
    expected = [{"name": "id", "type": "int64"}]

    result = _check_schema(actual, expected)
    assert result is not None
    assert "int64" in result


def test_check_schema_warning_only_for_extra_column():
    """_check_schema returns None (no error) when actual has extra columns not in expected."""
    from siphon.worker import _check_schema

    actual = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("extra_col", pa.string()),  # not in expected
    ])
    expected = [{"name": "id", "type": "int64"}]

    # Extra columns are a warning, not an error
    assert _check_schema(actual, expected) is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_schema_registry.py -v
```

Expected: FAILED — `ImportError: cannot import name '_schema_to_dict' from 'siphon.worker'`

- [ ] **Step 3: Add _schema_to_dict and _check_schema to worker.py**

In `src/siphon/worker.py`, after the `_compute_schema_hash` function and before `_check_data_quality`, add:

```python
# ── Schema registry ───────────────────────────────────────────────────────────


def _schema_to_dict(schema) -> list[dict]:
    """Serialize an Arrow schema to a JSON-safe list of field descriptors.

    Format: [{"name": "col", "type": "int64", "nullable": True}, ...]
    """
    return [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]


def _check_schema(actual, expected: list[dict]) -> str | None:
    """Validate *actual* Arrow schema against *expected* field descriptors.

    Rules:
    - Missing column (in expected but not actual): error
    - Type mismatch: error
    - Extra column (in actual but not expected): warning log only, no error

    Returns an error message string on failure, None on success.
    """
    expected_map = {f["name"]: f["type"] for f in expected}
    actual_map = {field.name: str(field.type) for field in actual}

    missing = [n for n in expected_map if n not in actual_map]
    if missing:
        return f"Schema validation failed: missing columns {missing}"

    mismatched = [
        f"{n}: expected {expected_map[n]!r}, got {actual_map[n]!r}"
        for n in expected_map
        if n in actual_map and actual_map[n] != expected_map[n]
    ]
    if mismatched:
        return f"Schema validation failed: type mismatch [{', '.join(mismatched)}]"

    extra = [n for n in actual_map if n not in expected_map]
    if extra:
        logger.warning("extra_columns_not_in_expected_schema", columns=extra)

    return None
```

- [ ] **Step 4: Run schema tests**

```bash
uv run pytest tests/test_schema_registry.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 358 passed, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add src/siphon/worker.py tests/test_schema_registry.py
git commit -m "feat(phase-14): _schema_to_dict and _check_schema in worker"
```

---

## Task 7: Wire Schema into Worker Flow

**Files:**
- Modify: `src/siphon/worker.py`
- Modify: `src/siphon/models.py`
- Create: `tests/test_worker_phase14.py`

- [ ] **Step 1: Add pipeline_expected_schema to Job dataclass**

In `src/siphon/models.py`, add after `pipeline_alert`:

```python
    pipeline_alert: dict | None = None  # {webhook_url: str, alert_on: list[str]}
    pipeline_expected_schema: list[dict] | None = None  # expected Arrow schema for DQ
    schema_hash: str | None = None      # computed during extraction; written to job_runs
```

(Remove the existing `schema_hash` line from its current position and keep it at the end, after `pipeline_expected_schema`.)

- [ ] **Step 2: Write failing tests for worker schema integration**

Create `tests/test_worker_phase14.py`:

```python
# tests/test_worker_phase14.py
"""Worker integration tests for phase 14 schema validation and registry."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa
import pytest

from siphon.models import Job


_PIPELINE_UUID = "11111111-1111-1111-1111-111111111111"


def _make_batch(schema: pa.Schema, rows: int = 5) -> pa.RecordBatch:
    arrays = []
    for field in schema:
        if pa.types.is_integer(field.type):
            arrays.append(pa.array(list(range(rows)), type=field.type))
        else:
            arrays.append(pa.array([f"v{i}" for i in range(rows)], type=field.type))
    return pa.record_batch(arrays, schema=schema)


@pytest.mark.asyncio
async def test_last_schema_saved_after_success():
    """run_job calls _update_pipeline_metadata, which saves last_schema to the pipeline."""
    from siphon.worker import run_job

    schema = pa.schema([pa.field("id", pa.int64()), pa.field("name", pa.string())])
    batch = _make_batch(schema)

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([batch])
    mock_dest = MagicMock()
    mock_dest.write.return_value = batch.num_rows

    job = Job(job_id="test-schema-save", pipeline_id=_PIPELINE_UUID)

    mock_pipeline = MagicMock()
    mock_pipeline.last_schema_hash = None
    mock_pipeline.last_schema = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pipeline

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_factory = MagicMock(return_value=mock_session)

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30, db_factory=mock_factory)

    assert job.status == "success"
    # last_schema was set on the pipeline object
    assert mock_pipeline.last_schema is not None
    assert isinstance(mock_pipeline.last_schema, list)
    assert mock_pipeline.last_schema[0]["name"] == "id"


@pytest.mark.asyncio
async def test_schema_validation_fails_on_missing_column():
    """Job fails with 'schema validation failed' when expected_schema has a column not in actual."""
    from siphon.worker import run_job

    actual_schema = pa.schema([pa.field("id", pa.int64())])
    batch = _make_batch(actual_schema)

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([batch])
    mock_dest = MagicMock()
    mock_dest.write.return_value = batch.num_rows

    job = Job(
        job_id="test-schema-fail",
        pipeline_id=None,
        pipeline_expected_schema=[
            {"name": "id", "type": "int64"},
            {"name": "name", "type": "string"},  # missing from actual
        ],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30)

    assert job.status == "failed"
    assert "name" in job.error
    assert "missing" in job.error.lower()


@pytest.mark.asyncio
async def test_schema_validation_passes_with_extra_column():
    """Job succeeds when actual has extra columns not in expected_schema."""
    from siphon.worker import run_job

    actual_schema = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("extra", pa.string()),  # not in expected
    ])
    batch = _make_batch(actual_schema)

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([batch])
    mock_dest = MagicMock()
    mock_dest.write.return_value = batch.num_rows

    job = Job(
        job_id="test-schema-extra",
        pipeline_id=None,
        pipeline_expected_schema=[{"name": "id", "type": "int64"}],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30)

    assert job.status == "success"
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
uv run pytest tests/test_worker_phase14.py -v
```

Expected: FAILED — `test_last_schema_saved_after_success` fails because `last_schema` is not being set; schema validation tests fail because `_check_schema` isn't called.

- [ ] **Step 4: Update _sync_extract_and_write to validate schema and capture it**

In `src/siphon/worker.py`, `_sync_extract_and_write` needs to:
1. Capture the actual Arrow schema after reading the first batch
2. Run `_check_schema` if `job.pipeline_expected_schema` is set
3. Store the schema in a new attribute `job.actual_schema` so `_update_pipeline_metadata` can use it

Add `import pyarrow as pa` at the top of `worker.py` (lazy import inside function is fine too — keep it lazy to avoid import-time failure without pyarrow).

In `_sync_extract_and_write`, after capturing `job.schema_hash` from the first batch, add schema validation. The DQ path (buffered) becomes:

```python
    if dq is not None:
        batches = list(source.extract_batches())
        if not batches:
            dq_error = _check_data_quality(dq, 0)
            if dq_error:
                raise ValueError(dq_error)
            return 0, 0

        job.schema_hash = _compute_schema_hash(batches[0].schema)

        # Schema validation (expected_schema DQ check)
        if job.pipeline_expected_schema:
            schema_error = _check_schema(batches[0].schema, job.pipeline_expected_schema)
            if schema_error:
                raise ValueError(schema_error)

        # Store schema for registry update
        job._actual_schema = batches[0].schema

        rows_read = sum(b.num_rows for b in batches)
        dq_error = _check_data_quality(dq, rows_read)
        if dq_error:
            raise ValueError(dq_error)

        rows_written = 0
        for i, batch in enumerate(batches):
            if job.pipeline_pii:
                batch = _apply_pii_masking(batch, job.pipeline_pii)
            rows_written += destination.write(batch, is_first_chunk=(i == 0))
        return rows_read, rows_written
```

The non-DQ streaming path becomes:

```python
    rows_read = 0
    rows_written = 0
    for i, batch in enumerate(source.extract_batches()):
        if i == 0:
            job.schema_hash = _compute_schema_hash(batch.schema)
            # Schema validation
            if job.pipeline_expected_schema:
                schema_error = _check_schema(batch.schema, job.pipeline_expected_schema)
                if schema_error:
                    raise ValueError(schema_error)
            # Store schema for registry
            job._actual_schema = batch.schema
        rows_read += batch.num_rows
        if job.pipeline_pii:
            batch = _apply_pii_masking(batch, job.pipeline_pii)
        rows_written += destination.write(batch, is_first_chunk=(i == 0))
    return rows_read, rows_written
```

Note: `job._actual_schema` is a temporary attribute set during extraction. It's not declared in the dataclass (it's transient, only needed within the same run_job call). Add `_actual_schema: object = field(default=None)` to the `Job` dataclass in `models.py`:

```python
    schema_hash: str | None = None      # computed during extraction; written to job_runs
    _actual_schema: object = field(default=None, repr=False)  # transient; not serialized
```

- [ ] **Step 5: Update _update_pipeline_metadata to save last_schema**

In `_update_pipeline_metadata`, after `pipeline.last_schema_hash = job.schema_hash`, add:

```python
            if job.schema_hash:
                pipeline.last_schema_hash = job.schema_hash
            if getattr(job, "_actual_schema", None) is not None:
                pipeline.last_schema = _schema_to_dict(job._actual_schema)
```

The full updated block inside `_update_pipeline_metadata`:

```python
            pipeline.last_watermark = datetime.now(tz=UTC).isoformat()
            if job.schema_hash:
                pipeline.last_schema_hash = job.schema_hash
            if getattr(job, "_actual_schema", None) is not None:
                pipeline.last_schema = _schema_to_dict(job._actual_schema)
            pipeline.updated_at = datetime.now(tz=UTC)
            await session.commit()
```

- [ ] **Step 6: Run worker phase14 tests**

```bash
uv run pytest tests/test_worker_phase14.py -v
```

Expected: 3 PASSED

- [ ] **Step 7: Run full test suite**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 361 passed (3 new), 0 failures.

- [ ] **Step 8: Commit**

```bash
git add src/siphon/worker.py src/siphon/models.py tests/test_worker_phase14.py
git commit -m "feat(phase-14): schema validation in DQ; save last_schema after successful job"
```

---

## Task 8: Pipeline API — last_schema and expected_schema Fields

**Files:**
- Modify: `src/siphon/pipelines/router.py`
- Modify: `tests/test_pipelines.py`

- [ ] **Step 1: Write failing tests**

At the end of `tests/test_pipelines.py`, add:

```python
def test_pipeline_response_includes_last_schema_and_expected_schema(client, db_session, test_connection):
    """GET /pipelines/{id} returns last_schema and expected_schema fields (may be None)."""
    payload = {
        "name": "schema-fields-test",
        "source_connection_id": str(test_connection.id),
        "query": "SELECT 1",
        "destination_path": "s3a://bronze/test/",
    }
    create_resp = client.post("/api/v1/pipelines", json=payload, headers=auth_header())
    assert create_resp.status_code == 201
    pipeline_id = create_resp.json()["id"]

    resp = client.get(f"/api/v1/pipelines/{pipeline_id}", headers=auth_header())
    assert resp.status_code == 200
    data = resp.json()
    assert "last_schema" in data
    assert "expected_schema" in data
    assert data["last_schema"] is None
    assert data["expected_schema"] is None


def test_put_pipeline_sets_expected_schema(client, db_session, test_connection):
    """PUT /pipelines/{id} accepts expected_schema and returns it on GET."""
    payload = {
        "name": "expected-schema-test",
        "source_connection_id": str(test_connection.id),
        "query": "SELECT 1",
        "destination_path": "s3a://bronze/test/",
    }
    create_resp = client.post("/api/v1/pipelines", json=payload, headers=auth_header())
    assert create_resp.status_code == 201
    pipeline_id = create_resp.json()["id"]

    expected = [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}]
    put_resp = client.put(
        f"/api/v1/pipelines/{pipeline_id}",
        json={"expected_schema": expected},
        headers=auth_header(),
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["expected_schema"] == expected

    get_resp = client.get(f"/api/v1/pipelines/{pipeline_id}", headers=auth_header())
    assert get_resp.json()["expected_schema"] == expected
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/test_pipelines.py::test_pipeline_response_includes_last_schema_and_expected_schema tests/test_pipelines.py::test_put_pipeline_sets_expected_schema -v
```

Expected: FAILED — `KeyError: 'last_schema'`

- [ ] **Step 3: Update Pydantic models and _to_response in pipelines/router.py**

In `PipelineCreate`, add:

```python
    partition_by: Literal["none", "ingest_date"] = "none"
    expected_schema: list[dict] | None = None
```

In `PipelineUpdate`, add:

```python
    partition_by: Literal["none", "ingest_date"] | None = None
    expected_schema: list[dict] | None = None
```

In `PipelineResponse`, add:

```python
    partition_by: str
    last_schema: list[dict] | None = None
    expected_schema: list[dict] | None = None
    is_active: bool
```

In `_to_response`, add the two new fields:

```python
    return PipelineResponse(
        id=str(p.id),
        ...
        partition_by=p.partition_by or "none",
        last_schema=p.last_schema,
        expected_schema=p.expected_schema,
        is_active=True,
        schedule=sched,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )
```

In the `update_pipeline` route handler, add `expected_schema` to the fields that are updated via `model_fields_set`. Find the block where fields are applied (similar to how `pii_columns` is handled with `None` vs not-set semantics) and add:

```python
        if "expected_schema" in body.model_fields_set:
            p.expected_schema = body.expected_schema
```

In `trigger_pipeline`, pass `pipeline_expected_schema` to the `Job`:

```python
    job = Job(
        job_id=str(uuid.uuid4()),
        pipeline_id=str(pipeline_id),
        pipeline_schema_hash=p.last_schema_hash,
        pipeline_pii=p.pii_columns or None,
        pipeline_expected_schema=p.expected_schema or None,
        pipeline_dq={...} if has_dq else None,
        is_backfill=bool(body.date_from and body.date_to),
        pipeline_alert=(...) if p.webhook_url else None,
    )
```

- [ ] **Step 4: Run the new pipeline tests**

```bash
uv run pytest tests/test_pipelines.py::test_pipeline_response_includes_last_schema_and_expected_schema tests/test_pipelines.py::test_put_pipeline_sets_expected_schema -v
```

Expected: 2 PASSED

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ --tb=short -q
```

Expected: 363 passed, 0 failures.

- [ ] **Step 6: Ruff check**

```bash
uv run ruff check src/
```

Expected: no violations.

- [ ] **Step 7: Commit**

```bash
git add src/siphon/pipelines/router.py tests/test_pipelines.py
git commit -m "feat(phase-14): last_schema + expected_schema in pipeline API"
```

---

## Task 9: Final Verification

- [ ] **Step 1: Run complete test suite**

```bash
uv run pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: 363+ passed, 0 failures, 0 errors.

- [ ] **Step 2: Ruff**

```bash
uv run ruff check src/
```

Expected: no violations.

- [ ] **Step 3: Verify JSON output format manually**

```bash
uv run python -c "
import structlog, sys
from siphon.main import _configure_logging
_configure_logging()
structlog.contextvars.bind_contextvars(job_id='test-123')
log = structlog.get_logger()
log.info('hello world', rows=42)
"
```

Expected output (JSON since not a TTY in CI):
```json
{"job_id": "test-123", "level": "info", "timestamp": "2026-...", "event": "hello world", "rows": 42}
```

- [ ] **Step 4: Final commit + update memory**

```bash
git add -A
git commit -m "chore(phase-14): final cleanup and verification"
```

---

## Definition of Done

- [ ] `uv run pytest` passes (363+ tests, 0 failures)
- [ ] `uv run ruff check src/` passes
- [ ] JSON log line for a job includes `job_id`, `pipeline_id`, `timestamp`, `level`, `event`
- [ ] `OTEL_EXPORTER_OTLP_ENDPOINT` unset → no network calls, process starts normally
- [ ] `GET /api/v1/pipelines/{id}` returns `last_schema` and `expected_schema` fields
- [ ] `PUT /api/v1/pipelines/{id}` with `expected_schema` → subsequent job with missing column returns `status: "failed"`
- [ ] All stdlib logs (uvicorn, SQLAlchemy) also emit as structured JSON in production
