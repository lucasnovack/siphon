# Siphon

A lightweight, self-hosted data pipeline platform that replaces Apache Spark in the Bronze layer of a medallion architecture. Siphon manages the full lifecycle of data extraction pipelines: connection registry, scheduling, incremental watermarks, data quality checks, schema evolution detection, and Parquet writes to S3-compatible storage — all from a single container.

```
Connections Registry
        │
        ▼
Pipeline (source + dest + schedule + DQ)
        │
        ├─── APScheduler (cron, advisory lock) ───► trigger
        └─── POST /trigger (manual) ──────────────► trigger
                                                        │
                                              Worker (ThreadPool)
                                                        │
                                          source.extract_batches()
                                                        │
                                        watermark injection (incremental)
                                                        │
                                        data quality check (before write)
                                                        │
                                        schema hash diff (after extract)
                                                        │
                                         destination.write() → S3 Parquet
                                                        │
                                              job_runs → PostgreSQL
```

**Why Siphon instead of Spark?**

| | Spark | Siphon |
|---|---|---|
| Cold-start | 2–5 min | < 5s |
| Memory (1M row extract) | ~4 GB | ~200 MB |
| Ops overhead | Cluster + JVM tuning | Single container |
| Deployment | KubernetesOperator + RBAC | `docker run` |
| Oracle support | ojdbc JAR + Instant Client | oracledb thin mode |
| Connection management | External (Airflow Variables) | Built-in registry (Fernet-encrypted) |
| Scheduling | Airflow DAGs | Built-in APScheduler + advisory lock |
| Schema drift detection | Manual | Automatic (SHA-256, never blocks) |
| Data quality gates | External (Great Expectations) | Built-in per-pipeline |

---

## Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [Authentication](#authentication)
- [API Reference](#api-reference)
  - [Legacy Job API](#legacy-job-api-airflow-compatible)
  - [Connections](#connections-api)
  - [Pipelines](#pipelines-api)
  - [Preview](#preview-api)
  - [Runs](#runs-api)
  - [Metrics](#metrics)
- [Configuration](#configuration)
- [Pipeline Examples](#pipeline-examples)
- [Variable Substitution](#variable-substitution)
- [Incremental Mode](#incremental-mode)
- [Schema Evolution](#schema-evolution)
- [Data Quality](#data-quality)
- [Plugin Reference](#plugin-reference)
- [Writing a Custom Plugin](#writing-a-custom-plugin)
- [Security](#security)
- [Kubernetes](#kubernetes)
- [Development](#development)
- [Roadmap](#roadmap)

---

## Quick Start (UI)

The fastest way to get started is through the web UI.

**Prerequisites:** Docker and Docker Compose.

```bash
# 1. Clone and start the full stack
git clone https://github.com/lucasnovack/siphon
cd siphon
docker compose up -d
```

Wait ~20 seconds for all services to start (PostgreSQL, MinIO, Siphon). Siphon automatically runs database migrations on first boot.

```bash
# 2. Check that everything is up
docker compose ps
# siphon should show "healthy"
```

**3. Open the UI:** [http://localhost:8000](http://localhost:8000)

Login with the bootstrap admin credentials set in `docker-compose.yml`:
- Email: `admin@example.com`
- Password: `changeme123`

**4. Create connections** (Connections → New):
- **Source:** type `sql`, connection string e.g. `mysql://siphon:siphon@mysql:3306/testdb`
- **Destination:** type `s3_parquet`, bucket `bronze`, endpoint `http://minio:9000`, access key `minioadmin`, secret `minioadmin`

MinIO browser: [http://localhost:9001](http://localhost:9001) (minioadmin / minioadmin)

**5. Create a pipeline** (Pipelines → New Pipeline):
- Select source connection → write a query → preview 100 rows
- Select destination → set S3 prefix (e.g. `bronze/orders/`)
- Optionally set a cron schedule → Create

**6. Trigger a run** and watch logs in real time on the Runs page.

> **Production note:** before exposing Siphon to any network, replace the default secrets in `docker-compose.yml`:
> - `SIPHON_JWT_SECRET` — any long random string
> - `SIPHON_ADMIN_PASSWORD` — strong password
> - `SIPHON_ENCRYPTION_KEY` — generate with:
>   ```bash
>   docker compose run --rm siphon python -c \
>     "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
>   ```

---

## Quick Start (API)

```bash
# Start the full local stack (Siphon + PostgreSQL + MySQL + MinIO)
docker compose up -d

# 1. Create a source connection
curl -s -X POST http://localhost:8000/api/v1/connections \
  -H "Authorization: Bearer changeme" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-mysql",
    "conn_type": "sql",
    "config": {"connection": "mysql://user:pass@mysql:3306/mydb"}
  }'
# → {"id": "a1b2...", "name": "prod-mysql", ...}

# 2. Create a destination connection
curl -s -X POST http://localhost:8000/api/v1/connections \
  -H "Authorization: Bearer changeme" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "minio-bronze",
    "conn_type": "s3_parquet",
    "config": {
      "bucket": "bronze",
      "endpoint": "http://minio:9000",
      "access_key": "minioadmin",
      "secret_key": "minioadmin"
    }
  }'

# 3. Create a pipeline
curl -s -X POST http://localhost:8000/api/v1/pipelines \
  -H "Authorization: Bearer changeme" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-daily",
    "source_connection_id": "<src-conn-id>",
    "dest_connection_id": "<dest-conn-id>",
    "query": "SELECT * FROM orders WHERE updated_at > :watermark",
    "dest_prefix": "bronze/orders/",
    "extraction_mode": "incremental",
    "incremental_key": "updated_at",
    "cron": "0 3 * * *",
    "is_active": true,
    "min_rows_expected": 100
  }'

# 4. Trigger manually
curl -s -X POST http://localhost:8000/api/v1/pipelines/<pipeline-id>/trigger \
  -H "Authorization: Bearer changeme"
# → {"job_id": "...", "run_id": 1}

# 5. Watch the run
curl -s "http://localhost:8000/api/v1/runs?pipeline_id=<pipeline-id>" \
  -H "Authorization: Bearer changeme"
```

**Prefer using the legacy job API directly (Airflow)?** See [Legacy Job API](#legacy-job-api-airflow-compatible) — it remains fully supported.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                          FastAPI App                              │
│                                                                   │
│  get_current_principal ── API key (Airflow) OR JWT (UI/CLI)       │
│                                                                   │
│  /api/v1/connections ──► Connection registry (Fernet-encrypted)   │
│  /api/v1/pipelines   ──► Pipeline CRUD + schedule sync            │
│  /api/v1/preview     ──► Ad-hoc SQL preview (LIMIT 100)           │
│  /api/v1/runs        ──► Run history + logs cursor + cancel       │
│  /metrics            ──► Prometheus text (jobs, rows, queue)      │
│                                                                   │
│  APScheduler ──► pg_try_advisory_xact_lock ──► trigger            │
│  POST /trigger ─────────────────────────────► trigger             │
│                                                   │               │
│                                     asyncio Queue + ThreadPool    │
│                                                   │               │
│                                           Worker per job          │
│                                                   │               │
│                            source.extract_batches() (streaming)   │
│                                                   │               │
│                            watermark injection (incremental only) │
│                                                   │               │
│                            _check_data_quality() → fail fast      │
│                                                   │               │
│                            _compute_schema_hash() → drift detect  │
│                                                   │               │
│                            destination.write(batch) → S3 Parquet  │
│                                                   │               │
│                            _persist_job_run() → PostgreSQL        │
│                            _update_pipeline_metadata()            │
└──────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

- Jobs run in a `ThreadPoolExecutor` — blocking I/O (DB drivers, SFTP) never blocks the event loop.
- Sources implement `extract_batches()` — a generator yielding `pa.Table` chunks. Memory stays bounded regardless of table size.
- When data quality rules are active, batches are buffered, checked in full, then written or rejected atomically.
- `_persist_job_run` does an UPDATE when a pre-created `job_runs` row exists (trigger path), or INSERT otherwise (legacy path) — never creates duplicates.
- APScheduler uses `pg_try_advisory_xact_lock` before each fire — safe for multi-pod / rolling deploys without Recreate strategy.

---

## Authentication

Siphon supports two auth methods via the same `Authorization: Bearer <token>` header:

| Method | Token | Use case |
|---|---|---|
| **API key** | `SIPHON_API_KEY` value | Airflow service accounts, scripts |
| **JWT** | Access token from `POST /api/v1/auth/login` | Human users, UI |

Health probes (`/health/live`, `/health/ready`) are always public. Roles: `admin` (full CRUD) and `operator` (read + trigger only).

### Getting a JWT token

```bash
# Login
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "secret"}'
# → {"access_token": "eyJ...", "token_type": "bearer"}
# A refresh token cookie is also set (httpOnly, 7-day TTL)

# Refresh (uses the httpOnly cookie automatically)
curl -s -X POST http://localhost:8000/api/v1/auth/refresh \
  --cookie-jar cookies.txt --cookie cookies.txt

# Logout
curl -s -X POST http://localhost:8000/api/v1/auth/logout \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "<token>"}'
```

Access tokens expire in 15 minutes. Presenting a revoked refresh token invalidates **all** sessions for that user.

---

## API Reference

All endpoints require `Authorization: Bearer <token>` unless noted.

---

### Legacy Job API (Airflow-compatible)

The original `POST /jobs` API is fully preserved — existing Airflow DAGs require no changes.

#### `POST /jobs`

Submit an async extraction job directly.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "Authorization: Bearer $SIPHON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {"type": "sql", "uri": "mysql://...", "query": "SELECT * FROM orders"},
    "destination": {"type": "s3_parquet", "bucket": "bronze", "prefix": "bronze/orders/", ...}
  }'
# → {"job_id": "3f2a..."}
```

- `202` — accepted | `429` — queue full | `401` — auth | `413` — body too large

#### `GET /jobs/{job_id}`

```json
{
  "job_id": "3f2a...",
  "status": "success",
  "rows_read": 42000,
  "rows_written": 42000,
  "started_at": "2025-01-15T08:00:01Z",
  "finished_at": "2025-01-15T08:00:14Z"
}
```

Status: `pending` → `running` → `success` | `failed` | `partial_success`

#### `GET /jobs/{job_id}/logs`

Returns `{"job_id": "...", "logs": ["[ts] [INFO] ..."]}`.

---

### Connections API

Connections store encrypted credentials for sources and destinations. Config blobs are Fernet-encrypted at rest using `SIPHON_ENCRYPTION_KEY`.

| Method | Endpoint | Description | Role |
|---|---|---|---|
| `GET` | `/api/v1/connections` | List all connections | any |
| `GET` | `/api/v1/connections/types` | Supported connection types | any |
| `POST` | `/api/v1/connections` | Create connection | admin |
| `GET` | `/api/v1/connections/{id}` | Get connection (config masked) | any |
| `PUT` | `/api/v1/connections/{id}` | Update connection | admin |
| `DELETE` | `/api/v1/connections/{id}` | Delete connection | admin |
| `POST` | `/api/v1/connections/{id}/test` | Test connectivity | admin |

```bash
# Create a SQL source connection
curl -X POST http://localhost:8000/api/v1/connections \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-mysql",
    "conn_type": "sql",
    "config": {"connection": "mysql://user:pass@host:3306/mydb"}
  }'

# Test it
curl -X POST http://localhost:8000/api/v1/connections/<id>/test \
  -H "Authorization: Bearer $TOKEN"
# → {"ok": true} or {"ok": false, "error": "..."}
```

**Supported types:** `sql` (MySQL, PostgreSQL, MSSQL, Oracle), `s3_parquet`, `sftp`

---

### Pipelines API

Pipelines define what to extract, where to write it, how often, and with what guardrails.

| Method | Endpoint | Description | Role |
|---|---|---|---|
| `GET` | `/api/v1/pipelines` | List pipelines | any |
| `POST` | `/api/v1/pipelines` | Create pipeline | admin |
| `GET` | `/api/v1/pipelines/{id}` | Get pipeline | any |
| `PUT` | `/api/v1/pipelines/{id}` | Update pipeline + resync schedule | admin |
| `DELETE` | `/api/v1/pipelines/{id}` | Delete pipeline | admin |
| `POST` | `/api/v1/pipelines/{id}/trigger` | Trigger manual run | admin, operator |
| `GET` | `/api/v1/pipelines/{id}/runs` | Paginated run history | any |

**Pipeline fields:**

| Field | Type | Description |
|---|---|---|
| `name` | string | Unique display name |
| `source_connection_id` | uuid | Source connection |
| `dest_connection_id` | uuid | Destination connection |
| `query` | string | SQL query (variables and watermark injected at runtime) |
| `dest_prefix` | string | S3 key prefix |
| `extraction_mode` | `full_refresh` \| `incremental` | Default: `full_refresh` |
| `incremental_key` | string | Column used for watermark filtering (incremental only) |
| `cron` | string | 5-field cron expression (e.g. `0 3 * * *`) |
| `is_active` | bool | Whether the schedule is active |
| `min_rows_expected` | int | DQ: fail if fewer rows extracted |
| `max_rows_drop_pct` | int | DQ: fail if row count drops more than N% vs previous run |

```bash
# Trigger with explicit date range (overrides watermark)
curl -X POST http://localhost:8000/api/v1/pipelines/<id>/trigger \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"date_from": "2024-01-01T00:00:00Z", "date_to": "2024-02-01T00:00:00Z"}'
```

---

### Preview API

Run a read-only SQL query against any `sql` connection. Always wrapped in `SELECT * FROM (...) LIMIT 100` before execution.

```bash
curl -X POST http://localhost:8000/api/v1/preview \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "connection_id": "<sql-connection-id>",
    "query": "SELECT id, name, amount FROM orders WHERE status = '\''pending'\''"
  }'
# → [{"id": 1, "name": "Alice", "amount": 99.5}, ...]
```

- `400` — not a SQL connection | `404` — connection not found

---

### Runs API

Global job run history backed by PostgreSQL. In-flight run logs are served from the in-memory job queue with cursor-based pagination.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/runs` | List all runs, newest-first (query: `?pipeline_id=`, `?status=`, `?limit=`, `?offset=`) |
| `GET` | `/api/v1/runs/{id}` | Get a single run |
| `GET` | `/api/v1/runs/{id}/logs` | Stream logs with cursor (`?since=<offset>`) |
| `POST` | `/api/v1/runs/{id}/cancel` | Cancel a run (admin only) |

```bash
# Tail logs with cursor
curl "http://localhost:8000/api/v1/runs/42/logs?since=0" -H "Authorization: Bearer $TOKEN"
# → {"logs": ["line1", "line2"], "next_offset": 2, "done": false}

# Next page
curl "http://localhost:8000/api/v1/runs/42/logs?since=2" -H "Authorization: Bearer $TOKEN"
```

Run fields include: `job_id`, `pipeline_id`, `status`, `triggered_by` (`manual` | `schedule`), `rows_read`, `rows_written`, `duration_ms`, `schema_changed`, `error`, `started_at`, `finished_at`.

---

### Metrics

```bash
curl http://localhost:8000/metrics
```

Returns Prometheus text format:

| Metric | Type | Labels |
|---|---|---|
| `siphon_jobs_total` | Counter | `status` |
| `siphon_job_duration_seconds` | Histogram | — |
| `siphon_rows_extracted_total` | Counter | — |
| `siphon_queue_depth` | Gauge | — |
| `siphon_schema_changes_total` | Counter | — |

---

## Configuration

**Core**

| Variable | Default | Description |
|---|---|---|
| `SIPHON_API_KEY` | *(unset)* | Legacy API key. If unset, API key auth is disabled and a warning is logged at startup. |
| `SIPHON_PORT` | `8000` | HTTP port |
| `SIPHON_MAX_WORKERS` | `10` | ThreadPoolExecutor max workers |
| `SIPHON_MAX_QUEUE` | `50` | Max pending jobs before 429 |
| `SIPHON_JOB_TIMEOUT` | `3600` | Per-job timeout in seconds |
| `SIPHON_JOB_TTL_SECONDS` | `3600` | How long finished jobs stay in memory |
| `SIPHON_MAX_REQUEST_SIZE_MB` | `1` | Request body size limit |
| `SIPHON_ALLOWED_HOSTS` | *(all)* | Comma-separated hostnames or CIDRs for SSRF guard |
| `SIPHON_ALLOWED_S3_PREFIX` | *(all)* | Required path prefix for S3 writes |
| `SIPHON_S3_SCHEME` | `https` | `http` or `https` for S3 endpoint |
| `SIPHON_MAX_FILE_SIZE_MB` | `500` | Max SFTP file size |
| `SIPHON_SFTP_KNOWN_HOSTS` | *(unset)* | Path to SSH known_hosts file |
| `SIPHON_SFTP_HOST_KEY` | *(unset)* | Base64-encoded host public key |
| `SIPHON_ENABLE_SYNC_EXTRACT` | `false` | Enable `POST /extract` (dev only) |
| `SIPHON_DRAIN_TIMEOUT` | `3600` | Seconds to wait for active jobs on SIGTERM |

**PostgreSQL + Auth**

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(unset)* | PostgreSQL async URL: `postgresql+asyncpg://user:pass@host/db`. DB-backed features return 503 if unset. |
| `SIPHON_JWT_SECRET` | *(dev fallback)* | HMAC secret for JWT. **Always set in production.** |
| `SIPHON_ADMIN_EMAIL` | *(unset)* | Bootstrap admin email (used only when no users exist). |
| `SIPHON_ADMIN_PASSWORD` | *(unset)* | Bootstrap admin password (bcrypt-hashed at startup). |

**Connections + Encryption**

| Variable | Default | Description |
|---|---|---|
| `SIPHON_ENCRYPTION_KEY` | *(unset)* | Fernet key for encrypting connection configs at rest. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. **Required** when using the connections API. |

---

## Pipeline Examples

### Full refresh (legacy API)

```json
{
  "source": {
    "type": "sql",
    "uri": "mysql://user:pass@mysql:3306/mydb",
    "query": "SELECT id, name, amount FROM orders WHERE DATE(created_at) = @TODAY",
    "chunk_size": 50000
  },
  "destination": {
    "type": "s3_parquet",
    "bucket": "bronze",
    "prefix": "bronze/orders/date=@TODAY/",
    "endpoint": "http://minio:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
  }
}
```

### Oracle → Parquet

Oracle uses oracledb thin mode — no Instant Client required:

```json
{
  "source": {
    "type": "sql",
    "uri": "oracle+oracledb://user:pass@oracle-host:1521/MYSERVICE",
    "query": "SELECT * FROM SCHEMA.TABLE WHERE TRUNC(DT_CREATED) = TRUNC(SYSDATE-1)",
    "chunk_size": 10000
  },
  "destination": {
    "type": "s3_parquet",
    "bucket": "bronze",
    "prefix": "bronze/oracle_table/date=@TODAY/",
    "endpoint": "http://minio:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
  }
}
```

### SFTP → Parquet (atomic file moves)

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
    "endpoint": "http://minio:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
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

| Variable | Resolves to | Example |
|---|---|---|
| `@TODAY` | Current date | `2025-01-15` |
| `@MIN_DATE` | `1900-01-01` | `1900-01-01` |
| `@LAST_MONTH` | First day of last month | `2025-01-01` |
| `@NEXT_MONTH` | First day of next month | `2025-03-01` |

---

## Incremental Mode

Set `extraction_mode: incremental` and `incremental_key` on a pipeline. Siphon wraps the query with a type-aware `WHERE` clause at trigger time:

```sql
-- Original query
SELECT id, updated_at FROM orders

-- Becomes (PostgreSQL):
WITH _siphon_base AS (
  SELECT id, updated_at FROM orders
)
SELECT * FROM _siphon_base
WHERE updated_at > CAST('2024-06-01T00:00:00+00:00' AS TIMESTAMPTZ)
```

The watermark cast adapts per dialect: `DATETIME` (MySQL), `TIMESTAMPTZ` (PostgreSQL), `TIMESTAMP WITH TIME ZONE` (Oracle), `DATETIMEOFFSET` (MSSQL).

`pipelines.last_watermark` is updated **only after** the `job_runs` row is successfully persisted — avoiding the 2-phase-commit race on pod kill.

---

## Schema Evolution

After each extraction, Siphon computes a SHA-256 hash of the Arrow schema (field names + types). If the hash differs from `pipelines.last_schema_hash`:

- `job_runs.schema_changed = true`
- A warning is appended to the run logs
- The write proceeds normally — the schema change **never blocks** extraction
- `pipelines.last_schema_hash` is updated after the run

Engineers can query `SELECT * FROM job_runs WHERE schema_changed = true ORDER BY created_at DESC` to audit drift, or watch for the badge in the upcoming UI (Phase 10).

---

## Data Quality

Per-pipeline optional guardrails, evaluated after extraction and before writing:

| Config key | Type | Behavior on failure |
|---|---|---|
| `min_rows_expected` | int | Fail if `rows_read < min_rows_expected` |
| `max_rows_drop_pct` | int | Fail if `rows_read` dropped > N% vs previous run |

On failure: job is marked `failed`, no data is written to S3, error message stored in `job_runs.error`.

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
| `partition_range` | no | `[min, max]` override |

| URI prefix | Backend |
|---|---|
| `mysql://`, `postgresql://`, `mssql://` | ConnectorX (Rust, Arrow-native) |
| `oracle+oracledb://` | oracledb thin mode + cursor streaming |

### S3 Parquet Destination (`type: s3_parquet`)

| Field | Required | Description |
|---|---|---|
| `bucket` | yes | S3/MinIO bucket |
| `prefix` | yes | Key prefix |
| `endpoint` | no | Override for MinIO / S3-compatible |
| `access_key` | yes | AWS/MinIO access key |
| `secret_key` | yes | AWS/MinIO secret key |
| `region` | no | AWS region (default: `us-east-1`) |

### SFTP Source (`type: sftp`)

| Field | Required | Description |
|---|---|---|
| `host` | yes | SFTP hostname |
| `port` | no | SSH port (default: `22`) |
| `username` | yes | SSH username |
| `password` / `private_key` | one of | Auth credentials |
| `remote_path` | yes | Remote directory |
| `parser` | yes | Parser plugin name |
| `skip_patterns` | no | fnmatch patterns to skip |
| `move_to_processing` / `move_to_processed` | no | Atomic file move directories |
| `fail_fast` | no | Stop on first error (default: `true`) |
| `chunk_size` | no | Files per Arrow batch (default: `100`) |

**Security:** `RejectPolicy` is hardcoded — unknown host keys are always rejected. Configure `SIPHON_SFTP_KNOWN_HOSTS` or `SIPHON_SFTP_HOST_KEY`.

---

## Writing a Custom Plugin

Plugins are auto-discovered at startup from `plugins/sources/`, `plugins/destinations/`, and `plugins/parsers/`. Drop a file in the right directory — no core changes needed.

### Custom Source

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

### Custom Destination

```python
# src/siphon/plugins/destinations/my_dest.py
import pyarrow as pa
from siphon.plugins.destinations import register
from siphon.plugins.destinations.base import Destination

@register("my_dest")
class MyDestination(Destination):
    def write(self, table: pa.Table, is_first_chunk: bool) -> int:
        print(table.to_pandas())
        return table.num_rows
```

### Custom Parser (SFTP)

```python
# src/siphon/plugins/parsers/csv_parser.py
import io, pyarrow as pa, pyarrow.csv as pacsv
from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser

@register("csv_parser")
class CsvParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        return pacsv.read_csv(io.BytesIO(data))
```

---

## Security

| Threat | Mitigation |
|---|---|
| Unauthorized access | `Authorization: Bearer` required on all endpoints (API key or JWT) |
| Compromised JWT secret | Startup warning if unset; 15-min access token TTL |
| Refresh token theft | Only SHA-256 hash stored in DB; raw token only in httpOnly cookie |
| Session hijacking | Rotation on every refresh; reuse detection revokes all sessions |
| Brute-force login | slowapi: 10 req/min per IP on `POST /api/v1/auth/login` |
| Connection credential leakage | Fernet-encrypted at rest; config masked on read endpoints |
| SSRF via connection strings | `_validate_host()` checks against `SIPHON_ALLOWED_HOSTS` |
| Path traversal in S3 writes | `_validate_path()` rejects `..` and paths outside `SIPHON_ALLOWED_S3_PREFIX` |
| SFTP host spoofing | `RejectPolicy` — `AutoAddPolicy` is never used |
| SFTP large file bomb | Files over `SIPHON_MAX_FILE_SIZE_MB` are skipped |
| Container privilege escalation | Non-root UID 1000 |
| Oversized request bodies | 413 for bodies over `SIPHON_MAX_REQUEST_SIZE_MB` |
| Container vulnerabilities | Trivy scan (CRITICAL/HIGH, ignore-unfixed) on every CI build |
| Credential leakage in logs | `mask_uri()` applied before logging; 422 errors never log the request body |

---

## Kubernetes

```yaml
# k8s/deployment.yaml — safe for multi-pod thanks to APScheduler advisory lock
apiVersion: apps/v1
kind: Deployment
metadata:
  name: siphon
spec:
  replicas: 2        # advisory lock prevents duplicate scheduled runs
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
                secretKeyRef:
                  name: siphon-secrets
                  key: database-url
            - name: SIPHON_API_KEY
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: api-key
            - name: SIPHON_JWT_SECRET
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: jwt-secret
            - name: SIPHON_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: encryption-key
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

# Unit tests (no external services)
uv run pytest --ignore=tests/test_integration_sql.py \
              --ignore=tests/test_integration_s3.py \
              --ignore=tests/test_integration_sftp.py -v

# Start local services
docker compose up -d mysql minio sftp

# Integration tests
DATABASE_URL=postgresql+asyncpg://siphon:siphon@localhost:5432/siphon \
SIPHON_ALLOWED_S3_PREFIX=bronze/ SIPHON_S3_SCHEME=http \
  uv run pytest tests/test_integration_sql.py \
               tests/test_integration_s3.py \
               tests/test_integration_sftp.py -m integration -v

# Build Docker image
docker build -t siphon:dev .
```

**Project layout:**

```
src/siphon/
├── main.py                    # FastAPI app, routers, lifespan, metrics endpoint
├── queue.py                   # Asyncio job queue + ThreadPoolExecutor
├── worker.py                  # Extraction loop: extract → DQ → schema hash → write → persist
├── models.py                  # Pydantic models (Job, ExtractRequest, JobStatus, …)
├── variables.py               # @TODAY, @LAST_MONTH, etc.
├── orm.py                     # SQLAlchemy: User, Connection, Pipeline, Schedule, JobRun, RefreshToken
├── db.py                      # Async engine, session factory, get_db dependency
├── crypto.py                  # Fernet encrypt/decrypt (SIPHON_ENCRYPTION_KEY)
├── metrics.py                 # Prometheus counters/histograms
├── scheduler.py               # APScheduler + pg_try_advisory_xact_lock
├── auth/
│   ├── deps.py                # Principal + get_current_principal (dual-auth)
│   ├── jwt_utils.py           # JWT create/decode, refresh token, bcrypt
│   └── router.py              # /login, /refresh, /logout, /me
├── users/
│   └── router.py              # Admin-only CRUD /api/v1/users
├── connections/
│   └── router.py              # CRUD + /test + /types
├── pipelines/
│   ├── router.py              # CRUD + /trigger + /runs + schedule sync
│   └── watermark.py           # inject_watermark() + _cast_for_dialect()
├── preview/
│   └── router.py              # POST /api/v1/preview (LIMIT 100, SSRF guard)
├── runs/
│   └── router.py              # List, logs cursor, cancel
└── plugins/
    ├── sources/               # sql.py (ConnectorX + oracledb), sftp.py
    ├── destinations/          # s3_parquet.py
    └── parsers/               # example_parser.py (stub)

alembic/versions/
├── 001_initial_schema.py      # users, connections, pipelines, schedules, job_runs, refresh_tokens
└── 002_add_dest_connection_triggered_by.py
```

---

## Roadmap

| Phase | Status | Description |
|---|---|---|
| 1–6 | ✅ | Core extraction engine: queue, worker, SQL/SFTP/S3 plugins, variable substitution |
| 7 | ✅ | Hotfixes: TTL eviction, SFTP atomic moves, `/extract` guard |
| 7.5 | ✅ | Oracle cursor streaming (no pandas, oracledb native fetchmany) |
| 8 | ✅ | PostgreSQL persistence + JWT auth + token rotation + user management |
| **9** | ✅ | **Connections + Pipelines API, APScheduler, incremental watermarks, DQ, schema evolution, preview, runs, Prometheus** |
| **10** | ✅ | **React UI: pipeline wizard, query editor (CodeMirror), run history, schema drift badge, settings** |
| 11 | ⏳ | Kubernetes manifests (deployment, service, HPA) |
| 12 | ⏳ | Airflow SiphonOperator package |

---

## License

MIT
