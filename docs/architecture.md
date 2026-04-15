# Architecture

Siphon is a self-hosted data pipeline service that handles the **Bronze layer** of a medallion data architecture. It replaces Apache Spark for jobs that follow a simple pattern: extract rows from a SQL database (or files from SFTP), write them as Parquet files to object storage.

Spark adds 30–60 seconds of startup overhead, 2 GB+ of RAM, and a 2 GB container image for jobs that are essentially `SELECT … FROM table` plus a file write. Siphon does the same job in a 200 MB container that starts in under a second.

---

## System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        siphon (API container)                       │
│                                                                     │
│   ┌────────────┐   ┌────────────────────┐   ┌───────────────────┐  │
│   │  React UI  │   │   FastAPI (HTTP)    │   │  APScheduler      │  │
│   │  (SPA)     │◄──┤   /api/v1/*        │   │  (cron jobs)      │  │
│   └────────────┘   └────────┬───────────┘   └────────┬──────────┘  │
│                             │                         │             │
│                   ┌─────────▼─────────────────────────▼──────────┐  │
│                   │              Job Queue                        │  │
│                   │    Celery (Redis broker)                      │  │
│                   │    high / normal / low priority queues        │  │
│                   └─────────────────────┬─────────────────────────┘  │
└─────────────────────────────────────────┼──────────────────────────┘
                                          │
                    ┌─────────────────────▼──────────────────────┐
                    │         siphon-worker (Celery container)    │
                    │            prefork, separate container      │
                    │                                            │
                    │  ┌──────────────────────────────────────┐  │
                    │  │           Celery worker               │  │
                    │  └──────┬──────────────────┬────────────┘  │
                    │         │                  │               │
                    │  ┌──────▼──────┐  ┌────────▼────────────┐  │
                    │  │   Source    │  │   Destination        │  │
                    │  │  (plugin)   │  │   (plugin)           │  │
                    │  └──────┬──────┘  └────────┬────────────┘  │
                    └─────────┼──────────────────┼───────────────┘
                              │                  │
               ┌──────────────▼──┐  ┌────────────▼──────────┐  ┌──────────┐
               │  Source DB      │  │  Object store          │  │  Redis   │
               │  (MySQL /       │  │  (MinIO / S3)          │  │ (broker) │
               │   PG / etc)     │  └────────────────────────┘  └──────────┘
               └─────────────────┘
```

**PostgreSQL** (separate container) stores users, connections, pipelines, schedules, and job run history.

**Apache Airflow** (external) can trigger jobs via `POST /jobs` using the legacy API key auth — it treats Siphon as a remote executor.

---

## Request lifecycle

A pipeline run follows this path from trigger to persisted result:

```
1. Trigger
   HTTP POST /api/v1/pipelines/{id}/trigger
   OR APScheduler fires _fire_pipeline()
   OR Airflow calls POST /jobs

2. Route handler
   Authenticates → validates pipeline → decrypts credentials →
   builds ExtractRequest + Job object →
   INSERTs job_run(status="queued") in DB →
   calls queue.enqueue(job, priority)

3. Queue (queue.py — thin Celery wrapper)
   Calls celery_app.apply_async(queue=priority) →
   Celery dispatches to the siphon-worker container

4. Celery worker (prefork, separate container)
   Picks up task → calls asyncio.run(run_pipeline_task()) →
   Creates fresh SQLAlchemy engine (no event loop reuse) →
   UPDATEs job_run(status="running") →
   Calls source.extract_batches() →
   Optionally buffers for DQ checks →
   Calls destination.write() for each batch →
   Promotes staging to final in S3 →
   UPDATEs last_watermark on pipeline (after promote) →
   UPDATEs job_run(status="success" or "error")

5. Response
   Frontend polls GET /api/v1/runs/{job_id} until status ≠ queued/running
```

---

## Directory structure

```
siphon/
├── src/siphon/
│   ├── main.py               FastAPI app, route registration, lifespan
│   ├── models.py             Pydantic schemas (ExtractRequest, JobStatus…)
│   ├── orm.py                SQLAlchemy ORM models (tables)
│   ├── db.py                 Async session factory + FastAPI dependency
│   ├── queue.py              Thin Celery wrapper — enqueue() → apply_async(queue=priority)
│   ├── celery_app.py         Celery configuration, high/normal/low queues
│   ├── tasks.py              @celery_app.task run_pipeline_task(job_dict)
│   ├── worker.py             Extraction logic, DQ checks, persistence
│   ├── variables.py          Date variable substitution (@TODAY, @LAST_MONTH…)
│   ├── crypto.py             Fernet encrypt/decrypt for connection credentials
│   ├── scheduler.py          APScheduler integration
│   ├── metrics.py            Prometheus counters and histograms
│   ├── auth/
│   │   ├── deps.py           get_current_principal() FastAPI dependency
│   │   ├── jwt_utils.py      JWT creation/verification, bcrypt, refresh tokens
│   │   └── router.py         /api/v1/auth endpoints
│   ├── connections/router.py /api/v1/connections endpoints
│   ├── pipelines/
│   │   ├── router.py         /api/v1/pipelines endpoints + trigger
│   │   └── watermark.py      inject_watermark() for incremental extraction
│   ├── runs/router.py        /api/v1/runs endpoints
│   ├── preview/router.py     /api/v1/preview endpoint
│   ├── users/router.py       /api/v1/users endpoints
│   └── plugins/
│       ├── sources/          SQLSource, SFTPSource + registry
│       ├── destinations/     S3ParquetDestination + registry
│       └── parsers/          Binary → Arrow parsers + registry
├── frontend/src/             React SPA
├── alembic/                  Database migration scripts
├── tests/                    Test suite
├── testenv/                  Local MySQL + MinIO for manual testing
├── Dockerfile                Multi-stage image build
└── docker-compose.yml        Local dev stack
```

---

## Key design decisions

### Celery + Redis queue (horizontal scale)

Jobs are dispatched via Celery with three priority queues — `high`, `normal`, and `low` — backed by Redis as the broker. Workers run in separate `siphon-worker` containers using Celery's prefork execution model; each task calls `asyncio.run()` with a fresh SQLAlchemy engine so there is no event loop reuse across tasks.

Job state is persisted to PostgreSQL `job_runs` throughout the lifecycle (queued → running → success/error), so the API pod has no in-memory job state and can restart freely without losing visibility into running or completed jobs. Graceful drain is implemented via `task_acks_late=True` and `worker_prefetch_multiplier=1`, which ensures a task is only acknowledged after it completes and that each worker holds at most one task at a time.

Trade-off: requires Redis as an additional service (four containers: `siphon` API, `siphon-worker`, `postgres`, `redis`), but enables multiple worker pods and survives API pod restarts without data loss.

### ConnectorX for SQL extraction

[ConnectorX](https://github.com/sfu-db/connector-x) is a Rust library that reads SQL databases directly into Apache Arrow format without going through Python objects. This makes it significantly faster and more memory-efficient than pandas/SQLAlchemy for large result sets.

Oracle is an exception: ConnectorX's Oracle support is limited, so Siphon uses `oracledb` with Python-level cursor streaming.

### PyArrow for Parquet writes

PyArrow's `pq.write_to_dataset()` writes partitioned Parquet directly to S3/MinIO using its built-in `S3FileSystem`. No s3fs, no boto3, no intermediate temp files.

### Fernet encryption for credentials

Connection credentials are encrypted with [Fernet](https://cryptography.io/en/latest/fernet/) before being stored. The raw config is never logged, never returned by the API, and only decrypted at the moment a job runs. See [auth.md](auth.md) for key management.

### Plugin registry pattern

Sources, destinations, and parsers are registered with a `@register("type")` decorator. New types are added by creating a new file in the right directory — the autodiscovery imports all modules at startup, no manual wiring needed. See [contributing.md](contributing.md) for the step-by-step.

### PostgreSQL is optional

If `DATABASE_URL` is not set, Siphon starts without a database. Auth, connections, pipelines, and the UI are all disabled, but the legacy `/jobs` extraction endpoint still works. This makes it possible to use Siphon as a pure extraction engine called directly by Airflow, without setting up a database.

---

## Data models

The six database tables and how they relate:

```
users ──────────────────────────────────────── 1 user : N refresh_tokens
                                               (JWT refresh rotation)

connections ────────────────────────────────── credentials (encrypted)
     │
     ├── source_connection_id ──────────────── pipelines
     └── dest_connection_id   ──────────────── (many pipelines per connection)
                │
                ├── pipeline_id ────────────── schedules   (1:1)
                └── pipeline_id ────────────── job_runs    (1:N)
                                               (history of every execution)
```

See [orm.py](../src/siphon/orm.py) for column-level detail.

---

## Security layers

Every incoming request passes through multiple validation gates in order:

| Layer | Where | What it does |
|---|---|---|
| Size limit | Middleware | Rejects bodies > `SIPHON_MAX_REQUEST_SIZE_MB` (default 1 MB) |
| Authentication | `get_current_principal()` | Validates JWT or API key; fetches user from DB |
| Authorization | Route handlers | `principal.require_admin()` for write operations |
| Model validation | Pydantic | Type checks, constraints; 422 with sanitized errors |
| Host allowlist | SQLSource | Rejects connections to hosts outside `SIPHON_ALLOWED_HOSTS` |
| S3 path prefix | S3ParquetDestination | Rejects paths outside `SIPHON_ALLOWED_S3_PREFIX` |
| Path traversal | S3ParquetDestination | Rejects `..` in destination paths |
| Host key verification | SFTPSource | RejectPolicy; refuses unknown SSH hosts |

---

## Observability

| Signal | Where |
|---|---|
| HTTP access logs | uvicorn stdout (structlog format) |
| Extraction logs | Per-job in-memory list; exposed via `GET /api/v1/runs/{id}/logs` |
| Prometheus metrics | `GET /metrics` (jobs_total, rows_extracted_total, job_duration_seconds, queue_depth, schema_changes_total) |
| Health | `GET /health/live` (liveness), `GET /health/ready` (readiness), `GET /health` (debug) |

---

## Glossary

| Term | Meaning |
|---|---|
| **Connection** | A saved set of encrypted credentials for a database, SFTP server, or object store |
| **Pipeline** | A definition: source connection + query + destination + extraction mode + DQ rules |
| **Job / Run** | A single execution of a pipeline (or ad-hoc extraction). Tracked in `job_runs` table |
| **Schedule** | A cron expression attached to a pipeline; APScheduler fires the run |
| **Watermark** | The ISO-8601 timestamp of the last successful incremental run; stored on the pipeline |
| **Schema hash** | SHA-256 of Arrow field names + types; changes are flagged as schema drift |
| **DQ** | Data Quality: min_rows_expected and max_rows_drop_pct guards before writing |
| **Bronze layer** | Raw, unmodified data; first stage in the medallion architecture |
