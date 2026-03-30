# API Reference

All endpoints are under `http://<host>:8000`. Every endpoint except `POST /api/v1/auth/login` and the health checks requires authentication.

**Authentication header:**
```
Authorization: Bearer <access_token>
```

---

## Auth

### `POST /api/v1/auth/login`
Log in. Returns an access token and sets a refresh cookie.

**Rate limit:** 10/minute per IP.

```json
// Request
{ "email": "admin@example.com", "password": "changeme123" }

// Response 200
{ "access_token": "eyJ...", "expires_in": 900 }

// Response 401
{ "detail": "Invalid credentials" }
```

---

### `POST /api/v1/auth/refresh`
Rotate the refresh token. Reads the `refresh_token` httpOnly cookie; returns a new access token and sets a new cookie.

```json
// Response 200
{ "access_token": "eyJ...", "expires_in": 900 }

// Response 401 — cookie missing or token expired/revoked
{ "detail": "Invalid or expired refresh token" }
```

---

### `POST /api/v1/auth/logout`
Revoke the current session.

```
// Response 204 No Content
```

---

### `GET /api/v1/auth/me`
Return the current user.

```json
// Response 200
{
  "id": "uuid",
  "email": "admin@example.com",
  "name": null,
  "role": "admin",
  "is_active": true,
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

## Connections

### `GET /api/v1/connections`
List all connections. Credentials are never included.

```json
// Response 200
[
  {
    "id": "uuid",
    "name": "prod-mysql",
    "type": "sql",
    "key_version": 1,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  }
]
```

---

### `POST /api/v1/connections` *(admin)*
Create a connection. Config is encrypted before storage.

```json
// Request
{
  "name": "prod-mysql",
  "type": "sql",
  "config": {
    "connection": "mysql://user:pass@host:3306/db"
  }
}

// Response 201
{ "id": "uuid", "name": "prod-mysql", "type": "sql", "key_version": 1, ... }

// Response 409 — name already exists
{ "detail": "Connection 'prod-mysql' already exists" }
```

---

### `GET /api/v1/connections/types/list`
Return supported connection types and their field schemas. Used by the UI to render dynamic forms.

```json
// Response 200
[
  {
    "type": "sql",
    "fields": [
      { "name": "connection", "type": "string", "label": "Connection URL",
        "required": true, "secret": true, "placeholder": "mysql://user:pass@host:3306/db" }
    ]
  },
  {
    "type": "sftp",
    "fields": [
      { "name": "host",     "type": "string",  "label": "Host",     "required": true },
      { "name": "port",     "type": "integer", "label": "Port",     "default": 22 },
      { "name": "username", "type": "string",  "label": "Username", "required": true },
      { "name": "password", "type": "string",  "label": "Password", "required": true, "secret": true }
    ]
  },
  {
    "type": "s3_parquet",
    "fields": [
      { "name": "endpoint",   "type": "string", "label": "Endpoint",   "required": true },
      { "name": "access_key", "type": "string", "label": "Access Key", "required": true, "secret": true },
      { "name": "secret_key", "type": "string", "label": "Secret Key", "required": true, "secret": true }
    ]
  }
]
```

---

### `POST /api/v1/connections/test` *(admin)*
Test a connection **before** saving it. Takes the same body as POST /api/v1/connections.

```json
// Request
{ "type": "sql", "config": { "connection": "mysql://user:pass@host:3306/db" } }

// Response 200 — success
{ "ok": true, "latency_ms": 42 }

// Response 200 — failure (error details in response, not HTTP status)
{ "ok": false, "latency_ms": null, "error": "Connection refused" }
```

---

### `POST /api/v1/connections/{id}/test`
Test a saved connection using its stored (decrypted) credentials.

```json
// Response 200
{ "ok": true, "latency_ms": 38 }
```

---

### `GET /api/v1/connections/{id}`
Get a single connection. No credentials returned.

---

### `PUT /api/v1/connections/{id}` *(admin)*
Update a connection. Only `name` and `config` are updatable. Updating `config` increments `key_version`.

```json
// Request (all fields optional)
{
  "name": "prod-mysql-v2",
  "config": { "connection": "mysql://user:newpass@host:3306/db" }
}
```

---

### `DELETE /api/v1/connections/{id}` *(admin)*
Delete a connection. Pipelines that reference it will fail on the next run.

```
// Response 204 No Content
```

---

## Pipelines

### `GET /api/v1/pipelines`
List all pipelines.

```json
// Response 200
[
  {
    "id": "uuid",
    "name": "orders-daily",
    "source_connection_id": "uuid",
    "dest_connection_id": "uuid",
    "query": "SELECT * FROM orders WHERE updated_at >= @TODAY",
    "destination_path": "bronze/orders/",
    "extraction_mode": "incremental",
    "incremental_key": "updated_at",
    "last_watermark": "2026-03-30T03:00:00+00:00",
    "last_schema_hash": "abc123...",
    "min_rows_expected": null,
    "max_rows_drop_pct": null,
    "is_active": true,
    "schedule": {
      "cron_expr": "0 3 * * *",
      "is_active": true,
      "next_run_at": "2026-03-31T03:00:00Z"
    },
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-03-30T03:00:00Z"
  }
]
```

---

### `POST /api/v1/pipelines` *(admin)*
Create a pipeline.

```json
// Request
{
  "name": "orders-daily",
  "source_connection_id": "uuid",
  "dest_connection_id": "uuid",
  "query": "SELECT * FROM orders WHERE updated_at >= @TODAY",
  "extraction_mode": "incremental",
  "incremental_key": "updated_at",
  "destination_path": "bronze/orders/",
  "min_rows_expected": 100,
  "max_rows_drop_pct": 20
}

// Response 201
{ ...pipeline object... }
```

---

### `GET /api/v1/pipelines/{id}`
Get a single pipeline.

---

### `PUT /api/v1/pipelines/{id}` *(admin)*
Update a pipeline. All fields are optional; only supplied fields are updated.

```json
// Request
{
  "query": "SELECT id, name, total FROM orders WHERE updated_at >= @TODAY",
  "min_rows_expected": 200
}
```

---

### `DELETE /api/v1/pipelines/{id}` *(admin)*
Delete a pipeline and its schedule.

```
// Response 204 No Content
```

---

### `POST /api/v1/pipelines/{id}/trigger` *(admin)*
Manually trigger a pipeline run.

```json
// Request body (optional)
{
  "date_from": "2026-01-01",
  "date_to": "2026-03-31"
}

// Response 202
{
  "job_id": "uuid",
  "status": "queued",
  "run_id": 42
}
```

`job_id` is the UUID used to look up run status. `run_id` is the internal database integer ID.

---

### `PUT /api/v1/pipelines/{id}/schedule` *(admin)*
Add or update the schedule for a pipeline. Creates the APScheduler job immediately.

```json
// Request
{ "cron": "0 3 * * *", "is_active": true }

// Response 200
{
  "pipeline_id": "uuid",
  "cron": "0 3 * * *",
  "is_active": true
}
```

---

### `DELETE /api/v1/pipelines/{id}/schedule` *(admin)*
Remove the schedule from a pipeline. The pipeline definition is preserved.

```
// Response 204 No Content
```

---

### `GET /api/v1/pipelines/{id}/runs`
List recent runs for a specific pipeline.

Query params: `limit` (default 50), `offset` (default 0).

```json
// Response 200
[
  {
    "id": "job-uuid",
    "status": "success",
    "triggered_by": "schedule",
    "rows_read": 1500,
    "rows_written": 1500,
    "schema_changed": false,
    "started_at": "2026-03-30T03:00:01Z",
    "finished_at": "2026-03-30T03:00:08Z",
    "created_at": "2026-03-30T03:00:00Z"
  }
]
```

---

## Runs

### `GET /api/v1/runs`
List all runs across all pipelines, newest first.

Query params: `limit` (default 50, max 500), `offset` (default 0), `status` (filter by status).

```json
// Response 200
[
  {
    "id": "job-uuid",
    "job_id": "job-uuid",
    "pipeline_id": "pipeline-uuid or null",
    "status": "success",
    "triggered_by": "manual",
    "rows_read": 1500,
    "rows_written": 1500,
    "duration_ms": 7200,
    "schema_changed": false,
    "error": null,
    "started_at": "2026-03-30T03:00:01Z",
    "finished_at": "2026-03-30T03:00:08Z",
    "created_at": "2026-03-30T03:00:00Z"
  }
]
```

**Status values:**

| Value | Meaning |
|---|---|
| `queued` | Waiting for a worker thread |
| `running` | Extraction in progress |
| `success` | All rows extracted and written |
| `partial_success` | Some files failed (SFTP only); rows written may be incomplete |
| `failed` | Error during extraction or DQ check failed |

---

### `GET /api/v1/runs/{job_id}`
Get a single run by job UUID.

---

### `GET /api/v1/runs/{job_id}/logs`
Get in-memory logs for a run. Designed for polling.

Query params: `since` (integer offset, default 0). Returns only lines at index ≥ since.

```json
// Response 200
{
  "run_id": "job-uuid",
  "logs": [
    "INFO: Extracting from mysql://***@host:3306/db",
    "INFO: 1500 rows read in 6.2s",
    "INFO: Written to bronze/orders/ (1 file)"
  ],
  "next_offset": 3
}
```

Polling pattern:

```javascript
let offset = 0
while (status === 'running' || status === 'queued') {
  const res = await api.get(`/runs/${id}/logs?since=${offset}`)
  appendToUI(res.logs)
  offset = res.next_offset
  await sleep(2000)
}
```

> Logs are in-memory only. After the job TTL expires (`SIPHON_JOB_TTL_SECONDS`, default 1 hour), the logs array is empty. Historical runs show `[]`.

---

### `POST /api/v1/runs/{job_id}/cancel` *(admin)*
Request cancellation of a queued or running job.

- **Queued jobs**: immediately marked `failed` with `error = "Cancelled by user"`
- **Running jobs**: a cancellation request is logged, but the extraction continues to completion (hard kill is not implemented in v1)

```json
// Response 202
{
  "run_id": "job-uuid",
  "status": "failed",
  "message": "Job cancelled"
}
```

---

## Preview

### `POST /api/v1/preview`
Execute a SQL query against a saved connection and return the first 100 rows. Used by the pipeline wizard to validate queries before saving.

```json
// Request
{
  "connection_id": "uuid-of-sql-connection",
  "query": "SELECT * FROM orders LIMIT 5"
}

// Response 200
{
  "columns": ["id", "customer_id", "total", "created_at"],
  "rows": [
    [1, 42, 99.99, "2026-03-01T00:00:00Z"],
    [2, 43, 49.50, "2026-03-02T00:00:00Z"]
  ],
  "row_count": 2
}
```

Siphon wraps the query in a `LIMIT 100` subquery if no LIMIT is present. The query is never modified in place — the wrapper is: `SELECT * FROM (your_query) _preview LIMIT 100`.

---

## Users

All user endpoints require `admin` role.

### `GET /api/v1/users`
List all users.

### `POST /api/v1/users`
Create a user.

```json
// Request
{
  "email": "operator@example.com",
  "password": "secure-password",
  "role": "operator"
}
```

### `GET /api/v1/users/{id}`
Get a single user.

### `PUT /api/v1/users/{id}`
Update a user. All fields optional.

```json
{ "role": "admin", "password": "new-password", "is_active": false }
```

### `DELETE /api/v1/users/{id}`
Soft-delete (sets `is_active = false`).

---

## Legacy extraction (Airflow)

### `POST /jobs`
Submit an extraction job directly, without a pipeline definition.

```json
// Request
{
  "source": {
    "type": "sql",
    "connection": "mysql://user:pass@host:3306/db",
    "query": "SELECT * FROM orders WHERE updated_at >= @TODAY"
  },
  "destination": {
    "type": "s3_parquet",
    "path": "s3a://bronze/orders/2026-03-30",
    "endpoint": "minio.internal:9000",
    "access_key": "key",
    "secret_key": "secret"
  }
}

// Response 202
{ "job_id": "uuid", "status": "queued" }
```

### `GET /jobs/{job_id}`
Poll job status.

```json
// Response 200
{
  "job_id": "uuid",
  "status": "success",
  "rows_read": 1500,
  "rows_written": 1500,
  "duration_ms": 7200,
  "log_count": 8,
  "failed_files": [],
  "error": null
}
```

### `GET /jobs/{job_id}/logs`
Get log lines with `?since=N` cursor.

---

## Health

### `GET /health/live`
Liveness probe. Always returns 200 if the process is running.

```json
{ "status": "ok" }
```

### `GET /health/ready`
Readiness probe. Returns 503 when the queue is full or the service is shutting down.

```json
// 200 — healthy
{ "status": "ok", "accepting_jobs": true }

// 503 — unhealthy
{ "status": "degraded", "accepting_jobs": false, "reason": "queue_full" }
```

### `GET /health`
Debug endpoint with queue stats and uptime.

```json
{
  "status": "ok",
  "accepting_jobs": true,
  "queue": {
    "workers_active": 2,
    "workers_max": 10,
    "jobs_queued": 0,
    "jobs_total": 47
  },
  "uptime_seconds": 3600
}
```

---

## Metrics

### `GET /metrics`
Prometheus text format.

```
# HELP siphon_jobs_total Total jobs by status
# TYPE siphon_jobs_total counter
siphon_jobs_total{status="success"} 42
siphon_jobs_total{status="failed"} 3

# HELP siphon_job_duration_seconds Job duration in seconds
# TYPE siphon_job_duration_seconds histogram
siphon_job_duration_seconds_bucket{le="5"} 10
...

# HELP siphon_rows_extracted_total Total rows written to destination
# TYPE siphon_rows_extracted_total counter
siphon_rows_extracted_total 1234567

# HELP siphon_queue_depth Current queued + active jobs
# TYPE siphon_queue_depth gauge
siphon_queue_depth 2

# HELP siphon_schema_changes_total Schema drift detections
# TYPE siphon_schema_changes_total counter
siphon_schema_changes_total 1
```

---

## Error responses

All error responses follow the same shape:

```json
{ "detail": "Human-readable error message" }
```

Validation errors (422) include more detail:

```json
{
  "detail": "Request validation failed",
  "errors": [
    { "type": "missing", "loc": ["body", "name"], "msg": "Field required" }
  ]
}
```

Note: the `input` field is stripped from validation errors to prevent credential leakage.

---

## Common HTTP status codes

| Code | Meaning |
|---|---|
| 200 | Success |
| 201 | Created |
| 202 | Accepted (async job submitted) |
| 204 | No Content (delete) |
| 400 | Bad request (driver error, config issue) |
| 401 | Unauthenticated |
| 403 | Forbidden (wrong role) |
| 404 | Not found |
| 409 | Conflict (name already exists) |
| 413 | Request body too large |
| 422 | Validation error |
| 429 | Rate limit exceeded |
| 503 | Service unavailable (queue full, draining, or DB not configured) |
