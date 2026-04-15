# Siphon

Self-hosted Bronze-layer pipeline platform. Replaces Apache Spark for `SELECT → Parquet → S3` jobs. 200 MB container, <1s startup vs 2 GB / 30–60s for Spark.

Stack: FastAPI + Celery + Redis + PostgreSQL + APScheduler. React 18 SPA served by FastAPI. All phases (1–17) complete, 374+ tests passing.

---

## Directory structure

```
src/siphon/
├── main.py               FastAPI app, route registration, lifespan
├── models.py             Pydantic schemas (ExtractRequest, JobStatus…)
├── orm.py                SQLAlchemy ORM models
├── db.py                 Async session factory + FastAPI dependency
├── queue.py              Thin Celery wrapper (enqueue → apply_async)
├── worker.py             run_job(): extract → DQ → write → persist
├── variables.py          @TODAY, @LAST_MONTH, @NEXT_MONTH substitution
├── crypto.py             Fernet encrypt/decrypt for credentials
├── scheduler.py          APScheduler + PostgreSQL jobstore + advisory lock
├── metrics.py            Prometheus counters/histograms
├── celery_app.py         Celery config, high/normal/low queues
├── tasks.py              @celery_app.task run_pipeline_task(job_dict)
├── auth/                 JWT httpOnly, refresh rotation, reuse detection
├── connections/          CRUD, test, types — credentials encrypted at rest
├── pipelines/            CRUD, schedule upsert, trigger, watermark
├── runs/                 Global history, cursor logs, cancel (revoke)
├── preview/              POST /preview — LIMIT 100 + SSRF guard
├── users/                Admin-only CRUD
└── plugins/
    ├── sources/          SQLSource, SFTPSource, HTTPRestSource
    ├── destinations/     S3ParquetDestination
    └── parsers/          CSV, JSON, Avro → Arrow
```

---

## Request lifecycle (pipeline trigger)

```
POST /pipelines/{id}/trigger
  → decrypt credentials → build Job → INSERT job_run(status=queued)
  → queue.enqueue() → Celery apply_async(queue=priority)

Celery worker (prefork)
  → asyncio.run(run_job()) with fresh SQLAlchemy engine
  → UPDATE job_run(status=running)
  → source.extract_batches() → [DQ buffer] → destination.write()
  → promote (rename staging → final in S3)
  → UPDATE last_watermark on pipeline   ← watermark updated AFTER promote
  → UPDATE job_run(status=success/error)
```

---

## Data models

```
users ──── 1:N ──── refresh_tokens
connections ──── 1:N ──── pipelines (source_connection_id + dest_connection_id)
pipelines ──── 1:1 ──── schedules
pipelines ──── 1:N ──── job_runs
```

Migrations: 001–011. Soft delete (`deleted_at`) on connections, pipelines, schedules, users.

---

## Security layers

| Layer | What |
|---|---|
| Size limit | Rejects bodies > SIPHON_MAX_REQUEST_SIZE_MB |
| Auth | JWT or API key → `get_current_principal()` |
| SSRF | SIPHON_ALLOWED_HOSTS allowlist on SQLSource |
| S3 prefix | SIPHON_ALLOWED_S3_PREFIX on S3ParquetDestination |
| Path traversal | Rejects `..` in destination paths |
| SFTP | RejectPolicy — never AutoAddPolicy |

---

## Plugin registry

New sources/destinations/parsers: create file in `plugins/{sources,destinations,parsers}/`, use `@register("type")`. Autodiscovery imports at startup — no manual wiring.

---

@.claude/rules/architecture.md
@.claude/rules/testing.md
@.claude/rules/git.md
