# Siphon Roadmap

History of delivered phases and the evolution plan toward making Siphon production-ready as a data tool.

---

## Delivered phases

### Phase 1 — Skeleton ✅

Complete scaffolding: folder structure, Pydantic models, plugin ABCs, registry with autodiscovery, date variable resolver.

### Phase 2 — API + Queue ✅
HTTP service with a functional job queue: `POST /jobs`, `POST /extract`, `GET /jobs/{id}`, health endpoints, security middleware.

### Phase 3 — SQL Source ✅
`SQLSource` covering MySQL, PostgreSQL, MSSQL, SQLite via ConnectorX and Oracle via oracledb thin mode.

### Phase 4 — SFTP Source + S3 Parquet Destination ✅
`SFTPSource` with retry and batch processing. `S3ParquetDestination` with Snappy compression and path validation.

### Phase 5 — Incremental Extraction ✅
Watermark injection with dialect-specific CTE (MySQL, PostgreSQL, Oracle, MSSQL). Automatic `last_watermark` update after success.

### Phase 6 — Data Quality Guards ✅
`min_rows_expected` and `max_rows_drop_pct` checks. Schema hash for drift detection. Alerts in job logs.

### Phase 7 — Hotfixes ✅
Fixed OOM in `_jobs` dict, SFTP stranded files in processing folder, and `POST /extract` always enabled.

### Phase 7.5 — Oracle Cursor Streaming ✅
Replaced pandas path with cursor streaming via `oracledb`. Peak memory is now O(chunk\_size) regardless of table size.

### Phase 8 — PostgreSQL + Auth ✅
PostgreSQL schema with 6 tables via Alembic. Dual-auth (API key + JWT) with user CRUD, admin bootstrap, and `job_runs` persistence in the worker.

### Phase 9 — Connections + Pipelines API ✅
Full management API (`/api/v1/connections`, `/api/v1/pipelines`, `/api/v1/runs`, `/api/v1/preview`). Scheduling with APScheduler + advisory lock. Prometheus metrics. Fernet credential encryption.

### Phase 10 — Frontend MVP ✅
React SPA with Vite + TypeScript. Login, connections, pipelines, runs pages. Query preview. JWT auth with refresh token in httpOnly cookie.

### Phase 10.5 — Security Hardening ✅
SQL injection via `incremental_key` (CRITICAL). Auth on `/health` and `/metrics`. Rate limiting on sensitive endpoints. `except Exception` narrowing in auth. CRITICAL log for default JWT secret. URL-decode in path traversal. HTTP security headers. Credential masking in logs.

---

## Production phases

The phases below complete Siphon as a production-ready platform, delivered after the data engineering analysis of 2026-04-04.

---

### Phase 11 — Reliability and Parsers ✅ (delivered 2026-04-04)

**Branch:** `master` | **Tests:** 352

- **Retry on SQL sources** ✅ — exponential backoff with jitter
- **Idempotency / deduplication** ✅ — staging path (`_staging/{job_id}`) + promote after DB write; ordering fix (promote before watermark) delivered in Phase 14 Completion
- **CSV parser** ✅
- **JSON / JSONL parser** ✅
- **Basic PII masking** ✅ — sha256 / redact per column

---

### Phase 12 — Backfill, Partitioning, and Alerting ✅ (delivered 2026-04-04)

**Branch:** `master`

- **Backfill API** ✅
- **Hive-style partitioning** ✅
- **Webhook alerting** ✅
- **Freshness SLA** ✅

---

### Phase 13 — New Connectors ✅ (delivered 2026-04-05)

**Branch:** `master` | **Tests:** 368

- **HTTP/REST source** ✅ — Bearer/OAuth2/API key auth, cursor/page/offset pagination, rate limiting
- **Avro parser** ✅ — fastavro
- BigQuery and Snowflake were implemented in this phase and **removed in Phase 15** (S3/Parquet only focus)

---

### Phase 14 — Observability and Catalog ✅ partial (delivered 2026-04-05)

**Branch:** `master` | **Tests:** 368

- **Structured JSON logging** ✅ — structlog, stdlib bridge, contextvars per job
- **OpenTelemetry** ✅ — TracerProvider always active; trace_id on all logs; OTLP via env var
- **Schema registry** ✅ — Arrow schema as JSONB in `pipelines.last_schema`; exposed on `GET /api/v1/pipelines/{id}`
- **Data lineage** ✅ — `source_connection_id` + `destination_path` on `job_runs`; exposed on `GET /api/v1/runs` _(delivered in Phase 14 Completion — see below)_
- **Column metadata** ⏳ — not started (OpenMetadata/Collibra integration is LOW priority)

### Phase 14 Completion — Idempotency + Data Lineage ✅ (2026-04-05)

**Branch:** `feature/phase-14-completion` (merged) | **Tests:** 374

- **Idempotency fix** ✅ — `destination.promote()` now runs before watermark update; prevents silent data gap
- **Minimal data lineage** ✅ — migration 007, `source_connection_id` + `destination_path` on `job_runs`

---

### Phase 15 — Cleanup + Performance ✅ (delivered 2026-04-06)

**Branch:** `master`

- **BigQuery and Snowflake removal** ✅ — Siphon is S3/Parquet only; dependencies and tests removed
- **`max_concurrent_jobs` on Connection** ✅ — limits concurrent jobs per source (migration 008); worker checks before starting
- **`priority` on Pipeline** ✅ — `low/normal/high` enum (migration 009); replaces `asyncio.Queue` with `PriorityQueue`
- **Frontend updated** ✅ — `priority` field in PipelineWizard and `max_concurrent_jobs` in ConnectionForm

---

### Phase 16 — Celery + Redis (Horizontal scale) ✅ (delivered 2026-04-07)

**Branch:** `master`

- **Celery + Redis** ✅ — `celery_app.py`, `high/normal/low` queues, Redis broker and backend
- **`tasks.py`** ✅ — `run_pipeline_task` Celery task calling existing `run_job()`
- **Job state in PostgreSQL** ✅ — `job_runs` in DB; `GET /jobs/{id}` reads from DB; cancel via `celery revoke`
- **`queue.py` as wrapper** ✅ — `enqueue()` → `apply_async(queue=priority)`
- **`docker-compose.yml`** ✅ — `redis:7-alpine` + `siphon-worker` service
- **Graceful drain** ✅ — `task_acks_late=True`, `worker_prefetch_multiplier=1`

---

### Phase 17 — GDPR Compliance ✅ (delivered 2026-04-08)

**Branch:** `master`

- **Soft delete** ✅ — `deleted_at TIMESTAMPTZ` on `connections`, `pipelines`, `schedules`, `users` (migration 010); all `GET` endpoints filter `WHERE deleted_at IS NULL`
- **Cascade** ✅ — soft-delete connection → soft-delete pipelines + remove Celery schedules
- **S3 Purge API** ✅ — `DELETE /api/v1/pipelines/{id}/data` with `?before=date&partition=val` params (admin-only); synchronous (<1000 files) or background Celery task (≥1000, returns 202)
- **`gdpr_events`** ✅ — migration 011; records each purge with files/bytes deleted
- **Audit endpoints** ✅ — `GET /api/v1/gdpr/events` and `GET /api/v1/gdpr/events/{id}` (admin-only)

---

## Design decisions guiding the roadmap

- **Bronze layer only** — Siphon is not a replacement for dbt or Spark for Silver/Gold; focus is on reliable Extract-Load
- **Plugin architecture** — new sources and destinations require no core changes; just register with `@register("type")`
- **No Airflow dependency** — Siphon has its own scheduler; Airflow integration is via API key in the Airflow → Siphon direction, not the other way around
- **Self-hosted first** — deployable on Docker Compose or Kubernetes; no vendor lock-in to managed services
