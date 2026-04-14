# Siphon

A lightweight, self-hosted data pipeline platform that replaces Apache Spark in the Bronze layer of a medallion architecture. Siphon handles the full lifecycle of extraction pipelines: connection registry, scheduling, incremental watermarks, data quality checks, schema evolution detection, and Parquet writes to S3-compatible storage.

**Why not Spark?** A typical Bronze job is three lines: read SQL, write Parquet. For that, Spark adds 30–60s of cold-start, 2 GB of memory, and a JVM-based image — 200 times the overhead. Siphon does the same job in under 5 seconds with ~200 MB of RAM.

| | Spark | Siphon |
|---|---|---|
| Cold-start | 2–5 min | < 5s |
| Memory (1M rows) | ~4 GB | ~200 MB |
| Oracle support | ojdbc JAR + Instant Client | oracledb thin mode |
| Connection management | Airflow Variables | Built-in registry (Fernet-encrypted) |
| Scheduling | Airflow DAGs | Built-in APScheduler + advisory lock |
| Schema drift detection | Manual | Automatic (SHA-256, non-blocking) |
| Data quality gates | External (Great Expectations) | Built-in per-pipeline |

---

## Stack

```
┌─────────────────────────────────────────────────────┐
│                    FastAPI (siphon)                  │
│                                                      │
│  /api/v1/connections  ──► Connection registry        │
│  /api/v1/pipelines    ──► Pipeline CRUD + schedules  │
│  /api/v1/preview      ──► Ad-hoc SQL preview         │
│  /api/v1/runs         ──► Run history + logs         │
│  /api/v1/gdpr         ──► Audit events               │
│  /metrics             ──► Prometheus                 │
│                                                      │
│  APScheduler ──► advisory lock ──► trigger ──┐       │
│  POST /trigger ─────────────────────────────►│       │
│                                              ▼       │
│                              Celery task (Redis)     │
└─────────────────────────────────────────────────────┘
                                   │
                 ┌─────────────────┘
                 ▼
┌─────────────────────────────────────────────────────┐
│               Celery Worker (siphon-worker)          │
│                                                      │
│  source.extract_batches()  ──► streaming Arrow chunks│
│  inject_watermark()        ──► incremental WHERE     │
│  _check_data_quality()     ──► fail fast or pass     │
│  _compute_schema_hash()    ──► drift detection       │
│  destination.write()       ──► S3 Parquet (Snappy)   │
│  _persist_job_run()        ──► PostgreSQL            │
└─────────────────────────────────────────────────────┘
```

**Runtime components:** PostgreSQL (state + scheduling) · Redis (Celery broker/backend) · S3-compatible storage (destination — AWS S3, MinIO, or any compatible service)

---

## Contents

- [Quick Start](#quick-start)
- [Authentication](#authentication)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Pipeline Examples](#pipeline-examples)
- [Variable Substitution](#variable-substitution)
- [Incremental Mode](#incremental-mode)
- [Schema Evolution](#schema-evolution)
- [Data Quality](#data-quality)
- [PII Masking](#pii-masking)
- [GDPR](#gdpr)
- [Plugin Reference](#plugin-reference)
- [Writing a Custom Plugin](#writing-a-custom-plugin)
- [Security](#security)
- [Kubernetes](#kubernetes)
- [Development](#development)

---

## Quick Start

**Prerequisites:** Docker and Docker Compose.

```bash
git clone https://github.com/lucasnovack/siphon
cd siphon
docker compose up -d
```

This starts PostgreSQL, Redis, the API, and a Celery worker. Siphon runs database migrations automatically on first boot.

```bash
docker compose ps
# siphon should show "healthy" after ~20 seconds
```

Open the UI at [http://localhost:8000](http://localhost:8000) and log in with the bootstrap credentials from `docker-compose.yml`:

- Email: `admin@example.com`
- Password: `changeme123`

**Before exposing to any network**, replace the default secrets:

```bash
# Generate a Fernet key for connection encryption
docker compose run --rm siphon python -c \
  "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set `SIPHON_JWT_SECRET`, `SIPHON_ADMIN_PASSWORD`, and `SIPHON_ENCRYPTION_KEY` in `docker-compose.yml` (or via environment / secrets manager).

---

## Authentication

Two auth methods, same `Authorization: Bearer <token>` header:

| Method | Token | Use case |
|---|---|---|
| API key | `SIPHON_API_KEY` value | Airflow, scripts, CI |
| JWT | Access token from `POST /api/v1/auth/login` | Human users, UI |

Health probes (`/health/live`, `/health/ready`) are always public. Roles: `admin` (full CRUD) and `operator` (read + trigger).

```bash
# Login
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "changeme123"}'
# → {"access_token": "eyJ...", "token_type": "bearer"}
```

Access tokens expire in 15 minutes. A refresh token is set as an httpOnly cookie. Presenting a revoked refresh token invalidates all sessions for that user.

---

## API Reference

All endpoints require `Authorization: Bearer <token>` unless noted.

### Connections

Encrypted credential store for sources and destinations. Config blobs are Fernet-encrypted at rest.

| Method | Endpoint | Description | Role |
|---|---|---|---|
| `GET` | `/api/v1/connections` | List connections | any |
| `GET` | `/api/v1/connections/types` | Supported types | any |
| `POST` | `/api/v1/connections` | Create | admin |
| `GET` | `/api/v1/connections/{id}` | Get (config masked) | any |
| `PUT` | `/api/v1/connections/{id}` | Update | admin |
| `DELETE` | `/api/v1/connections/{id}` | Soft delete | admin |
| `POST` | `/api/v1/connections/{id}/test` | Test connectivity | admin |

### Pipelines

| Method | Endpoint | Description | Role |
|---|---|---|---|
| `GET` | `/api/v1/pipelines` | List pipelines | any |
| `POST` | `/api/v1/pipelines` | Create | admin |
| `GET` | `/api/v1/pipelines/{id}` | Get | any |
| `PUT` | `/api/v1/pipelines/{id}` | Update + resync schedule | admin |
| `DELETE` | `/api/v1/pipelines/{id}` | Soft delete | admin |
| `POST` | `/api/v1/pipelines/{id}/trigger` | Trigger manual run | admin, operator |
| `GET` | `/api/v1/pipelines/{id}/runs` | Paginated run history | any |
| `DELETE` | `/api/v1/pipelines/{id}/data` | Purge S3 data (GDPR) | admin |

**Pipeline fields:**

| Field | Type | Description |
|---|---|---|
| `name` | string | Unique display name |
| `source_connection_id` | uuid | Source connection |
| `dest_connection_id` | uuid | Destination connection |
| `query` | string | SQL query (variables and watermark injected at runtime) |
| `dest_prefix` | string | S3 key prefix |
| `extraction_mode` | `full_refresh` \| `incremental` | Default: `full_refresh` |
| `incremental_key` | string | Column used for watermark (incremental only) |
| `cron` | string | 5-field cron expression |
| `is_active` | bool | Whether the schedule is active |
| `priority` | `low` \| `normal` \| `high` | Queue priority (default: `normal`) |
| `min_rows_expected` | int | DQ: fail if fewer rows extracted |
| `max_rows_drop_pct` | int | DQ: fail if row count drops more than N% vs previous run |
| `expected_schema` | object | DQ: schema contract (missing col = error, type mismatch = error) |

### Preview

Run a read-only SQL query against any `sql` connection. Always wrapped in `SELECT * FROM (...) LIMIT 100`.

```bash
curl -X POST http://localhost:8000/api/v1/preview \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connection_id": "<id>", "query": "SELECT id, name FROM orders"}'
```

### Runs

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/runs` | All runs, newest-first (`?pipeline_id=`, `?status=`, `?limit=`, `?offset=`) |
| `GET` | `/api/v1/runs/{id}` | Single run |
| `GET` | `/api/v1/runs/{id}/logs` | Cursor-based log streaming (`?since=<offset>`) |
| `POST` | `/api/v1/runs/{id}/cancel` | Cancel in-flight run (admin) |

```bash
# Tail logs
curl "http://localhost:8000/api/v1/runs/42/logs?since=0" -H "Authorization: Bearer $TOKEN"
# → {"logs": ["..."], "next_offset": 5, "done": false}
```

Run fields include: `job_id`, `pipeline_id`, `status`, `triggered_by`, `rows_read`, `rows_written`, `duration_ms`, `schema_changed`, `source_connection_id`, `destination_path`, `error`, `started_at`, `finished_at`.

### Legacy Job API (Airflow-compatible)

The original `POST /jobs` endpoint is fully preserved — existing DAGs require no changes.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer $SIPHON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {"type": "sql", "uri": "mysql://...", "query": "SELECT * FROM orders"},
    "destination": {"type": "s3_parquet", "bucket": "bronze", "prefix": "bronze/orders/", "access_key": "...", "secret_key": "..."}
  }'
# → {"job_id": "3f2a..."}

curl http://localhost:8000/jobs/<job_id> -H "Authorization: Bearer $SIPHON_API_KEY"
```

Status flow: `pending` → `running` → `success` | `failed` | `partial_success`

### Metrics

```bash
curl http://localhost:8000/metrics
```

| Metric | Type |
|---|---|
| `siphon_jobs_total` | Counter (by `status`) |
| `siphon_job_duration_seconds` | Histogram |
| `siphon_rows_extracted_total` | Counter |
| `siphon_queue_depth` | Gauge |
| `siphon_schema_changes_total` | Counter |

---

## Configuration

**Core**

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host/db`. Required for all persistent features. |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker and backend. Required for worker. |
| `SIPHON_JWT_SECRET` | dev fallback | HMAC secret for JWT signing. Always set in production. |
| `SIPHON_ENCRYPTION_KEY` | — | Fernet key for encrypting connection configs. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `SIPHON_ADMIN_EMAIL` | — | Bootstrap admin email (used only when no users exist). |
| `SIPHON_ADMIN_PASSWORD` | — | Bootstrap admin password (bcrypt-hashed at startup). |
| `SIPHON_API_KEY` | — | Legacy API key. If unset, API key auth is disabled. |
| `SIPHON_PORT` | `8000` | HTTP port. |

**Security**

| Variable | Default | Description |
|---|---|---|
| `SIPHON_ALLOWED_HOSTS` | all | Comma-separated hostnames or CIDRs for SSRF guard on connection strings. |
| `SIPHON_ALLOWED_S3_PREFIX` | all | Required prefix for S3 write paths (path traversal guard). |
| `SIPHON_S3_SCHEME` | `https` | `http` or `https` for S3 endpoint. |
| `SIPHON_MAX_REQUEST_SIZE_MB` | `1` | Request body size limit. |

**SFTP**

| Variable | Default | Description |
|---|---|---|
| `SIPHON_SFTP_KNOWN_HOSTS` | — | Path to SSH known_hosts file. |
| `SIPHON_SFTP_HOST_KEY` | — | Base64-encoded host public key (alternative to known_hosts). |
| `SIPHON_MAX_FILE_SIZE_MB` | `500` | Max SFTP file size; larger files are skipped. |

**Misc**

| Variable | Default | Description |
|---|---|---|
| `SIPHON_DRAIN_TIMEOUT` | `3600` | Seconds to wait for active jobs on SIGTERM. |
| `SIPHON_ENABLE_SYNC_EXTRACT` | `false` | Enable `POST /extract` (dev/testing only). |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | When set, exports traces via gRPC OTLP. |
| `OTEL_SERVICE_NAME` | `siphon` | Service name in trace spans. |

---

## Pipeline Examples

### MySQL → S3 Parquet (full refresh)

```json
{
  "source": {
    "type": "sql",
    "uri": "mysql://user:pass@host:3306/mydb",
    "query": "SELECT id, name, amount FROM orders WHERE DATE(created_at) = @TODAY",
    "chunk_size": 50000
  },
  "destination": {
    "type": "s3_parquet",
    "bucket": "bronze",
    "prefix": "bronze/orders/date=@TODAY/",
    "access_key": "...",
    "secret_key": "..."
  }
}
```

### Oracle → S3 Parquet (no Instant Client required)

```json
{
  "source": {
    "type": "sql",
    "uri": "oracle+oracledb://user:pass@host:1521/SERVICE",
    "query": "SELECT * FROM schema.table WHERE TRUNC(dt_created) = TRUNC(SYSDATE-1)",
    "chunk_size": 10000
  },
  "destination": {
    "type": "s3_parquet",
    "bucket": "bronze",
    "prefix": "bronze/oracle_table/date=@TODAY/",
    "access_key": "...",
    "secret_key": "..."
  }
}
```

### SFTP → S3 Parquet (atomic file moves)

```json
{
  "source": {
    "type": "sftp",
    "host": "sftp.example.com",
    "username": "ftpuser",
    "password": "secret",
    "remote_path": "/upload",
    "parser": "csv_parser",
    "move_to_processing": "/processing",
    "move_to_processed": "/processed",
    "fail_fast": false
  },
  "destination": {
    "type": "s3_parquet",
    "bucket": "bronze",
    "prefix": "bronze/sftp_files/date=@TODAY/",
    "access_key": "...",
    "secret_key": "..."
  }
}
```

### Airflow DAG integration

```python
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.http.sensors.http import HttpSensor

submit = SimpleHttpOperator(
    task_id="trigger",
    http_conn_id="siphon_default",
    endpoint="/api/v1/pipelines/<pipeline-id>/trigger",
    method="POST",
    headers={"Authorization": "Bearer {{ var.value.siphon_api_key }}"},
    response_filter=lambda r: r.json()["run_id"],
    do_xcom_push=True,
)

wait = HttpSensor(
    task_id="wait",
    http_conn_id="siphon_default",
    endpoint="/api/v1/runs/{{ ti.xcom_pull(task_ids='trigger') }}",
    headers={"Authorization": "Bearer {{ var.value.siphon_api_key }}"},
    response_check=lambda r: r.json()["status"] in ("success", "failed"),
    poke_interval=15,
    mode="reschedule",
)

submit >> wait
```

---

## Variable Substitution

Variables in SQL queries and S3 prefixes are resolved at job execution time.

| Variable | Resolves to |
|---|---|
| `@TODAY` | Current date (`2025-01-15`) |
| `@MIN_DATE` | `1900-01-01` |
| `@LAST_MONTH` | First day of last month (`2025-01-01`) |
| `@NEXT_MONTH` | First day of next month (`2025-03-01`) |

---

## Incremental Mode

Set `extraction_mode: incremental` and `incremental_key` on a pipeline. Siphon wraps the query in a dialect-aware CTE at trigger time:

```sql
-- Original query
SELECT id, updated_at FROM orders

-- PostgreSQL output
WITH _siphon_base AS (SELECT id, updated_at FROM orders)
SELECT * FROM _siphon_base
WHERE updated_at > CAST('2024-06-01T00:00:00+00:00' AS TIMESTAMPTZ)
```

Watermark cast per dialect: `DATETIME` (MySQL) · `TIMESTAMPTZ` (PostgreSQL) · `TIMESTAMP WITH TIME ZONE` (Oracle) · `DATETIMEOFFSET` (MSSQL).

`last_watermark` is updated only after the `job_runs` row is persisted — safe against pod kills between write and watermark update.

---

## Schema Evolution

After each extraction, Siphon computes a SHA-256 hash of the Arrow schema. On change:

- `job_runs.schema_changed = true`
- Warning appended to run logs
- Write proceeds normally — schema drift **never blocks** extraction

The last seen schema is stored as JSONB in `pipelines.last_schema`. You can also set `pipelines.expected_schema` to enforce a contract: missing columns fail the job, type mismatches fail, extra columns warn.

---

## Data Quality

Per-pipeline guardrails evaluated before writing:

| Config | Behavior |
|---|---|
| `min_rows_expected` | Fail if `rows_read < min_rows_expected` |
| `max_rows_drop_pct` | Fail if row count drops more than N% vs previous run |
| `expected_schema` | Fail on missing/mismatched columns; warn on extra columns |

On failure: job marked `failed`, no data written, error stored in `job_runs.error`.

---

## PII Masking

Per-column masking applied before writing to S3, configured on the pipeline:

```json
"pii_columns": [
  {"column": "email", "method": "sha256"},
  {"column": "ssn", "method": "redact"},
  {"column": "phone", "method": "partial"}
]
```

Methods: `sha256` (deterministic hash) · `redact` (replace with `***`) · `partial` (keep first/last chars).

---

## GDPR

All entities (`connections`, `pipelines`, `schedules`, `users`) use soft delete — `DELETE` sets `deleted_at` rather than removing the row. All `GET` endpoints filter `WHERE deleted_at IS NULL`.

Soft-deleting a connection cascades to its pipelines and removes their schedules from Celery.

**S3 data purge:**

```bash
DELETE /api/v1/pipelines/{id}/data?before=2024-01-01&partition=date=2023-12-31
```

- Fewer than 1 000 files: synchronous, returns `200` with bytes/files deleted.
- 1 000+ files: dispatched as a Celery background task, returns `202` with a job reference.
- Every purge is recorded in the `gdpr_events` table.

```bash
GET /api/v1/gdpr/events          # audit log (admin only)
GET /api/v1/gdpr/events/{id}     # single event
```

---

## Plugin Reference

### SQL Source (`type: sql`)

| Field | Required | Description |
|---|---|---|
| `uri` | yes | `mysql://`, `postgresql://`, `mssql://`, `oracle+oracledb://` |
| `query` | yes | SQL query |
| `chunk_size` | no | Rows per batch (default: `100000`) |
| `partition_on` | no | Column for ConnectorX parallel partitioning (MySQL/PG/MSSQL) |
| `partition_num` | no | Number of partitions (default: `4`) |

| URI prefix | Backend |
|---|---|
| `mysql://`, `postgresql://`, `mssql://` | ConnectorX (Rust, Arrow-native) |
| `oracle+oracledb://` | oracledb thin mode + cursor streaming |

### SFTP Source (`type: sftp`)

| Field | Required | Description |
|---|---|---|
| `host` | yes | SFTP hostname |
| `port` | no | SSH port (default: `22`) |
| `username` | yes | SSH username |
| `password` / `private_key` | one of | Auth credentials |
| `remote_path` | yes | Remote directory |
| `parser` | yes | Parser plugin name (`csv_parser`, `json_parser`, `avro_parser`) |
| `skip_patterns` | no | fnmatch patterns to skip |
| `move_to_processing` / `move_to_processed` | no | Atomic file move directories |
| `fail_fast` | no | Stop on first error (default: `true`) |

Unknown host keys are always rejected (`RejectPolicy`). Configure `SIPHON_SFTP_KNOWN_HOSTS` or `SIPHON_SFTP_HOST_KEY`.

### HTTP/REST Source (`type: http_rest`)

| Field | Required | Description |
|---|---|---|
| `url` | yes | Base URL |
| `auth_type` | no | `bearer`, `api_key`, `oauth2` |
| `pagination` | no | `cursor`, `page`, or `offset` |
| `json_path` | no | jq-style path to extract records from response |

### S3 Parquet Destination (`type: s3_parquet`)

| Field | Required | Description |
|---|---|---|
| `bucket` | yes | S3 bucket name |
| `prefix` | yes | Key prefix |
| `access_key` / `secret_key` | yes | Credentials |
| `endpoint` | no | Override for S3-compatible services |
| `region` | no | AWS region (default: `us-east-1`) |

Writes use staging + atomic promote (`_staging/{job_id}/` → final path). `rows_read == rows_written` is validated before promote.

### Parsers

| Name | Description |
|---|---|
| `csv_parser` | Comma-separated values |
| `json_parser` | JSON / JSONL with optional jq-style path |
| `avro_parser` | Avro via fastavro |

---

## Writing a Custom Plugin

Plugins are auto-discovered from `plugins/sources/`, `plugins/destinations/`, and `plugins/parsers/`. Drop a file in the right directory — no core changes needed.

```python
# src/siphon/plugins/sources/my_source.py
from typing import Iterator
import pyarrow as pa
from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source

@register("my_source")
class MySource(Source):
    def extract_batches(self) -> Iterator[pa.Table]:
        yield pa.table({"col": [1, 2, 3]})
```

```python
# src/siphon/plugins/destinations/my_dest.py
import pyarrow as pa
from siphon.plugins.destinations import register
from siphon.plugins.destinations.base import Destination

@register("my_dest")
class MyDestination(Destination):
    def write(self, table: pa.Table, is_first_chunk: bool) -> int:
        ...
        return table.num_rows
```

---

## Security

| Threat | Mitigation |
|---|---|
| Unauthorized access | `Authorization: Bearer` required on all endpoints |
| JWT secret exposure | Startup warning if unset; 15-min access token TTL |
| Refresh token theft | Only SHA-256 hash stored in DB; raw token in httpOnly cookie only |
| Session hijacking | Rotation on every refresh; reuse detection revokes all sessions |
| Brute-force login | slowapi: 10 req/min per IP on `POST /api/v1/auth/login` |
| Credential leakage at rest | Fernet-encrypted; config masked on read endpoints |
| SSRF via connection strings | `_validate_host()` checks against `SIPHON_ALLOWED_HOSTS` |
| Path traversal in S3 writes | `_validate_path()` rejects `..` and paths outside `SIPHON_ALLOWED_S3_PREFIX` |
| SFTP host spoofing | `RejectPolicy` hardcoded — `AutoAddPolicy` never used |
| Oversized requests | 413 for bodies over `SIPHON_MAX_REQUEST_SIZE_MB` |
| Credential leakage in logs | `mask_uri()` applied before logging; 422 errors never log request body |
| Container vulnerabilities | Trivy scan (CRITICAL/HIGH) on every CI build |
| Container privilege escalation | Non-root UID 1000 |

---

## Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: siphon
spec:
  replicas: 2  # APScheduler advisory lock prevents duplicate scheduled runs
  template:
    spec:
      terminationGracePeriodSeconds: 600
      containers:
        - name: siphon
          image: ghcr.io/lucasnovack/siphon:latest
          ports:
            - containerPort: 8000
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef: {name: siphon-secrets, key: database-url}
            - name: REDIS_URL
              valueFrom:
                secretKeyRef: {name: siphon-secrets, key: redis-url}
            - name: SIPHON_JWT_SECRET
              valueFrom:
                secretKeyRef: {name: siphon-secrets, key: jwt-secret}
            - name: SIPHON_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef: {name: siphon-secrets, key: encryption-key}
            - name: SIPHON_ALLOWED_HOSTS
              value: "10.0.0.0/8,172.16.0.0/12"
            - name: SIPHON_ALLOWED_S3_PREFIX
              value: "bronze/"
          livenessProbe:
            httpGet: {path: /health/live, port: 8000}
            initialDelaySeconds: 10
            periodSeconds: 15
          readinessProbe:
            httpGet: {path: /health/ready, port: 8000}
            periodSeconds: 10
          resources:
            requests: {memory: "256Mi", cpu: "250m"}
            limits: {memory: "2Gi", cpu: "2"}
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            readOnlyRootFilesystem: true
```

---

## Development

**Prerequisites:** Python 3.12+, [uv](https://github.com/astral-sh/uv), Docker

```bash
# Install dependencies
uv sync

# Lint
uv run ruff check . && uv run ruff format --check .

# Run tests
uv run pytest -v

# Build Docker image
docker build -t siphon:dev .
```

**Project layout:**

```
src/siphon/
├── main.py           # FastAPI app, lifespan, routes, middleware
├── worker.py         # Extraction loop: extract → DQ → schema → write → persist
├── queue.py          # Thin Celery wrapper (enqueue → apply_async by priority)
├── celery_app.py     # Celery configured with Redis broker, queues: high/normal/low
├── tasks.py          # @celery_app.task run_pipeline_task
├── models.py         # Pydantic models
├── orm.py            # SQLAlchemy ORM: User, Connection, Pipeline, Schedule, JobRun, GdprEvent
├── db.py             # Async engine + session factory
├── crypto.py         # Fernet encrypt/decrypt
├── scheduler.py      # APScheduler + pg_try_advisory_xact_lock
├── variables.py      # @TODAY, @LAST_MONTH, etc.
├── metrics.py        # Prometheus counters/histograms
├── auth/             # JWT, bcrypt, refresh token rotation, rate limiting
├── users/            # Admin-only CRUD
├── connections/      # CRUD + /test + /types
├── pipelines/        # CRUD + /trigger + /runs + watermark injection
├── preview/          # Ad-hoc SQL preview (LIMIT 100, SSRF guard)
├── runs/             # History, cursor-based logs, cancel
├── gdpr/             # Audit event log
└── plugins/
    ├── sources/      # sql.py, sftp.py, http_rest.py
    ├── destinations/ # s3_parquet.py
    └── parsers/      # csv_parser.py, json_parser.py, avro_parser.py

alembic/versions/
├── 001  users, connections, pipelines, schedules, job_runs, refresh_tokens
├── 002  dest_connection_id, triggered_by on job_runs
├── 003  PII masking columns
├── 004  webhook + SLA fields
├── 005  Hive partition_by
├── 006  schema registry (last_schema, expected_schema)
├── 007  data lineage (source_connection_id, destination_path)
├── 008  max_concurrent_jobs on connections
├── 009  priority on pipelines
├── 010  soft delete (deleted_at on all entities)
└── 011  gdpr_events table
```
