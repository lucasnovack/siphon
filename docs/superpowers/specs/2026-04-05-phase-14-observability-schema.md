# Phase 14 — Observability & Schema Registry

**Date:** 2026-04-05
**Branch:** `feature/phase-14-observability-schema`

## Goal

Close the four remaining production-readiness gaps:

1. **Structured JSON logging** — replace plain-text `logging` with `structlog`; JSON in prod, colored in dev; `job_id`/`pipeline_id` auto-injected via contextvars
2. **OpenTelemetry tracing** — spans per job, `trace_id` in every log line; no-op by default, OTLP-ready via env var
3. **Schema registry** — store actual Arrow schema (JSONB) alongside existing SHA-256 hash; expose via API
4. **DQ schema validation** — optional per-pipeline `expected_schema` contract; validates column names + types before write

---

## Architecture

No new modules. Changes are additive inside existing files. The four features form two vertical slices:

- **Observability slice:** `main.py` (structlog + OTEL setup) → `worker.py` (bind context, create span) → all other modules (swap `logging.getLogger` → `structlog.get_logger`)
- **Schema slice:** `orm.py` + migration → `worker.py` (`_schema_to_dict`, `_check_schema`) → `pipelines/router.py` (Pydantic fields)

---

## 1. Structured Logging (structlog)

### Dependencies

Add to `pyproject.toml` main deps:
```
"structlog>=24.4",
```

### Configuration (`main.py`)

Called once in the `lifespan` context, before the app starts serving:

```python
import structlog, sys, logging

def _configure_logging() -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _otel_trace_processor,           # injects trace_id (see §2)
    ]
    if sys.stderr.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Redirect stdlib logging (uvicorn, SQLAlchemy, APScheduler) to structlog
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=shared_processors + [renderer],
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
```

### Usage in modules

All modules replace:
```python
import logging
logger = logging.getLogger(__name__)
```
with:
```python
import structlog
logger = structlog.get_logger()
```

Call sites change from `logger.info("msg %s", val)` to `logger.info("msg", key=val)` (keyword args = structured fields). Positional `%s` style also works but is discouraged for new code.

### Worker context binding

At the start of each job execution in `worker.py`:

```python
structlog.contextvars.clear_contextvars()
structlog.contextvars.bind_contextvars(
    job_id=job.job_id,
    pipeline_id=str(job.pipeline_id) if job.pipeline_id else None,
)
```

`ThreadPoolExecutor` copies the `contextvars` snapshot per task, so context is isolated per job automatically.

### FastAPI request middleware

In `main.py`, a lightweight middleware binds `request_id` per HTTP request:

```python
@app.middleware("http")
async def _bind_request_id(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=str(uuid.uuid4()))
    return await call_next(request)
```

### Tests

`structlog.testing.capture_logs()` is used to assert structured fields:

```python
with structlog.testing.capture_logs() as cap:
    run_something()
assert any(e["event"] == "Job started" and e["job_id"] == "abc" for e in cap)
```

Existing `caplog` tests are unaffected — stdlib is still bridged.

---

## 2. OpenTelemetry

### Dependencies

Add to `pyproject.toml`:
```
"opentelemetry-sdk>=1.24",
"opentelemetry-instrumentation-fastapi>=0.45b0",
```

Optional dep group `[otel-otlp]`:
```
"opentelemetry-exporter-otlp-proto-grpc>=1.24",
```

### Configuration (`main.py`)

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, NullSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def _configure_otel(app: FastAPI) -> None:
    provider = TracerProvider()
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    else:
        provider.add_span_processor(BatchSpanProcessor(NullSpanExporter()))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
```

Even with `NullSpanExporter`, `TracerProvider` generates real `trace_id` values — they appear in logs.

### structlog processor

```python
def _otel_trace_processor(logger, method, event_dict):
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict
```

### Worker span

```python
tracer = trace.get_tracer("siphon.worker")

with tracer.start_as_current_span("siphon.job") as span:
    span.set_attribute("job_id", job.job_id)
    span.set_attribute("pipeline_id", str(job.pipeline_id or ""))
    span.set_attribute("source_type", job.source_config.get("type", ""))
    span.set_attribute("dest_type", job.dest_config.get("type", "s3_parquet"))
    # ... existing job execution logic ...
    span.set_attribute("rows_extracted", rows_read)
```

### New environment variable

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (no-op) | gRPC endpoint for OTLP collector (e.g. `http://jaeger:4317`) |

### Tests

OTEL is mocked via `unittest.mock.patch("opentelemetry.trace.get_current_span")` or by configuring a `NoOpTracerProvider` in test fixtures. Tests verify structlog output contains `trace_id` when a valid span is active.

---

## 3. Schema Registry

### ORM changes (`orm.py`)

Add to `Pipeline`:
```python
last_schema: Mapped[list | None] = mapped_column(JSONB, nullable=True)
```

`last_schema_hash` (existing) is retained — used for fast diff detection without deserializing JSONB.

### Migration

`alembic/versions/006_add_schema_registry.py`:
```sql
ALTER TABLE pipelines ADD COLUMN last_schema JSONB;
```

(Previous migration is 005 — `add_partition_by`.)

### Schema serialization (`worker.py`)

```python
def _schema_to_dict(schema: pa.Schema) -> list[dict]:
    return [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]
```

Called after extraction. Result stored in `pipeline.last_schema` via `_update_pipeline_metadata`.

### API

`PipelineResponse` (in `pipelines/router.py`) gains:
```python
last_schema: list[dict] | None = None
```

No new endpoint — the field is returned on `GET /api/v1/pipelines/{id}` and `GET /api/v1/pipelines`.

---

## 4. DQ Schema Validation

### ORM changes (`orm.py`)

Add to `Pipeline`:
```python
expected_schema: Mapped[list | None] = mapped_column(JSONB, nullable=True)
```

### Migration

Same migration `006_add_schema_registry.py`:
```sql
ALTER TABLE pipelines ADD COLUMN expected_schema JSONB;
```

### Pydantic (`pipelines/router.py`)

```python
class PipelineCreate(BaseModel):
    ...
    expected_schema: list[dict] | None = None

class PipelineUpdate(BaseModel):
    ...
    expected_schema: list[dict] | None = None

class PipelineResponse(BaseModel):
    ...
    expected_schema: list[dict] | None = None
    last_schema: list[dict] | None = None
```

### Validation function (`worker.py`)

```python
def _check_schema(actual: pa.Schema, expected: list[dict]) -> str | None:
    expected_map = {f["name"]: f["type"] for f in expected}
    actual_map = {field.name: str(field.type) for field in actual}

    missing = [n for n in expected_map if n not in actual_map]
    if missing:
        return f"Schema validation failed: missing columns {missing}"

    mismatched = [
        f"{n}: expected {expected_map[n]}, got {actual_map[n]}"
        for n in expected_map
        if n in actual_map and actual_map[n] != expected_map[n]
    ]
    if mismatched:
        return f"Schema validation failed: type mismatch {mismatched}"

    extra = [n for n in actual_map if n not in expected_map]
    if extra:
        logger.warning("extra columns not in expected_schema", columns=extra)

    return None
```

### Worker integration

After extraction, before write, alongside existing DQ checks:

```python
if job.pipeline_expected_schema:
    schema_error = _check_schema(actual_schema, job.pipeline_expected_schema)
    if schema_error:
        raise ValueError(schema_error)
```

`Job` dataclass gains `pipeline_expected_schema: list[dict] | None = None`.

---

## File Map

| File | Action | Change |
|------|--------|--------|
| `pyproject.toml` | Modify | Add `structlog`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`; optional `[otel-otlp]` group |
| `src/siphon/main.py` | Modify | `_configure_logging()`, `_configure_otel()`, `_bind_request_id` middleware |
| `src/siphon/worker.py` | Modify | structlog, `_otel_trace_processor`, span per job, `_schema_to_dict`, `_check_schema`, context binding |
| `src/siphon/orm.py` | Modify | `last_schema`, `expected_schema` JSONB fields on `Pipeline` |
| `src/siphon/models.py` | Modify | `Job.pipeline_expected_schema` field |
| `src/siphon/pipelines/router.py` | Modify | `last_schema`, `expected_schema` in Pydantic models |
| `alembic/versions/006_add_schema_registry.py` | Create | Add `last_schema`, `expected_schema` columns |
| All other `src/siphon/**/*.py` | Modify | Replace `logging.getLogger` with `structlog.get_logger` |
| `tests/test_logging.py` | Create | structlog output, context binding, trace_id injection |
| `tests/test_schema_registry.py` | Create | `_schema_to_dict` round-trip, `_check_schema` rules |
| `tests/test_worker_phase14.py` | Create | DQ schema validation in worker flow |

---

## New Dependencies Summary

| Package | Type | Purpose |
|---|---|---|
| `structlog>=24.4` | main | Structured JSON logging |
| `opentelemetry-sdk>=1.24` | main | Tracing + trace_id generation |
| `opentelemetry-instrumentation-fastapi>=0.45b0` | main | Auto-instrument FastAPI routes |
| `opentelemetry-exporter-otlp-proto-grpc>=1.24` | optional (`[otel-otlp]`) | Export spans to OTLP collector |

---

## New Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | gRPC OTLP endpoint; if unset, spans are discarded (no-op) |

---

## Testing Strategy

- **structlog:** `structlog.testing.capture_logs()` for unit tests; assert `job_id`, `trace_id` fields present
- **OTEL:** `unittest.mock.patch` on `trace.get_current_span()` returning a mock with valid `SpanContext`
- **Schema registry:** `_schema_to_dict` round-trip; assert JSONB stored after successful job (mocked DB session)
- **DQ schema validation:** `_check_schema` unit tests covering missing column, type mismatch, extra column (warning only)
- **Existing tests:** No changes expected — stdlib logging bridge preserves `caplog` compatibility

---

## Definition of Done

- [ ] `uv run pytest` passes (352+ tests, zero failures)
- [ ] `uv run ruff check src/` passes
- [ ] JSON log line for a job includes `job_id`, `pipeline_id`, `trace_id`, `timestamp`, `level`
- [ ] `OTEL_EXPORTER_OTLP_ENDPOINT` unset → no network calls, `trace_id` still in logs
- [ ] `GET /api/v1/pipelines/{id}` returns `last_schema` after a successful run
- [ ] `PUT /api/v1/pipelines/{id}` with `expected_schema` → subsequent job with missing column returns `failed` status
