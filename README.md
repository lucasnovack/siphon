# Siphon

A lightweight, self-hosted data extraction service that replaces Apache Spark in the Bronze layer of a medallion data pipeline. Siphon extracts data from SQL databases and SFTP servers and writes Parquet files directly to S3-compatible object storage.

```
Source (SQL / SFTP)  →  Siphon  →  S3 / MinIO (Parquet)
```

**Why Siphon instead of Spark?**

| | Spark | Siphon |
|---|---|---|
| Cold-start | 2–5 min | < 5s |
| Memory for a 1M row extract | ~4 GB | ~200 MB |
| Ops overhead | Cluster + JVM | Single container |
| Deployment | KubernetesOperator + RBAC | `docker run` |
| Oracle support | ojdbc JAR | oracledb thin mode (no Instant Client) |

---

## Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Pipeline Examples](#pipeline-examples)
- [Variable Substitution](#variable-substitution)
- [Plugin Reference](#plugin-reference)
- [Writing a Custom Plugin](#writing-a-custom-plugin)
- [Security](#security)
- [Kubernetes](#kubernetes)
- [Development](#development)

---

## Quick Start

```bash
# Run with Docker Compose (includes MySQL + MinIO)
docker compose up -d

# Submit an extraction job
curl -s -X POST http://localhost:8000/jobs \
  -H "X-API-Key: changeme" \
  -H "Content-Type: application/json" \
  -d '{
    "source": {
      "type": "sql",
      "uri": "mysql://siphon:siphon@mysql:3306/testdb",
      "query": "SELECT * FROM orders WHERE created_at >= @LAST_MONTH"
    },
    "destination": {
      "type": "s3_parquet",
      "bucket": "bronze",
      "prefix": "bronze/orders/",
      "endpoint": "http://minio:9000",
      "access_key": "minioadmin",
      "secret_key": "minioadmin"
    }
  }'
# → {"job_id": "3f2a..."}

# Poll until done
curl -s http://localhost:8000/jobs/3f2a... -H "X-API-Key: changeme"
# → {"status": "success", "rows_read": 42000, "rows_written": 42000}
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    FastAPI App                       │
│                                                      │
│  POST /jobs ──► asyncio Queue ──► ThreadPoolExecutor │
│                                         │            │
│                               Worker loop per job    │
│                                         │            │
│                                source.extract_batches()
│                                         │            │
│                         (optional) parser.parse()    │
│                                         │            │
│                          destination.write(batch)    │
└─────────────────────────────────────────────────────┘
```

**Key design decisions:**

- Jobs are executed in a `ThreadPoolExecutor` so blocking I/O (database drivers, SFTP) does not block the event loop.
- Sources implement `extract_batches()` — a generator that yields `pa.Table` chunks. The worker streams these directly to the destination, keeping memory bounded.
- Destinations receive `is_first_chunk: bool` so they can decide between `delete_matching` (first) and `overwrite_or_ignore` (subsequent) write modes.
- `rows_read == rows_written` is validated before marking a job as `success`.

---

## API Reference

All endpoints require the `X-API-Key` header (set via `SIPHON_API_KEY`).

### `POST /jobs`

Submit an async extraction job.

```bash
curl -X POST http://localhost:8000/jobs \
  -H "X-API-Key: $SIPHON_API_KEY" \
  -H "Content-Type: application/json" \
  -d @job.json
```

Returns `{"job_id": "<uuid>"}` immediately. The job runs in the background.

**Response codes:**
- `202` — job accepted
- `429` — queue is full (`SIPHON_MAX_QUEUE_SIZE` reached)
- `401` — missing or invalid API key
- `413` — request body too large

---

### `POST /extract`

Synchronous extraction — blocks until the job completes. Same request body as `/jobs`.

---

### `GET /jobs/{job_id}`

Poll a job's status.

```bash
curl http://localhost:8000/jobs/3f2a... -H "X-API-Key: $SIPHON_API_KEY"
```

```json
{
  "job_id": "3f2a1c...",
  "status": "success",
  "rows_read": 42000,
  "rows_written": 42000,
  "started_at": "2025-01-15T08:00:01Z",
  "finished_at": "2025-01-15T08:00:14Z",
  "failed_files": []
}
```

**Status values:** `pending` → `running` → `success` | `failed` | `partial_success`

`partial_success` is returned when `fail_fast: false` is set on an SFTP source and some files could not be downloaded.

---

### `GET /jobs/{job_id}/logs`

Stream logs for a specific job.

```bash
curl http://localhost:8000/jobs/3f2a.../logs -H "X-API-Key: $SIPHON_API_KEY"
```

```json
{
  "job_id": "3f2a1c...",
  "logs": [
    "[2025-01-15T08:00:01Z] [INFO] Job started",
    "[2025-01-15T08:00:14Z] [INFO] rows_read=42000 rows_written=42000"
  ]
}
```

---

### `GET /health`

Returns service metadata and current queue depth.

### `GET /health/live`

Liveness probe. Returns `200` if the process is alive.

### `GET /health/ready`

Readiness probe. Returns `503` when the queue is full.

---

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|---|---|---|
| `SIPHON_API_KEY` | `changeme` | API key required in `X-API-Key` header |
| `SIPHON_PORT` | `8000` | HTTP port |
| `SIPHON_MAX_WORKERS` | `4` | ThreadPoolExecutor max workers |
| `SIPHON_MAX_QUEUE_SIZE` | `100` | Max pending jobs before 429 |
| `SIPHON_MAX_REQUEST_SIZE_MB` | `1` | Request body size limit (MB) |
| `SIPHON_ALLOWED_HOSTS` | *(empty = all allowed)* | Comma-separated hostnames or CIDR blocks for SSRF guard |
| `SIPHON_ALLOWED_S3_PREFIX` | *(empty = all allowed)* | Required path prefix for S3 writes (e.g. `bronze/`) |
| `SIPHON_S3_SCHEME` | `https` | `http` or `https` for S3 endpoint |
| `SIPHON_MAX_FILE_SIZE_MB` | `500` | Max SFTP file size to download |
| `SIPHON_SFTP_KNOWN_HOSTS` | *(empty)* | Path to SSH known_hosts file |
| `SIPHON_SFTP_HOST_KEY` | *(empty)* | Base64-encoded host public key (alternative to known_hosts) |

---

## Pipeline Examples

### SQL → Parquet (incremental daily load)

Extract yesterday's rows from a MySQL table and write Parquet to MinIO:

```json
{
  "source": {
    "type": "sql",
    "uri": "mysql://user:pass@mysql-host:3306/mydb",
    "query": "SELECT id, name, amount, created_at FROM orders WHERE DATE(created_at) = @TODAY",
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

Variables (`@TODAY`, `@LAST_MONTH`, etc.) are resolved at job execution time — see [Variable Substitution](#variable-substitution).

---

### SQL → Parquet (full monthly extract with partitioning)

Use ConnectorX partitioned reads for large tables:

```json
{
  "source": {
    "type": "sql",
    "uri": "postgresql://user:pass@pg-host:5432/mydb",
    "query": "SELECT * FROM events WHERE created_at BETWEEN @LAST_MONTH AND @TODAY",
    "partition_on": "id",
    "partition_num": 8,
    "chunk_size": 100000
  },
  "destination": {
    "type": "s3_parquet",
    "bucket": "bronze",
    "prefix": "bronze/events/month=@LAST_MONTH/",
    "endpoint": "http://minio:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin"
  }
}
```

`partition_on` splits the query across `partition_num` parallel connections using ConnectorX's native partitioning. Only works with numeric or date columns.

---

### Oracle → Parquet

Oracle uses oracledb thin mode (no Instant Client required):

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

---

### SFTP → Parquet (with atomic file moves)

Download CSV files from an SFTP server, parse them, and write to Parquet:

```json
{
  "source": {
    "type": "sftp",
    "host": "sftp.example.com",
    "port": 22,
    "username": "ftpuser",
    "password": "secret",
    "remote_path": "/upload",
    "parser": "csv_parser",
    "skip_patterns": ["*.tmp", "*.lock", "archive_*"],
    "move_to_processing": "/processing",
    "move_to_processed": "/processed",
    "fail_fast": false,
    "chunk_size": 100
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

With `fail_fast: false`, if some files fail (network error, parse error), the job completes with `partial_success` and `failed_files` lists the filenames that could not be processed.

---

### Airflow DAG integration

Use the `SiphonOperator` (Fase 8, coming soon) or call the API directly with `SimpleHttpOperator`:

```python
from datetime import datetime
from airflow import DAG
from airflow.providers.http.operators.http import SimpleHttpOperator
from airflow.providers.http.sensors.http import HttpSensor
import json

with DAG(
    dag_id="orders_to_bronze",
    start_date=datetime(2025, 1, 1),
    schedule_interval="0 2 * * *",
    catchup=False,
) as dag:

    submit_job = SimpleHttpOperator(
        task_id="submit_extraction",
        http_conn_id="siphon_default",   # Connection: host=http://siphon:8000
        endpoint="/jobs",
        method="POST",
        headers={"X-API-Key": "{{ var.value.siphon_api_key }}", "Content-Type": "application/json"},
        data=json.dumps({
            "source": {
                "type": "sql",
                "uri": "mysql://{{ var.value.mysql_uri }}",
                "query": "SELECT * FROM orders WHERE DATE(created_at) = @TODAY"
            },
            "destination": {
                "type": "s3_parquet",
                "bucket": "bronze",
                "prefix": "bronze/orders/date={{ ds }}/",
                "endpoint": "{{ var.value.minio_endpoint }}",
                "access_key": "{{ var.value.minio_access_key }}",
                "secret_key": "{{ var.value.minio_secret_key }}"
            }
        }),
        response_filter=lambda r: r.json()["job_id"],
        do_xcom_push=True,
    )

    wait_for_job = HttpSensor(
        task_id="wait_for_completion",
        http_conn_id="siphon_default",
        endpoint="/jobs/{{ ti.xcom_pull(task_ids='submit_extraction') }}",
        headers={"X-API-Key": "{{ var.value.siphon_api_key }}"},
        response_check=lambda r: r.json()["status"] in ("success", "partial_success", "failed"),
        poke_interval=15,
        timeout=3600,
        mode="reschedule",
    )

    submit_job >> wait_for_job
```

---

## Variable Substitution

Siphon resolves variables in SQL queries and S3 prefixes at job execution time. All dates use the system timezone (set `TZ` env var to control).

| Variable | Resolves to | Example |
|---|---|---|
| `@TODAY` | Current date | `2025-01-15` |
| `@MIN_DATE` | `1900-01-01` | `1900-01-01` |
| `@LAST_MONTH` | First day of last month | `2025-01-01` |
| `@NEXT_MONTH` | First day of next month | `2025-03-01` |

Variables work in SQL `WHERE` clauses, `BETWEEN` expressions, and S3 prefix strings.

---

## Plugin Reference

### SQL Source (`type: sql`)

| Field | Required | Description |
|---|---|---|
| `uri` | yes | Connection string: `mysql://`, `postgresql://`, `mssql://`, `oracle+oracledb://` |
| `query` | yes | SQL query (variables resolved before execution) |
| `chunk_size` | no | Rows per batch (default: `100000`). Not used with ConnectorX partitioning. |
| `partition_on` | no | Column to partition by (ConnectorX only, MySQL/PG/MSSQL) |
| `partition_num` | no | Number of partitions (default: `4`) |
| `partition_range` | no | `[min, max]` override for partitioning |

**Supported dialects and backends:**

| URI prefix | Backend |
|---|---|
| `mysql://` | ConnectorX (Rust, returns Arrow directly) |
| `postgresql://` | ConnectorX |
| `mssql://` | ConnectorX |
| `oracle+oracledb://` | pandas + oracledb thin mode |

---

### S3 Parquet Destination (`type: s3_parquet`)

| Field | Required | Description |
|---|---|---|
| `bucket` | yes | S3/MinIO bucket name |
| `prefix` | yes | Key prefix (must start with `SIPHON_ALLOWED_S3_PREFIX` if set) |
| `endpoint` | no | Override endpoint for MinIO or other S3-compatible stores |
| `access_key` | yes | AWS access key / MinIO user |
| `secret_key` | yes | AWS secret key / MinIO password |
| `region` | no | AWS region (default: `us-east-1`) |

Writes `*.parquet` files using Hive-style partitioning compatible with Spark, Trino, and Athena.

---

### SFTP Source (`type: sftp`)

| Field | Required | Description |
|---|---|---|
| `host` | yes | SFTP hostname |
| `port` | no | SSH port (default: `22`) |
| `username` | yes | SSH username |
| `password` | no | Password auth |
| `private_key` | no | PEM-encoded private key (alternative to password) |
| `remote_path` | yes | Directory to list files from |
| `parser` | yes | Parser plugin name (e.g. `csv_parser`) |
| `skip_patterns` | no | List of fnmatch patterns to skip |
| `move_to_processing` | no | Directory to rename files to before downloading |
| `move_to_processed` | no | Directory to rename files to after successful download |
| `fail_fast` | no | Stop on first error (default: `true`). Set `false` for `partial_success`. |
| `chunk_size` | no | Files per Arrow batch (default: `100`) |

**Security:** Siphon uses `RejectPolicy` — it will never auto-accept an unknown host key. Configure `SIPHON_SFTP_KNOWN_HOSTS` or `SIPHON_SFTP_HOST_KEY` before connecting.

---

## Writing a Custom Plugin

Plugins are discovered automatically at startup from the `plugins/sources/`, `plugins/destinations/`, and `plugins/parsers/` directories.

### Custom Parser (e.g. CSV)

```python
# src/siphon/plugins/parsers/csv_parser.py
import io
import pyarrow as pa
import pyarrow.csv as pacsv
from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser

@register("csv_parser")
class CsvParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        return pacsv.read_csv(io.BytesIO(data))
```

Drop the file in the parsers directory — it will be picked up on the next restart.

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
        # yield one pa.Table per chunk
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
        # return number of rows written
        print(table.to_pandas())
        return table.num_rows
```

---

## Security

| Threat | Mitigation |
|---|---|
| Unauthorized access | `X-API-Key` header required on all endpoints |
| Credential leakage in logs | `mask_uri()` applied to all connection strings before logging; 422 errors never log the request body |
| SSRF via SQL connection | `_validate_host()` checks against `SIPHON_ALLOWED_HOSTS` (CIDR + hostname allowlist) |
| Path traversal in S3 writes | `_validate_path()` rejects `..` and paths outside `SIPHON_ALLOWED_S3_PREFIX` |
| SFTP host spoofing | `RejectPolicy` — unknown host keys are rejected; `AutoAddPolicy` is never used |
| SFTP large file bomb | Files over `SIPHON_MAX_FILE_SIZE_MB` are skipped |
| Container privilege escalation | Non-root UID 1000 in Docker image |
| Oversized request bodies | 413 returned for bodies over `SIPHON_MAX_REQUEST_SIZE_MB` |
| Container vulnerabilities | Trivy scan on every CI build (CRITICAL/HIGH, ignore-unfixed) |

---

## Kubernetes

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: siphon
spec:
  replicas: 1
  strategy:
    type: Recreate        # one job at a time; avoid split-brain on queue
  template:
    spec:
      terminationGracePeriodSeconds: 600   # allow long-running jobs to drain
      containers:
        - name: siphon
          image: ghcr.io/lucasnovack/siphon:latest
          ports:
            - containerPort: 8000
          env:
            - name: SIPHON_API_KEY
              valueFrom:
                secretKeyRef:
                  name: siphon-secrets
                  key: api-key
            - name: SIPHON_ALLOWED_HOSTS
              value: "10.0.0.0/8,172.16.0.0/12"
            - name: SIPHON_ALLOWED_S3_PREFIX
              value: "bronze/"
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 15
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8000
            periodSeconds: 10
          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "2Gi"
              cpu: "2"
          securityContext:
            runAsNonRoot: true
            runAsUser: 1000
            readOnlyRootFilesystem: true
          lifecycle:
            preStop:
              exec:
                command: ["/bin/sh", "-c", "sleep 5"]
```

```yaml
# k8s/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: siphon
spec:
  selector:
    app: siphon
  ports:
    - port: 8000
      targetPort: 8000
```

---

## Development

**Prerequisites:** Python 3.12+, [uv](https://github.com/astral-sh/uv), Docker

```bash
# Install dependencies
uv sync

# Run linting
uv run ruff check .
uv run ruff format --check .

# Run unit tests
uv run pytest --ignore=tests/test_integration_sql.py \
              --ignore=tests/test_integration_s3.py \
              --ignore=tests/test_integration_sftp.py -v

# Start local services (MySQL + MinIO + SFTP)
docker compose up -d mysql minio sftp

# Run integration tests
SIPHON_ALLOWED_S3_PREFIX=bronze/ SIPHON_S3_SCHEME=http \
  uv run pytest tests/test_integration_sql.py \
               tests/test_integration_s3.py \
               tests/test_integration_sftp.py \
               -m integration -v

# Build Docker image
docker build -t siphon:dev .

# Run the full stack locally
docker compose up
```

**Project layout:**

```
src/siphon/
├── main.py                    # FastAPI app, routes, middleware
├── queue.py                   # Asyncio job queue + ThreadPoolExecutor
├── worker.py                  # Extraction loop: source → parser → destination
├── models.py                  # Pydantic models, mask_uri
├── variables.py               # @TODAY, @LAST_MONTH, etc.
└── plugins/
    ├── sources/
    │   ├── base.py            # Source ABC
    │   ├── __init__.py        # Registry + autodiscovery
    │   └── sql.py             # SQLSource (ConnectorX + oracledb)
    │   └── sftp.py            # SFTPSource (Paramiko)
    ├── destinations/
    │   ├── base.py            # Destination ABC
    │   ├── __init__.py        # Registry + autodiscovery
    │   └── s3_parquet.py      # S3ParquetDestination (PyArrow)
    └── parsers/
        ├── base.py            # Parser ABC
        ├── __init__.py        # Registry + autodiscovery
        └── example_parser.py  # Stub: bytes → pa.Table with raw column
```

---

## License

MIT
