# Oracle Streaming Extraction Design

## Context

Siphon's Oracle extraction path (`_extract_oracle`) loads entire query results via pandas before writing to S3. For tables with 400M+ rows — which is the expected production workload — this causes OOM kills even on well-provisioned containers.

This spec defines the replacement: cursor-based streaming with native oracledb + Arrow, no pandas in the critical path.

---

## Problem Statement

Current `_extract_oracle` code:

```python
def _extract_oracle(self, query: str) -> pa.Table:  # pragma: no cover
    import oracledb, pandas as pd
    conn = oracledb.connect(...)
    if self.partition_num and self.partition_num > 1:
        chunks = pd.read_sql(query, conn, chunksize=100_000)
        return pa.concat_tables([pa.Table.from_pandas(chunk) for chunk in chunks])
    df = pd.read_sql(query, conn)
    return pa.Table.from_pandas(df, preserve_index=False)
```

Three bugs:
1. `extract_batches()` calls `extract()` and yields once — no real streaming for Oracle
2. `pd.read_sql(..., chunksize=...)` is not true streaming; depending on the driver it may fetch the full result before iterating, and pandas has 2–3x memory overhead over raw Arrow
3. Without `partition_num > 1`, a full-table scan of 400M rows loads everything into a single DataFrame → OOM guaranteed

---

## Goals

- Peak memory = O(chunk_size), not O(table_size), for any Oracle query
- No pandas in the Oracle extraction path
- Works for both filtered queries (`WHERE dt = :date`) and full scans (`SELECT *`)
- Configurable chunk size via env var
- Proper test coverage (currently `# pragma: no cover`)

## Non-Goals (TODO for future phases)

- **Oracle parallel partitioning:** executing N sub-queries in parallel using `partition_on`. Current streaming already solves the OOM; parallelism is a performance optimization for later.
- **ConnectorX + Oracle via JDBC:** still experimental upstream, risk too high for production.
- **Integration test with real Oracle:** no Oracle container in CI. Will be added when a licensed Oracle instance is available for testing.

---

## Architecture

### What changes

`extract_batches()` in `SQLSource` gains an Oracle-specific branch:

```
SQLSource.extract_batches()
  ├── oracle://...  → _extract_oracle_batches(query, chunk_size)   ← NEW: real generator
  └── everything else → extract() via ConnectorX                   ← unchanged
```

`_extract_oracle_batches()` is a generator that uses `cursor.fetchmany(chunk_size)` in a loop. Each iteration yields one `pa.Table`. The loop exits when `fetchmany` returns an empty list.

### What stays the same

- ConnectorX path (MySQL, PostgreSQL, MSSQL) — untouched
- `_validate_host()` — called before any Oracle connection
- `_oracle_dsn()` — still parses the connection URI
- `extract()` for Oracle — delegates to `_extract_oracle_batches()` and concatenates via `pa.concat_tables()`. Safe for small tables; documented as unsafe for large ones.
- `partition_on` / `partition_num` / `partition_range` for Oracle — logged as warning + ignored. Oracle parallel partitioning is a TODO.

### New env var

`SIPHON_ORACLE_CHUNK_SIZE` — integer, default `50_000`. Controls both `cursor.arraysize` (server-side fetch batch size) and `fetchmany(chunk_size)` (client-side iteration batch). These are intentionally kept equal.

---

## Type Mapping

oracledb returns `decimal.Decimal` for `NUMBER` columns by default. PyArrow cannot reliably infer these. The fix is an `outputtypehandler` registered on the connection, which instructs oracledb to return native Python types before the data reaches Arrow.

### `_oracle_output_type_handler(cursor, name, default_type, size, precision, scale)`

| Oracle type | oracledb DB_TYPE | Python type returned | Arrow type |
|---|---|---|---|
| `NUMBER(p, scale=0)` | `DB_TYPE_NUMBER` | `int` | `pa.int64()` |
| `NUMBER(p, scale>0)` | `DB_TYPE_NUMBER` | `float` | `pa.float64()` |
| `DATE` | `DB_TYPE_DATE` | `datetime` | `pa.timestamp('us')` |
| `TIMESTAMP` | `DB_TYPE_TIMESTAMP` | `datetime` | `pa.timestamp('us')` |
| `VARCHAR2`, `CHAR`, `NVARCHAR2` | — | `str` (default) | `pa.string()` (inferred) |
| `CLOB`, `NCLOB` | `DB_TYPE_CLOB` | `str` | `pa.large_string()` + `logger.warning` |
| `BLOB` | `DB_TYPE_BLOB` | `bytes` | `pa.large_binary()` |

**CLOB warning:** CLOB columns are supported but emit `logger.warning` per chunk. Large CLOB values per row can still cause memory spikes within a chunk. Users should be aware.

**Unmapped types** (RAW, XMLTYPE, INTERVAL, etc.): passed through to Arrow's default inference. If Arrow cannot infer, the job fails with a clear PyArrow error. No silent corruption.

### Building Arrow tables from cursor rows

```python
rows = cursor.fetchmany(chunk_size)
columns = [col[0].lower() for col in cursor.description]
arrays = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
yield pa.Table.from_pydict(arrays)
```

`from_pydict` with native Python types (post-handler) handles type inference cleanly for all mapped types above.

---

## Connection Management

```python
conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)
conn.outputtypehandler = _oracle_output_type_handler
with conn:
    with conn.cursor() as cursor:
        cursor.arraysize = chunk_size
        cursor.execute(query)
        while rows := cursor.fetchmany(chunk_size):
            yield _oracle_rows_to_arrow(rows, cursor.description)
```

Using `with conn` and `with conn.cursor()` ensures both are closed even if an exception is raised mid-iteration. `_CONNECT_TIMEOUT` from `SIPHON_CONNECT_TIMEOUT` (default 30s) applies as before.

---

## File Changes

| File | Change |
|---|---|
| `src/siphon/plugins/sources/sql.py` | Add `_extract_oracle_batches()`, `_oracle_output_type_handler()`, `_oracle_rows_to_arrow()`. Update `extract_batches()` to branch on Oracle. Update `extract()` for Oracle to use new path. Remove pandas import from Oracle path. Remove `# pragma: no cover`. Add `SIPHON_ORACLE_CHUNK_SIZE` env var. |
| `tests/test_sql_source.py` | Add 5 new Oracle streaming tests (see Testing section). |

---

## Testing

All tests mock `oracledb` — no real Oracle instance required.

### `test_oracle_yields_chunks`
Mock cursor returns 3 batches of 10 rows each (`fetchmany` called 4 times: 3 with data, 1 empty). Assert 3 `pa.Table` objects yielded, each with 10 rows.

### `test_oracle_number_types`
Mock cursor with two NUMBER columns: `id NUMBER(10,0)` and `price NUMBER(10,2)`. Assert `outputtypehandler` causes `id` → `pa.int64()` and `price` → `pa.float64()` in resulting table schema.

### `test_oracle_date_type`
Mock cursor with one `DATE` column returning `datetime` objects. Assert resulting Arrow column type is `pa.timestamp('us')`.

### `test_oracle_clob_emits_warning`
Mock cursor with one `CLOB` column. Assert `logger.warning` is called at least once during iteration.

### `test_oracle_empty_result`
Mock cursor where first `fetchmany` returns `[]`. Assert `extract_batches()` yields zero tables without raising.

---

## Backward Compatibility

- `extract()` for Oracle still works — it now delegates to `_extract_oracle_batches()` and concatenates. Behavior is identical for small tables.
- `partition_on` / `partition_num` for Oracle: previously only affected chunksize selection (the `if self.partition_num > 1` branch). That branch is removed. A `logger.warning` is emitted if these fields are set on an Oracle connection, telling the user they are ignored and pointing to the TODO.
- No change to ConnectorX path, models, worker, or any other file.
