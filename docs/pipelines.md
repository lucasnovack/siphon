# Pipelines

A pipeline defines how data flows from a source to a destination. It stores the SQL query, the source and destination connections, the extraction mode, optional data quality rules, and an optional schedule.

---

## Concepts

| Field | Description |
|---|---|
| `name` | Unique human-readable identifier |
| `source_connection_id` | The SQL connection to read from |
| `dest_connection_id` | The S3/MinIO connection to write to |
| `query` | SQL query to execute on the source. Supports [date variables](#date-variables) |
| `extraction_mode` | `full_refresh` (truncate + reload) or `incremental` (WHERE clause on watermark) |
| `incremental_key` | Column name used for watermark filtering, e.g. `updated_at` |
| `last_watermark` | ISO-8601 UTC timestamp of the last successful incremental run. Set automatically |
| `destination_path` | S3 key prefix, e.g. `bronze/orders/`. Supports `{date}` substitution |
| `min_rows_expected` | Minimum rows required to accept a run (DQ guard). Optional |
| `max_rows_drop_pct` | Maximum % drop allowed vs. previous run (DQ guard). Optional |
| `schedule` | Cron expression + active flag. Optional |
| `is_active` | Whether the pipeline is enabled. Inactive pipelines can still be triggered manually |

**Permissions**: only `admin` users can create, update, or delete pipelines and schedules.

---

## Via the UI

### Creating a pipeline

1. Navigate to **Pipelines → New Pipeline**
2. Fill in **name**, select **source connection**, and write a **SQL query**
3. Use the **Preview (100 rows)** button to validate the query before saving
4. Select **extraction mode**:
   - `full_refresh`: all rows are re-written on every run
   - `incremental`: only rows newer than the last watermark are extracted
5. If incremental, set the **watermark column** (e.g. `updated_at`)
6. Select the **destination connection** and set the **S3 prefix**
7. Optionally set **data quality rules**
8. Optionally add a **cron schedule**
9. Click **Create Pipeline**

### Triggering a run

Open the pipeline detail page and click **Run Now**. The run appears immediately in the Runs list with status `queued`.

### Editing a schedule

On the pipeline detail page, click **Add Schedule** or **Edit** next to the existing cron. Enter a 5-field cron expression and click **Save Schedule**.

---

## Via the API

### Create a pipeline

```http
POST /api/v1/pipelines
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "orders-daily",
  "source_connection_id": "uuid-of-mysql-connection",
  "dest_connection_id":   "uuid-of-minio-connection",
  "query":                "SELECT * FROM orders WHERE updated_at >= @TODAY",
  "extraction_mode":      "full_refresh",
  "destination_path":     "bronze/orders/"
}
```

### Trigger a run

```http
POST /api/v1/pipelines/{id}/trigger
Authorization: Bearer <token>
```

Response:

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued",
  "run_id": 42
}
```

Use `job_id` to poll for status: `GET /api/v1/runs/{job_id}`

### Set a schedule

```http
PUT /api/v1/pipelines/{id}/schedule
Authorization: Bearer <token>
Content-Type: application/json

{
  "cron": "0 3 * * *",
  "is_active": true
}
```

Cron format: **5 fields** — minute, hour, day-of-month, month, day-of-week.

```
┌───────────── minute (0–59)
│ ┌───────────── hour (0–23)
│ │ ┌───────────── day of month (1–31)
│ │ │ ┌───────────── month (1–12)
│ │ │ │ ┌───────────── day of week (0–6, 0=Sunday)
│ │ │ │ │
0 3 * * *   →  every day at 03:00
0 */6 * * * →  every 6 hours
0 8 * * 1   →  every Monday at 08:00
```

### Remove a schedule

```http
DELETE /api/v1/pipelines/{id}/schedule
Authorization: Bearer <token>
```

---

## Extraction modes

### Full refresh

Every run truncates the destination and rewrites all rows from scratch.

Use this when:
- The source table has no reliable updated_at column
- Datasets are small enough to re-extract completely
- You want the destination to always be an exact mirror of the source

### Incremental

Only rows with `incremental_key > last_watermark` are extracted. After a successful run, `last_watermark` is updated to the current UTC timestamp.

Use this when:
- The source table is large and re-extracting everything would be slow or expensive
- The source has a reliable `updated_at` or `created_at` column
- You only need new and modified rows

**How it works under the hood:**

Siphon wraps your query in a CTE and injects a WHERE clause:

```sql
-- Your original query
SELECT * FROM orders

-- What actually runs (PostgreSQL example)
WITH _siphon_base AS (
    SELECT * FROM orders
)
SELECT * FROM _siphon_base
WHERE updated_at > CAST('2026-03-29T03:00:00+00:00' AS TIMESTAMPTZ)
```

The CAST type is dialect-aware:

| Database | Cast type |
|---|---|
| PostgreSQL | `TIMESTAMPTZ` |
| MySQL | `DATETIME` |
| Oracle | `TIMESTAMP WITH TIME ZONE` |
| SQL Server | `DATETIMEOFFSET` |

**First run**: no `last_watermark` exists, so the WHERE clause is not injected. The full table is extracted.

**Subsequent runs**: the WHERE clause filters to rows newer than the previous run's timestamp.

> ⚠️ The watermark is the time the run *started*, not the max value from the data. This is intentional: it avoids boundary issues where new rows are written to the source just after the query starts. The trade-off is that rows written during the extraction window may be re-read on the next run (usually harmless for append-only Bronze data).

---

## Date variables

You can embed dynamic date placeholders directly in your SQL queries. Siphon resolves them at runtime before sending the query to the database.

| Variable | Resolves to | Example |
|---|---|---|
| `@TODAY` | Current date (SIPHON_TIMEZONE) | `2026-03-30` |
| `@MIN_DATE` | SIPHON_MIN_DATE (default `1997-01-01`) | `1997-01-01` |
| `@LAST_MONTH` | First day of previous month | `2026-03-01` |
| `@NEXT_MONTH` | First day of next month | `2026-05-01` |

**Example:**

```sql
SELECT *
FROM sales
WHERE sale_date BETWEEN @LAST_MONTH AND @TODAY
```

Timezone and min date are controlled by environment variables:

```
SIPHON_TIMEZONE=America/Sao_Paulo   # default
SIPHON_MIN_DATE=1997-01-01          # default
```

---

## Destination path

The `destination_path` field sets the S3 prefix where Parquet files are written.

```
bronze/orders/
bronze/customers/2026-03-30/
bronze/sales/{date}/
```

`{date}` is replaced at runtime with today's date in `YYYY-MM-DD` format. This is useful for creating date-partitioned prefixes automatically.

**Path rules:**
- Must start with `SIPHON_ALLOWED_S3_PREFIX` (default: `bronze/`)
- Cannot contain `..` (path traversal prevention)
- The trailing `/` is recommended but not required

For each run, Siphon:
1. Deletes all existing Parquet files at the destination path (first batch)
2. Writes new Parquet files (subsequent batches append)

This means running the same pipeline twice is idempotent — the second run replaces the first.

---

## Data Quality (DQ) guards

DQ guards run after extraction is complete but before writing to S3. If either guard fails, the run is marked `failed` and nothing is written.

### `min_rows_expected`

Rejects the run if `rows_read < min_rows_expected`.

Use this to catch source tables that have been accidentally truncated, or feeds that are unexpectedly empty.

```json
{ "min_rows_expected": 1000 }
```

### `max_rows_drop_pct`

Rejects the run if `(prev_rows - rows_read) / prev_rows > max_rows_drop_pct / 100`.

Use this to catch large accidental deletes in the source. For example, `max_rows_drop_pct: 20` means the run fails if more than 20% of the previous row count has disappeared.

```json
{ "max_rows_drop_pct": 20 }
```

**How DQ affects memory**: when DQ guards are set, all batches are buffered in memory before writing begins. For very large datasets without DQ guards, data streams directly from source to destination without buffering.

---

## Schema drift detection

After each run, Siphon computes a SHA-256 hash of the Arrow schema (column names + types). If the schema differs from the previous run, the run is marked with `schema_changed = true` and the UI displays a **Schema Changed** badge.

The run still succeeds — schema drift is a warning, not an error. The new schema hash is saved as the baseline for future runs.

Common causes:
- A column was added or removed from the source table
- A column type changed (e.g. INT → BIGINT)
- Column order changed (rare with SELECT *)

---

## Scheduling internals

When you set a schedule via the API or UI, Siphon:

1. Saves the schedule to the `schedules` table in PostgreSQL
2. Calls `sync_schedule(pipeline_id, cron, is_active)` which adds or updates the job in APScheduler
3. APScheduler stores the job in PostgreSQL (same database, different table managed by the library)

When the cron fires:
1. APScheduler calls `_fire_pipeline(pipeline_id)` in a background thread
2. The function acquires a PostgreSQL advisory lock (`pg_try_advisory_xact_lock`) to prevent duplicate fires in multi-pod deployments
3. If the lock is acquired: decrypts credentials, builds Job, submits to queue
4. The run is recorded with `triggered_by = "schedule"`

Schedules survive pod restarts because both the schedule definition and the next-fire-time are stored in PostgreSQL.

---

## Legacy API (Airflow integration)

Before pipelines existed, Siphon exposed a direct extraction endpoint for Airflow:

```http
POST /jobs
Authorization: Bearer <api-key>
Content-Type: application/json

{
  "source": {
    "type": "sql",
    "connection": "mysql://user:pass@host:3306/db",
    "query": "SELECT * FROM orders WHERE updated_at >= @TODAY"
  },
  "destination": {
    "type": "s3_parquet",
    "path": "s3a://bronze/orders/",
    "endpoint": "minio.internal:9000",
    "access_key": "...",
    "secret_key": "..."
  }
}
```

This endpoint still works and is the recommended integration point for Airflow. It authenticates via the legacy `SIPHON_API_KEY` and does not require a pipeline to exist in the database.

Poll for status:

```http
GET /jobs/{job_id}
Authorization: Bearer <api-key>
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Run fails with `Host '...' not in SIPHON_ALLOWED_HOSTS` | SSRF guard is active | Add the source host to `SIPHON_ALLOWED_HOSTS` |
| Run fails with `Unknown URL parameter 'connect_timeout'` | MySQL connection with `connect_timeout` in URL | Remove the parameter; it's only supported for PostgreSQL |
| Run shows `schema_changed = true` | Source table schema changed | Review changes; update downstream consumers if needed |
| Incremental run re-extracts all data | `last_watermark` was reset (e.g. after pipeline re-create) | Trigger one full run; subsequent runs will be incremental |
| DQ check fails: `rows below minimum` | Source returned fewer rows than expected | Check source; lower `min_rows_expected` if intentional |
| Run queued but never starts | Worker threads exhausted | Increase `SIPHON_MAX_WORKERS` or reduce concurrent runs |
| Schedule not firing | APScheduler not running (DATABASE_URL not set) | Set `DATABASE_URL`; check scheduler logs on startup |
