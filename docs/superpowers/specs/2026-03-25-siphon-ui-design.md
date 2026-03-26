# Siphon UI — Design Spec
**Date:** 2026-03-25
**Status:** Approved

---

## 1. Overview

Add a management UI to Siphon — similar to Airbyte — so data engineers can manage connections, build extraction pipelines, trigger and monitor jobs, and configure cron schedules, all without touching the API directly.

Simultaneously fix three critical bugs in the existing backend and add operational improvements (schema evolution tracking, data quality checks, Prometheus metrics) identified during the architecture review.

---

## 2. Scope

### In scope
- React frontend served by FastAPI (same container)
- PostgreSQL persistence: users, connections, pipelines, schedules, job_runs
- JWT auth (admin + operator roles) alongside existing API key auth (Airflow compat)
- Connection management with Fernet encryption and test-connection
- Pipeline management: full_refresh and incremental extraction modes
- Cron scheduling with `pg_advisory_lock` (no dual-pod race)
- Data preview (SELECT LIMIT 100)
- Live log viewer (polling)
- Schema evolution tracking (Arrow schema hash, alert on divergence)
- Data quality floor (min_rows_expected, % drop alert)
- Prometheus metrics (basic)
- Backfill via date_from/date_to on manual trigger
- Hotfixes: `_jobs` OOM, SFTP stranded files, `/extract` production guard

### Out of scope (explicit)
- CDC / log-based change capture
- Multi-source joins at extraction time
- Connection pooling (v2)
- Hard-delete tracking
- AI/schema-aware SQL completion

---

## 3. Architecture

### Deployment

Single FastAPI container. React build (`frontend/dist/`) is served by `StaticFiles`. PostgreSQL is the single persistence layer.

```
┌──────────────────────────────────────────────────────────┐
│                    Container: siphon                     │
│                                                          │
│  FastAPI                                                 │
│  ├── GET  /*              → StaticFiles (React build)    │
│  ├── /api/v1/auth         → login, refresh, logout, me  │
│  ├── /api/v1/connections  → CRUD + test                  │
│  ├── /api/v1/pipelines    → CRUD + trigger + schedule    │
│  ├── /api/v1/runs         → history + logs               │
│  ├── /api/v1/preview      → SELECT LIMIT 100             │
│  ├── /api/v1/users        → admin only                   │
│  └── /health/*            → probes (no auth, no prefix)  │
│                                                          │
│  APScheduler (DB-backed jobstore, pg_advisory_lock)      │
│  JobQueue   (existing, unchanged)                        │
└──────────────────────────────────────────────────────────┘
                        │
                   PostgreSQL
```

### Migration path

The frontend speaks only to `/api/v1/...`. Zero coupling to FastAPI internals. To extract the frontend to its own container: set `VITE_API_BASE_URL=https://api.siphon.internal` and serve `frontend/dist/` with Nginx. No code changes required.

### Backward compatibility

Existing endpoints (`POST /jobs`, `GET /jobs/:id`, `GET /jobs/:id/logs`, `POST /extract`) are preserved without any change. The Airflow operator continues to work unchanged.

---

## 4. Database Schema

### Tables

```sql
users
  id           UUID PK DEFAULT gen_random_uuid()
  email        TEXT UNIQUE NOT NULL
  name         TEXT
  password     TEXT NOT NULL                      -- bcrypt hash
  role         TEXT NOT NULL DEFAULT 'operator'
               CHECK (role IN ('admin', 'operator'))
  is_active    BOOL NOT NULL DEFAULT true
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()

connections
  id           UUID PK DEFAULT gen_random_uuid()
  name         TEXT UNIQUE NOT NULL
  type         TEXT NOT NULL
               CHECK (type IN ('sql', 'sftp', 's3_parquet'))
  encrypted    TEXT NOT NULL                      -- Fernet(JSON config)
  key_version  INTEGER NOT NULL DEFAULT 1         -- for key rotation
  created_by   UUID NOT NULL REFERENCES users(id)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()

pipelines
  id              UUID PK DEFAULT gen_random_uuid()
  name            TEXT UNIQUE NOT NULL
  source_conn     UUID NOT NULL REFERENCES connections(id)
  source_query    TEXT NOT NULL
  dest_conn       UUID NOT NULL REFERENCES connections(id)
  dest_prefix     TEXT NOT NULL
  extraction_mode TEXT NOT NULL DEFAULT 'full_refresh'
                  CHECK (extraction_mode IN ('full_refresh', 'incremental'))
  incremental_key TEXT                            -- e.g. "updated_at", "id"
  last_watermark  TEXT                            -- ISO-8601 UTC, e.g. "2026-03-25T02:00:00+00:00"
  last_schema_hash TEXT                           -- SHA-256 of Arrow schema JSON
  options         JSONB                           -- partition_on, chunk_size, fail_fast, etc.
  min_rows_expected   INT                         -- optional: fail if rows_read < this
  max_rows_drop_pct   INT                         -- optional: fail if drop% vs last run > this
  is_active       BOOL NOT NULL DEFAULT true
  created_by      UUID NOT NULL REFERENCES users(id)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()

schedules
  id            UUID PK DEFAULT gen_random_uuid()
  pipeline_id   UUID NOT NULL UNIQUE REFERENCES pipelines(id)
  cron_expr     TEXT NOT NULL CHECK (cron_expr <> '')
  is_active     BOOL NOT NULL DEFAULT true
  last_run_at   TIMESTAMPTZ                       -- cache, APScheduler is authoritative
  next_run_at   TIMESTAMPTZ                       -- cache, APScheduler is authoritative
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()

job_runs
  id            UUID PK                           -- same as in-memory job_id
  pipeline_id   UUID REFERENCES pipelines(id)     -- null = ad-hoc
  schedule_id   UUID REFERENCES schedules(id)     -- null = manual/api
  triggered_by  TEXT NOT NULL
                CHECK (triggered_by IN ('schedule', 'manual', 'api'))
  status        TEXT NOT NULL
                CHECK (status IN ('queued','running','success','failed','partial_success'))
  rows_read     BIGINT
  rows_written  BIGINT
  rows_failed   BIGINT
  error         TEXT
  schema_hash     TEXT                            -- Arrow schema hash at time of run
  schema_changed  BOOL NOT NULL DEFAULT false     -- true if schema diverged from previous run
  started_at    TIMESTAMPTZ
  finished_at   TIMESTAMPTZ
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  CONSTRAINT ck_timing CHECK (started_at IS NULL OR finished_at IS NULL
                               OR started_at <= finished_at)

refresh_tokens
  id          UUID PK DEFAULT gen_random_uuid()
  user_id     UUID NOT NULL REFERENCES users(id)
  token_hash  TEXT UNIQUE NOT NULL               -- SHA-256 of raw token
  revoked     BOOL NOT NULL DEFAULT false
  user_agent  TEXT
  ip_address  TEXT
  expires_at  TIMESTAMPTZ NOT NULL
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
```

### Indexes

```sql
CREATE INDEX idx_job_runs_pipeline_created ON job_runs (pipeline_id, created_at DESC);
CREATE INDEX idx_job_runs_status_created   ON job_runs (status, created_at DESC);
CREATE INDEX idx_schedules_next_run        ON schedules (next_run_at) WHERE is_active = true;
-- FK indexes (PostgreSQL does not auto-create these)
-- idx_job_runs_pipeline_id omitted: covered by compound idx_job_runs_pipeline_created
CREATE INDEX idx_job_runs_schedule_id  ON job_runs (schedule_id);
CREATE INDEX idx_pipelines_source_conn ON pipelines (source_conn);
CREATE INDEX idx_pipelines_dest_conn   ON pipelines (dest_conn);
CREATE INDEX idx_pipelines_created_by  ON pipelines (created_by);
CREATE INDEX idx_connections_created_by ON connections (created_by);
```

---

## 5. Credential Storage

Connections are stored encrypted with [Fernet](https://cryptography.io/en/latest/fernet/) symmetric encryption.

- `SIPHON_ENCRYPTION_KEY` — base64-urlsafe 32-byte key, required at startup
- The full connection config JSON is encrypted as a single blob in `connections.encrypted`
- `connections.key_version` enables staged key rotation: decrypt with old key, re-encrypt with new key, increment version — without a flag day
- The decrypted config is never logged, never serialized to API responses, never stored in `job_runs`

---

## 6. Auth

### Dual-auth middleware

The existing `api_key_middleware` is replaced by a FastAPI dependency `get_current_principal` used on all `/api/v1/` routers:

1. If `Authorization: Bearer <token>` matches `SIPHON_API_KEY` → authenticated as service account (full access, no role check)
2. If `Authorization: Bearer <token>` looks like a JWT (three dot-separated segments) → validate JWT, extract user + role
3. Otherwise → 401

Health probes (`/health/*`) and login (`/api/v1/auth/login`, `/api/v1/auth/refresh`) are excluded.

### JWT flow

```
POST /api/v1/auth/login     { email, password }
  → 200: { access_token, expires_in: 900 }
       + Set-Cookie: refresh_token=<value>; HttpOnly; Secure;
                    SameSite=Strict; Path=/api/v1/auth/refresh

POST /api/v1/auth/refresh   (cookie sent automatically)
  → 200: { access_token, expires_in: 900 }
       + Set-Cookie: refresh_token=<new_value>; ...   ← rotation
       + old refresh token marked revoked in DB

POST /api/v1/auth/logout
  → 200 + Set-Cookie: refresh_token=; Max-Age=0
       + DB: revoked=true for current token

GET  /api/v1/auth/me
  → 200: { id, email, name, role }
```

- `access_token`: JWT, 15-minute TTL, stored in **React memory only** (never localStorage)
- `refresh_token`: 7-day TTL, `httpOnly` cookie, `Path=/api/v1/auth/refresh` (never sent on other requests)
- **Token rotation**: every `/auth/refresh` issues a new token and revokes the old one. Reuse of a revoked token immediately revokes all sessions for that user
- **Rate limiting**: `POST /api/v1/auth/login` — max 10 attempts/minute per IP (slowapi)
- **Concurrent refresh race** (frontend): Axios interceptor uses a singleton promise mutex — all in-flight requests queue behind the first refresh call

### Roles

| Action | admin | operator |
|---|---|---|
| View connections, pipelines, runs | ✓ | ✓ |
| Create / edit / delete connections | ✓ | ✗ |
| Create / edit / delete pipelines | ✓ | ✗ |
| Trigger pipeline manually | ✓ | ✓ |
| Manage schedules | ✓ | ✗ |
| Manage users | ✓ | ✗ |

---

## 7. API Surface

### List envelope (all paginated lists)
```json
{ "items": [...], "total": 150, "page": 1, "limit": 50 }
```

### Endpoints

```
# Auth
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
GET    /api/v1/auth/me

# Connections
GET    /api/v1/connections               ?page&limit
POST   /api/v1/connections
GET    /api/v1/connections/:id
PUT    /api/v1/connections/:id
DELETE /api/v1/connections/:id
POST   /api/v1/connections/test          { type, ...config } → { ok, latency_ms, error? }
POST   /api/v1/connections/:id/test      → { ok, latency_ms, error? }
GET    /api/v1/connections/types         → [{ type, fields: [{name,type,required}] }]

# Pipelines
GET    /api/v1/pipelines                 ?page&limit
POST   /api/v1/pipelines
GET    /api/v1/pipelines/:id
PUT    /api/v1/pipelines/:id
DELETE /api/v1/pipelines/:id
POST   /api/v1/pipelines/:id/trigger     { date_from?, date_to? } → { job_id }
GET    /api/v1/pipelines/:id/runs        ?page&limit
PUT    /api/v1/pipelines/:id/schedule    { cron_expr, is_active }
DELETE /api/v1/pipelines/:id/schedule

# Preview
POST   /api/v1/preview                   { connection_id, query } → { columns, rows, row_count }

# Runs
GET    /api/v1/runs                      ?page&limit&status&pipeline_id
GET    /api/v1/runs/:id
GET    /api/v1/runs/:id/logs             ?since=N → { logs, next_offset }
POST   /api/v1/runs/:id/cancel           → { ok }

# Users (admin only)
GET    /api/v1/users
POST   /api/v1/users
PUT    /api/v1/users/:id
DELETE /api/v1/users/:id                 soft delete (is_active=false)

# Metrics
GET    /metrics                          Prometheus text format (was returning {})

# Health (no auth, no /api/v1 prefix — backward compat)
GET    /health/live
GET    /health/ready
GET    /health

# Legacy (backward compat for Airflow operator — unchanged)
POST   /jobs
POST   /extract                          (guarded by SIPHON_ENABLE_SYNC_EXTRACT=true)
GET    /jobs/:id
GET    /jobs/:id/logs
```

---

## 8. Extraction Modes

### full_refresh
Current behavior. Extracts all rows matching the query. `@TODAY`, `@LAST_MONTH`, etc. are resolved at run time.

### incremental
Watermark-based incremental extraction.

**Configuration:** `incremental_key` (e.g. `updated_at`) required. `last_watermark` starts as `null`.

**Behavior:**
- First run: extracts all rows, sets `last_watermark = MAX(incremental_key)` as ISO-8601 UTC string after successful write
- Subsequent runs: injects `AND <incremental_key> > '<last_watermark>'::timestamptz` (type-aware cast per dialect) into the query before execution
- Watermark is updated **only after** successful DB write of `job_runs` row — avoiding the 2PC race on pod kill
- `date_from` / `date_to` on manual trigger override the watermark for that run only (backfill)

**Edge cases documented (not silently ignored):**
- Source table has no `incremental_key` column: job fails immediately with clear error
- `last_watermark` is null and no rows returned: watermark stays null, not set to "now"
- DST: watermark is always stored and compared as UTC; source DATETIME (no TZ) columns are cast explicitly at query time

---

## 9. Schema Evolution

After each successful run, the worker computes a SHA-256 hash of the Arrow schema JSON and stores it in both `job_runs.schema_hash` and `pipelines.last_schema_hash`.

On the **next** run, if the incoming schema hash diverges from `pipelines.last_schema_hash`:
- Job still completes (write is not blocked)
- `job_runs` is marked with `schema_changed=true`
- A warning is appended to job logs: `[SCHEMA CHANGE] Column 'foo' added (int64). Previous runs do not have this column.`
- The UI shows a yellow warning badge on the pipeline and the run detail

This gives the engineer visibility without blocking extraction. The engineer decides whether to do a full refresh and re-partition historical data.

---

## 10. Data Quality

Per-pipeline optional configuration stored as direct columns on `pipelines`:

- `min_rows_expected INT`: if `rows_read < min_rows_expected`, job is marked `failed` (not success)
- `max_rows_drop_pct INT`: if `rows_read < previous_successful_run.rows_read * (1 - pct/100)`, job is marked `failed`

Both checks run after extraction, before writing. If either fails, no data is written.

---

## 11. APScheduler — Distributed Safety

APScheduler uses the `postgresql` jobstore (backed by `job_runs` table metadata) so schedule state survives pod restarts. Missed jobs are re-fired on startup within `misfire_grace_time=3600s`.

Before firing any scheduled job, a `pg_try_advisory_lock(schedule_id)` is acquired:

```python
async with db.begin():
    locked = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:id)"),
        {"id": hash(schedule_id) % (2**31)}
    )
    if not locked:
        return  # another pod is already running this job
    # fire job
```

The lock is transaction-scoped — released automatically when the DB transaction ends. This makes rolling deploys safe even if `Recreate` strategy is ever changed.

---

## 12. Hotfixes (Phase 7)

### `_jobs` dict OOM
Add TTL eviction to `JobQueue`: completed/failed jobs older than `SIPHON_JOB_TTL_SECONDS` (default: `3600`) are removed from `_jobs`. A background task runs every 5 minutes.

### SFTP stranded files
When `fail_fast=False` and a file parse fails after `_move_to_processing()`:
- Current (bug): file stays in `processing_folder` forever, unreachable
- Fix: on parse error, `_move_back_to_origin()` before appending to `failed_files`

### `/extract` production guard
`POST /extract` only available when `SIPHON_ENABLE_SYNC_EXTRACT=true` (default: `false`). Returns `404` otherwise. Backward compat preserved for local dev.

---

## 13. Observability

### Prometheus metrics (`GET /metrics`)

| Metric | Type | Labels |
|---|---|---|
| `siphon_jobs_total` | Counter | `status`, `source_type` |
| `siphon_job_duration_seconds` | Histogram | `source_type` |
| `siphon_rows_extracted_total` | Counter | `source_type` |
| `siphon_queue_depth` | Gauge | — |
| `siphon_schema_changes_total` | Counter | `pipeline_id` |

### Structured logging
Job log entries gain structured metadata: `{"ts": "...", "job_id": "...", "pipeline_id": "...", "level": "INFO", "msg": "..."}`. Searchable in Loki/Datadog.

---

## 14. Frontend

### Stack
- React 18 + Vite
- React Router v6
- TanStack Query (server state, polling)
- shadcn/ui + Tailwind
- react-hook-form + zod (form validation with discriminated unions per connection type)
- CodeMirror 6 (SQL editor, dialect wired to source connection type)
- cronstrue (cron → human-readable)
- date-fns (timestamp formatting)

### Page structure

```
/login
/                         Dashboard: recent runs, stats, health
/connections
  /connections/new
  /connections/:id
/pipelines
  /pipelines/new          4-step wizard
  /pipelines/:id          detail + run history + trigger
  /pipelines/:id/edit
/runs                     global run history
  /runs/:id               run detail + live logs
/settings/users
/settings/api-keys
/settings/system
```

### Pipeline Wizard (4 steps)

1. **Source** — select source connection (ConnectionSelect with inline "create new" escape)
2. **Query + Preview** — CodeMirror SQL editor, "Preview" button → table of 100 rows, incremental toggle + watermark column field
3. **Destination + Prefix** — select dest connection, S3 prefix with variable hints, data quality config (optional)
4. **Schedule + Review** — CronInput with cronstrue preview (optional), read-only summary before save

### Key components

| Component | Behavior |
|---|---|
| `ConnectionForm` | Dynamic fields by type (zod discriminated union). Preserves per-type values on type switch. "Test Connection" result cleared on any field change. |
| `LogViewer` | Polls `/runs/:id/logs?since=N` every 2s while running. Stops on terminal status + one final poll. DOM capped at 2000 lines. |
| `CronInput` | Text input + live cronstrue preview beneath it. |
| `ConnectionSelect` | Dropdown filtered by type, with inline "create new" escape hatch. |
| `ConfirmDialog` | Wraps shadcn `AlertDialog`. Used for all destructive actions. |
| `EmptyState` | Every list page has a CTA when empty. |
| `PageHeader` | Consistent title + breadcrumb + primary action across all pages. |
| `ApiErrorMessage` | Extracts `detail` from API error responses consistently. |
| `SchemaDriftBadge` | Yellow warning on pipeline card and run detail when schema changed. |

### State management

- TanStack Query for all server state
- `queryKeys.ts` centralizes all query keys — defined before any component
- `refetchIntervalInBackground: false` on all polling (TanStack default — confirm not overridden)
- CodeMirror lazy-loaded: `React.lazy(() => import('./components/QueryEditor'))`
- Axios interceptor with singleton refresh mutex for concurrent 401s

---

## 15. CI changes

### New stages

```yaml
lint-frontend:
  - pnpm install --frozen-lockfile
  - pnpm run lint
  - pnpm run type-check

test-frontend:
  - pnpm run test    # Vitest unit tests

docker:              # existing, gains:
  - Playwright E2E smoke: login → create connection → create pipeline → trigger → run completes

test-integration:    # existing, gains:
  - test_integration_ui.py: full API flow (user → connection → pipeline → trigger → job_runs in DB)
```

### Dockerfile (new frontend stage)

```dockerfile
FROM node:22-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN npm install -g pnpm && pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm run build

# ... existing builder and runtime stages ...

# In runtime stage:
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist
```

Node.js does not appear in the runtime image. Final image stays under 500MB.

---

## 16. New Roadmap Phases

| Phase | Title | Content |
|---|---|---|
| **7** | Hotfixes | `_jobs` OOM, SFTP stranded files, `/extract` guard |
| **8** | PostgreSQL + Auth | Alembic setup, all 6 tables, JWT auth, dual-auth middleware, user CRUD |
| **9** | Connections + Pipelines API | CRUD, Fernet encryption, test-connection, incremental mode, watermark, schema evolution, APScheduler + pg_advisory_lock, Prometheus metrics |
| **10** | Frontend | React + Vite setup, all pages, wizard, log viewer, schema drift badges |
| **11** | Kubernetes | (was Phase 7) |
| **12** | SiphonOperator (Airflow) | (was Phase 8) |
| **13** | Migration | (was Phase 9) |
