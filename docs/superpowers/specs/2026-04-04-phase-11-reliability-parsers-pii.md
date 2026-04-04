# Phase 11 — Reliability, Parsers & PII Masking

**Date:** 2026-04-04  
**Branch:** feature/phase-11-reliability  
**Status:** Approved for implementation

---

## Goal

Make Siphon safe for production use by adding:
1. **Idempotent writes** via S3 staging path — eliminate data duplication on worker crash
2. **Retry per batch** with exponential backoff — tolerate transient network/DB failures
3. **CSV and JSON/JSONL parsers** — make SFTP source usable with real data
4. **PII masking** — hash or redact sensitive columns before writing to S3

---

## 1. Idempotent Writes (Staging Path)

### Problem

The current write flow is not atomic:

```
extract → write S3 (final path) → update watermark in DB
                                   ↑ crash here = next run duplicates data
```

### Design

Introduce a staging path per job. The final path is only populated after the DB commit succeeds.

```
cleanup _staging/{job_id}/ 
  → extract batches 
  → write to {prefix}/_staging/{job_id}/part-N.parquet 
  → commit watermark to DB 
  → promote: copy staging files to final path, delete staging
```

**Changes to `S3ParquetDestination`:**

- Constructor receives `job_id: str` (passed from `Job.job_id`)
- `write()` writes to `{root_path}/_staging/{job_id}/` instead of `root_path`
- New method `promote(fs)` — copies all files from staging to final path, then deletes staging prefix
- New method `cleanup_staging(fs)` — deletes `_staging/{job_id}/` if it exists; called at job start

**Changes to `worker.py`:**

- Before extraction begins: call `destination.cleanup_staging(fs)` to remove any incomplete staging from a prior crash
- After successful DB commit: call `destination.promote(fs)` to move files to final path
- If job fails before promote: staging is left in place, cleaned up on next run for same pipeline

**S3 "move" semantics:** Copy + delete within same bucket. Not truly atomic at the S3 API level, but sufficient to prevent downstream reads of partial data (readers access only the final path).

**Incremental mode:** staging uses unique basename templates (`part-{uuid}-{i}.parquet`) same as today, ensuring multiple incremental runs never collide in the final path.

---

## 2. Retry per Batch

### Problem

Transient failures (network drops, DB connection resets) cause immediate job failure with no recovery.

### Design

Retry granularity depends on source type, because "batch" means different things:

| Source | Batch unit | Retry scope |
|---|---|---|
| SQL (ConnectorX) | Entire query (one call) | `cx.read_sql()` call |
| Oracle (cursor) | Cursor dies with connection | Entire cursor query |
| SFTP | One file per batch | Individual file download + parse |

**New utility: `_with_retry(fn, max_retries, backoff_base)`** in `src/siphon/utils/retry.py`:

```python
def _with_retry(fn, max_retries=3, backoff_base=2.0):
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except _RETRYABLE_ERRORS as exc:
            if attempt == max_retries:
                raise
            sleep = backoff_base * (2 ** attempt) * random.uniform(0.5, 1.5)
            logger.warning("Attempt %d failed (%s), retrying in %.1fs", attempt + 1, exc, sleep)
            time.sleep(sleep)
```

**Retryable errors:** `ConnectionError`, `TimeoutError`, `OSError`, and subclasses. Validation errors (`ValueError`, `TypeError`) are never retried.

**In `sql.py`:** wrap `cx.read_sql()` and `oracledb.connect()` with `_with_retry`.

**In `sftp.py`:** extend existing `_download_with_retry` to also cover the `parser.parse()` call. Already-staged files from successful SFTP batches are preserved across retries of the same job.

**Configuration (env vars):**

| Variable | Default | Description |
|---|---|---|
| `SIPHON_MAX_RETRIES` | `3` | Max retry attempts per batch |
| `SIPHON_RETRY_BACKOFF_BASE` | `2` | Base seconds for exponential backoff |

---

## 3. CSV and JSON Parsers

### Problem

`SFTPSource` is unusable in production — the only real parser is a stub that returns raw bytes.

### Design

Two new files in `src/siphon/plugins/parsers/`:

**`csv_parser.py`:**
```python
@register("csv")
class CsvParser(Parser):
    def __init__(self, delimiter: str = ",", encoding: str = "utf-8") -> None:
        self.delimiter = delimiter
        self.encoding = encoding

    def parse(self, data: bytes) -> pa.Table:
        import pyarrow.csv as pacsv
        return pacsv.read_csv(
            pa.BufferReader(data),
            parse_options=pacsv.ParseOptions(delimiter=self.delimiter),
            read_options=pacsv.ReadOptions(encoding=self.encoding),
        )
```

**`json_parser.py`:**
```python
@register("json")
class JsonParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        import pyarrow.json as pajson
        return pajson.read_json(pa.BufferReader(data))
```

PyArrow's JSON reader handles both JSON arrays (`[{...}]`) and JSONL (one object per line) natively — no config needed.

### Parser Config

Parsers currently are instantiated without arguments: `get_parser(parser)()`. To support CSV options, add `parser_config: dict` to `SFTPSourceConfig` and change instantiation to `get_parser(parser)(**parser_config)`.

Parsers without `__init__` params continue to work (empty kwargs). Existing pipelines are unaffected.

**Example SFTP connection config:**
```json
{
  "parser": "csv",
  "parser_config": { "delimiter": ";", "encoding": "latin-1" }
}
```

JSON usage (no config needed):
```json
{ "parser": "json" }
```

---

## 4. PII Masking

### Problem

Sensitive columns (email, CPF, phone) are written raw to S3, creating LGPD/GDPR compliance risk.

### Design

**DB/ORM changes:**
- New column `pii_columns JSONB` (nullable) on `pipelines` table — Alembic migration required
- `Pipeline.pii_columns: Mapped[dict | None]`

**Pydantic changes:**
- `PipelineCreate` and `PipelineUpdate` gain `pii_columns: dict[str, Literal["sha256", "redact"]] | None = None`

**Worker changes:**
New pure function `_apply_pii_masking(table: pa.Table, pii_columns: dict[str, str]) -> pa.Table` applied after each batch is extracted, before write to staging:

| Method | Behavior | Use case |
|---|---|---|
| `sha256` | SHA-256 hex digest of `str(value)` | Email, CPF — preserves joinability across datasets |
| `redact` | Replace with `None` (Arrow null) | Fields that must not exist in the lake |

Columns not present in the extracted table are silently skipped — pipeline does not fail if source schema changes.

**UI changes:**
- Pipeline create/edit form: new section "PII Masking" with add/remove rows (column name + method dropdown)
- Display masking rules in pipeline detail view

**Example pipeline config:**
```json
"pii_columns": {
  "email": "sha256",
  "cpf": "sha256",
  "internal_password_hash": "redact"
}
```

---

## Files Changed

| File | Change |
|---|---|
| `src/siphon/plugins/parsers/csv_parser.py` | New |
| `src/siphon/plugins/parsers/json_parser.py` | New |
| `src/siphon/utils/retry.py` | New |
| `src/siphon/plugins/destinations/s3_parquet.py` | Staging path, promote(), cleanup_staging() |
| `src/siphon/plugins/sources/sql.py` | Wrap extraction with _with_retry |
| `src/siphon/plugins/sources/sftp.py` | Extend retry to include parse(); preserve staging |
| `src/siphon/worker.py` | cleanup_staging → extract → commit → promote; apply PII masking |
| `src/siphon/models.py` | SFTPSourceConfig: parser_config field; PipelineCreate/Update: pii_columns |
| `src/siphon/orm.py` | Pipeline.pii_columns column |
| `src/siphon/pipelines/router.py` | Pass pii_columns through; return in PipelineResponse |
| `alembic/versions/` | Migration: add pii_columns to pipelines |
| `frontend/src/` | PII masking section in pipeline form |
| `tests/` | Unit tests for each new feature |

---

## Testing

- **Staging:** assert that S3 staging path is written before promote; simulate crash before promote and verify final path is clean; verify cleanup_staging removes stale staging
- **Retry:** mock `cx.read_sql` to fail N times then succeed; assert correct number of attempts and sleep calls; assert non-retryable errors are not retried
- **CSV parser:** parse well-formed CSV, CSV with semicolons, CSV with latin-1 encoding; assert schema and row count
- **JSON parser:** parse JSON array, JSONL; assert schema and row count
- **PII masking:** assert sha256 produces hex string of correct length; assert redact produces null column; assert missing columns are skipped
