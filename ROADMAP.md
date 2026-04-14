# Siphon — Implementation Roadmap

Implement phase by phase, in the order below.
Each phase ends with passing tests and working code before moving on.

---

## Phase 1 — Project skeleton ✅

- [x] Initialize project with `uv init`, configure `pyproject.toml` (deps, ruff, pytest)
- [x] Create folder structure
- [x] `models.py` — all Pydantic models
- [x] ABCs: `Source`, `Destination`, `Parser`
- [x] Registry + autodiscovery for sources, destinations, parsers
- [x] `variables.py` — resolve `@TODAY`, `@MIN_DATE`, `@LAST_MONTH`, `@NEXT_MONTH`
- [x] Unit tests: variable resolution, registry

---

## Phase 2 — API and queue ✅

- [x] `queue.py` — asyncio queue, ThreadPoolExecutor, drain logic (SIGTERM)
- [x] `worker.py` — execution loop: source.extract_batches() → destination.write()
- [x] `main.py` — FastAPI app, routes: `/jobs`, `/extract`, `/health`
- [x] Middleware: request size limit, API key auth, 422 without body logging

---

## Phase 3 — SQL Source ✅

- [x] `SQLSource` with ConnectorX (MySQL, PostgreSQL, MSSQL)
- [x] `_inject_timeout()`, `_validate_host()` (SSRF), `mask_uri()`
- [x] Oracle: `_extract_oracle()` with oracledb thin mode + cursor.fetchmany()
- [x] Partitioning fields: `partition_on`, `partition_num`, `partition_range`

---

## Phase 4 — S3 Destination ✅

- [x] `S3ParquetDestination` with PyArrow S3FileSystem
- [x] `_validate_path()` — traversal check + prefix check
- [x] Atomic staging: write temp → rename
- [x] `rows_read == rows_written` validation

---

## Phase 5 — SFTP Source ✅

- [x] `SFTPSource` with Paramiko, RejectPolicy
- [x] `_move_to_processing()` / `_move_to_processed()` / `_move_back_to_origin()`
- [x] `_download_with_retry()` — exponential backoff
- [x] `skip_patterns`, `SIPHON_MAX_FILE_SIZE_MB`, `fail_fast`, `partial_success`

---

## Phase 6 — Docker and CI ✅

- [x] `Dockerfile` — multi-stage, non-root UID 1000
- [x] `docker-compose.yml` — siphon + mysql + postgres + minio + sftp
- [x] `.github/workflows/ci.yml` — ruff, pytest unit, docker build, trivy, integration
- [x] `.github/workflows/publish.yml` — tag `v*` → GHCR

---

## Phase 7 — Critical hotfixes ✅

- [x] TTL eviction of finished jobs (`SIPHON_JOB_TTL_SECONDS`)
- [x] SFTP `_move_back_to_origin()` on parse failure
- [x] `/extract` guard: 404 without `SIPHON_ENABLE_SYNC_EXTRACT=true`

---

## Phase 7.5 — Oracle cursor streaming ✅

- [x] `_extract_oracle()` with native `cursor.fetchmany()` from oracledb
- [x] `_oracle_output_type_handler()` — LOB, NUMBER, DATE
- [x] `_oracle_rows_to_arrow()` — row-list → pa.Table conversion

---

## Phase 8 — PostgreSQL + Auth ✅

- [x] Alembic + migrations (6 tables: users, connections, pipelines, schedules, job_runs, refresh_tokens)
- [x] `db.py` — SQLAlchemy async engine, session factory
- [x] Auth router: login, refresh, logout, me — JWT + httpOnly cookie
- [x] Token rotation + reuse detection
- [x] Rate limiting on login (slowapi)
- [x] Users router — admin-only CRUD
- [x] Worker persists result in `job_runs`

---

## Phase 9 — Connections + Pipelines API ✅

- [x] `crypto.py` — Fernet encrypt/decrypt
- [x] Connections router: CRUD, test, types
- [x] Pipelines router: CRUD, schedule upsert, `GET /:id/runs`
- [x] `POST /pipelines/:id/trigger` — builds Job, creates queued job_run, enqueues
- [x] Preview router — `POST /preview` with LIMIT 100 + SSRF
- [x] Runs router — global history, cursor-based logs, cancel
- [x] `GET /metrics` — Prometheus
- [x] Scheduler: APScheduler + PostgreSQL jobstore + advisory lock multi-pod
- [x] Watermark: CTE `_siphon_base` + dialect-aware type WHERE clause
- [x] Schema evolution: SHA-256 hash, `schema_changed=True`, writes never blocked
- [x] Data quality: `min_rows_expected`, `max_rows_drop_pct`

---

## Phase 10 — Frontend ✅

- [x] Vite + React 18 + shadcn/ui + Tailwind + TanStack Query
- [x] Auth context, axios interceptor with refresh mutex
- [x] Pages: Login, Dashboard, Connections, Pipelines, Runs, Settings/Users, Settings/System
- [x] PipelineWizard 4 steps, RunDetailPage + LogViewer polling, QueryEditor CodeMirror
- [x] FastAPI serves `frontend/dist/` (SPA fallback)
- [x] Dockerfile: `frontend-builder` stage node:22-slim

---

## Phase 10.5 — Security hardening ✅

- [x] 19 vulnerabilities fixed (Trivy scan)

---

## Phase 11 — Retry, Parsers, PII, Staging ✅

- [x] Automatic retry on SQL sources with backoff
- [x] CSV parser, JSON parser (with jq-style path)
- [x] Configurable PII masking per column (hash / redact / partial)
- [x] Generalized atomic S3 staging

---

## Phase 12 — Backfill, Hive, Webhooks, SLA ✅

- [x] Backfill API — historical rerun with watermark override
- [x] Hive partitioning in Parquet (`_date=2024-04-03/`)
- [x] Webhook alerts: job failure, SLA breach
- [x] Configurable SLA per pipeline (max duration)

---

## Phase 13 — HTTP/REST Source, Avro, BigQuery, Snowflake ✅

- [x] `HTTPRestSource` — pagination, auth headers, JSON path extraction
- [x] Avro parser
- [x] BigQuery destination *(removed in phase 15)*
- [x] Snowflake destination *(removed in phase 15)*

---

## Phase 14 — Observability + Schema Registry ✅

- [x] structlog — JSON in prod, ConsoleRenderer in TTY, stdlib bridge
- [x] OTEL tracing — `trace_id`/`span_id` on every log line, optional OTLP export
- [x] Schema registry — `last_schema JSONB` + `expected_schema JSONB` on `pipelines`
- [x] DQ schema validation — missing col = error, type mismatch = error, extra col = warning
- [x] `PipelineResponse` returns `last_schema`

---

## Phase 14-completion — Idempotency + Lineage ✅

- [x] Idempotency fix: trigger creates "queued" row, worker does UPDATE (no duplicate)
- [x] Data lineage: `source_connection_id` + `destination_path` on `job_runs`
- [x] Exposed on `GET /runs` and `GET /runs/{id}`
- [x] 374 tests passing

---

## Phase 15 — Cleanup + Performance ✅

**Goal:** remove non-Parquet destinations, add per-connection concurrency limits and job prioritization.

- [x] Remove `bigquery_dest.py`, `snowflake_dest.py`, associated models and tests
- [x] `max_concurrent_jobs` on `Connection` — limits concurrent jobs per source (migration 008)
- [x] Worker checks concurrency before starting; requeues with backoff if at limit
- [x] `priority` enum (`low/normal/high`) on `pipelines` (migration 009)
- [x] Replace `asyncio.Queue` with `asyncio.PriorityQueue` in `queue.py`
- [x] Frontend: `priority` field in PipelineWizard and PipelineEditPage; `max_concurrent_jobs` in ConnectionForm

---

## Phase 16 — Celery + Redis (Horizontal scale) ✅

**Goal:** decouple API and workers; multiple worker pods consuming the same queue.

- [x] `celery_app.py` — Celery configured with Redis broker/backend, `high/normal/low` queues
- [x] `tasks.py` — `@celery_app.task run_pipeline_task(job_dict)` calling existing `run_job()`
- [x] Job state migrated from in-memory `_jobs` to `job_runs` in PostgreSQL
- [x] `GET /jobs/{id}` reads from DB; cancel via `celery revoke(terminate=True)`
- [x] `queue.py` becomes a thin wrapper: `enqueue()` → `apply_async(queue=priority)`
- [x] Per-connection concurrency reads from `job_runs WHERE status='running'` (no in-memory dict)
- [x] `docker-compose.yml`: add `redis:7-alpine` + `siphon-worker`
- [x] Graceful drain: `task_acks_late=True`, `worker_prefetch_multiplier=1`

---

## Phase 17 — GDPR Compliance ✅

**Goal:** soft delete on all entities + S3 data purge API.

- [x] `deleted_at TIMESTAMPTZ` nullable on `connections`, `pipelines`, `schedules`, `users` (migration 010)
- [x] All `GET` endpoints filter `WHERE deleted_at IS NULL`; `DELETE` sets `deleted_at = now()`
- [x] Cascade: soft-delete connection → soft-delete pipelines + remove Celery schedules
- [x] `DELETE /api/v1/pipelines/{id}/data` — S3 purge with `?before=date&partition=val` params (admin-only)
- [x] Synchronous purge (<1000 files) or background Celery task (≥1000, returns 202)
- [x] `gdpr_events` table + migration 011: records each purge with files/bytes deleted
- [x] `GET /api/v1/gdpr/events` and `GET /api/v1/gdpr/events/{id}` (admin-only)

---

## Global definition of done

Before considering v1 complete, all items in the spec must be checked.
