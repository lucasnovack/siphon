# Oracle Cursor Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pandas-based Oracle extraction path with an `oracledb` cursor streaming generator so peak memory is O(chunk_size) regardless of table size.

**Architecture:** Add three module-level helpers (`_oracle_rows_to_arrow`, `_oracle_output_type_handler`, `_ORACLE_CHUNK_SIZE`) and one new method (`_extract_oracle_batches`) to `SQLSource`. Wire `extract()` and `extract_batches()` to use the new path; delete the old pandas path entirely.

**Tech Stack:** `oracledb` (thin mode, local import), `pyarrow`, `pytest` with `MagicMock` + `patch.dict(sys.modules)`

---

## File Map

| File | Change |
|---|---|
| `src/siphon/plugins/sources/sql.py` | Add `from datetime import datetime`. Add `_ORACLE_CHUNK_SIZE`. Add `_oracle_rows_to_arrow()`. Add `_oracle_output_type_handler()`. Add `SQLSource._extract_oracle_batches()`. Rewrite `SQLSource.extract()` Oracle branch. Rewrite `SQLSource.extract_batches()`. Remove old `_extract_oracle()`. |
| `tests/test_sql_source.py` | Add 9 new Oracle tests. |

---

### Task 1: `_oracle_rows_to_arrow` helper + tests

**Files:**
- Modify: `src/siphon/plugins/sources/sql.py`
- Test: `tests/test_sql_source.py`

- [ ] **Step 1: Write the failing tests**

Add to the bottom of `tests/test_sql_source.py`:

```python
# ── Oracle helpers ─────────────────────────────────────────────────────────────


def test_oracle_rows_to_arrow_basic():
    from siphon.plugins.sources.sql import _oracle_rows_to_arrow

    rows = [(1, "Alice"), (2, "Bob")]
    description = [
        ("ID", None, None, None, None, None, None),
        ("NAME", None, None, None, None, None, None),
    ]
    table = _oracle_rows_to_arrow(rows, description)

    assert table.num_rows == 2
    assert table.column_names == ["id", "name"]
    assert table["id"].to_pylist() == [1, 2]
    assert table["name"].to_pylist() == ["Alice", "Bob"]


def test_oracle_rows_to_arrow_infers_int64():
    from siphon.plugins.sources.sql import _oracle_rows_to_arrow

    rows = [(1,), (2,)]
    description = [("ID", None, None, None, None, None, None)]
    table = _oracle_rows_to_arrow(rows, description)

    assert table.schema.field("id").type == pa.int64()


def test_oracle_rows_to_arrow_infers_float64():
    from siphon.plugins.sources.sql import _oracle_rows_to_arrow

    rows = [(1.5,), (2.7,)]
    description = [("PRICE", None, None, None, None, None, None)]
    table = _oracle_rows_to_arrow(rows, description)

    assert table.schema.field("price").type == pa.float64()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_sql_source.py::test_oracle_rows_to_arrow_basic tests/test_sql_source.py::test_oracle_rows_to_arrow_infers_int64 tests/test_sql_source.py::test_oracle_rows_to_arrow_infers_float64 -v
```

Expected: `ImportError: cannot import name '_oracle_rows_to_arrow'`

- [ ] **Step 3: Add `from datetime import datetime` and `_ORACLE_CHUNK_SIZE` and `_oracle_rows_to_arrow`**

In `src/siphon/plugins/sources/sql.py`, add to the stdlib imports block (after `from urllib.parse ...`):

```python
from datetime import datetime
```

After the `_ALLOWED_HOSTS` line add:

```python
_ORACLE_CHUNK_SIZE = int(os.getenv("SIPHON_ORACLE_CHUNK_SIZE", "50000"))
```

After the `logger = ...` line (or after the module constants), add as a module-level function before the `SQLSource` class:

```python
def _oracle_rows_to_arrow(rows: list, description) -> pa.Table:
    columns = [col[0].lower() for col in description]
    arrays = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
    return pa.Table.from_pydict(arrays)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_sql_source.py::test_oracle_rows_to_arrow_basic tests/test_sql_source.py::test_oracle_rows_to_arrow_infers_int64 tests/test_sql_source.py::test_oracle_rows_to_arrow_infers_float64 -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/siphon/plugins/sources/sql.py tests/test_sql_source.py
git commit -m "feat: add _oracle_rows_to_arrow helper and _ORACLE_CHUNK_SIZE constant"
```

---

### Task 2: `_oracle_output_type_handler` + tests

**Files:**
- Modify: `src/siphon/plugins/sources/sql.py`
- Test: `tests/test_sql_source.py`

- [ ] **Step 1: Write the failing tests**

Add after the `test_oracle_rows_to_arrow_infers_float64` test:

```python
# ── Oracle output type handler ─────────────────────────────────────────────────


def _make_oracle_mock():
    """Build a mock oracledb module with unique sentinel DB_TYPE constants."""
    from unittest.mock import MagicMock

    mock_ora = MagicMock()
    mock_ora.DB_TYPE_NUMBER = object()
    mock_ora.DB_TYPE_DATE = object()
    mock_ora.DB_TYPE_TIMESTAMP = object()
    mock_ora.DB_TYPE_CLOB = object()
    mock_ora.DB_TYPE_BLOB = object()
    return mock_ora


def test_oracle_handler_number_integer():
    from unittest.mock import MagicMock, patch

    from siphon.plugins.sources.sql import _oracle_output_type_handler

    mock_ora = _make_oracle_mock()
    mock_cursor = MagicMock()
    mock_cursor.arraysize = 50_000

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        _oracle_output_type_handler(mock_cursor, "id", mock_ora.DB_TYPE_NUMBER, None, 10, 0)

    mock_cursor.var.assert_called_once_with(int, arraysize=50_000)


def test_oracle_handler_number_float():
    from unittest.mock import MagicMock, patch

    from siphon.plugins.sources.sql import _oracle_output_type_handler

    mock_ora = _make_oracle_mock()
    mock_cursor = MagicMock()
    mock_cursor.arraysize = 50_000

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        _oracle_output_type_handler(mock_cursor, "price", mock_ora.DB_TYPE_NUMBER, None, 10, 2)

    mock_cursor.var.assert_called_once_with(float, arraysize=50_000)


def test_oracle_handler_date():
    from datetime import datetime
    from unittest.mock import MagicMock, patch

    from siphon.plugins.sources.sql import _oracle_output_type_handler

    mock_ora = _make_oracle_mock()
    mock_cursor = MagicMock()
    mock_cursor.arraysize = 50_000

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        _oracle_output_type_handler(mock_cursor, "created_at", mock_ora.DB_TYPE_DATE, None, None, None)

    mock_cursor.var.assert_called_once_with(datetime, arraysize=50_000)


def test_oracle_handler_clob_warns(caplog):
    import logging
    from unittest.mock import MagicMock, patch

    from siphon.plugins.sources.sql import _oracle_output_type_handler

    mock_ora = _make_oracle_mock()
    mock_cursor = MagicMock()
    mock_cursor.arraysize = 50_000

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        with caplog.at_level(logging.WARNING, logger="siphon.plugins.sources.sql"):
            _oracle_output_type_handler(mock_cursor, "notes", mock_ora.DB_TYPE_CLOB, None, None, None)

    assert any("CLOB" in r.message for r in caplog.records)
    mock_cursor.var.assert_called_once_with(str, arraysize=50_000)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_sql_source.py::test_oracle_handler_number_integer tests/test_sql_source.py::test_oracle_handler_number_float tests/test_sql_source.py::test_oracle_handler_date tests/test_sql_source.py::test_oracle_handler_clob_warns -v
```

Expected: `ImportError: cannot import name '_oracle_output_type_handler'`

- [ ] **Step 3: Implement `_oracle_output_type_handler`**

Add as a module-level function in `src/siphon/plugins/sources/sql.py`, right after `_oracle_rows_to_arrow`:

```python
def _oracle_output_type_handler(cursor, name, default_type, size, precision, scale):
    import oracledb

    if default_type == oracledb.DB_TYPE_NUMBER:
        if scale == 0:
            return cursor.var(int, arraysize=cursor.arraysize)
        return cursor.var(float, arraysize=cursor.arraysize)
    if default_type in (oracledb.DB_TYPE_DATE, oracledb.DB_TYPE_TIMESTAMP):
        return cursor.var(datetime, arraysize=cursor.arraysize)
    if default_type == oracledb.DB_TYPE_CLOB:
        logger.warning(
            "CLOB column '%s' detected — large values may cause memory spikes within a chunk",
            name,
        )
        return cursor.var(str, arraysize=cursor.arraysize)
    if default_type == oracledb.DB_TYPE_BLOB:
        return cursor.var(bytes, arraysize=cursor.arraysize)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_sql_source.py::test_oracle_handler_number_integer tests/test_sql_source.py::test_oracle_handler_number_float tests/test_sql_source.py::test_oracle_handler_date tests/test_sql_source.py::test_oracle_handler_clob_warns -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/siphon/plugins/sources/sql.py tests/test_sql_source.py
git commit -m "feat: add _oracle_output_type_handler with NUMBER/DATE/CLOB/BLOB mapping"
```

---

### Task 3: `_extract_oracle_batches` generator + streaming tests

**Files:**
- Modify: `src/siphon/plugins/sources/sql.py`
- Test: `tests/test_sql_source.py`

- [ ] **Step 1: Write the failing tests**

Add a shared fixture helper and two tests after the handler tests:

```python
# ── Oracle streaming ───────────────────────────────────────────────────────────


def _make_oracle_conn_mock(fetchmany_side_effect, description):
    """Build mock oracledb module + mock cursor wired for streaming tests."""
    from unittest.mock import MagicMock

    mock_ora = _make_oracle_mock()
    mock_cursor = MagicMock()
    mock_cursor.fetchmany.side_effect = fetchmany_side_effect
    mock_cursor.description = description
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_ora.connect.return_value = mock_conn
    return mock_ora, mock_cursor


def test_oracle_yields_chunks():
    from unittest.mock import patch

    rows_batch = [(i, f"name_{i}") for i in range(10)]
    desc = [
        ("ID", None, None, None, None, None, None),
        ("NAME", None, None, None, None, None, None),
    ]
    mock_ora, _ = _make_oracle_conn_mock(
        fetchmany_side_effect=[rows_batch, rows_batch, rows_batch, []],
        description=desc,
    )

    cls = get_source("sql")
    src = cls(connection="oracle+oracledb://user:pass@host:1521/svc", query="SELECT * FROM t")

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        tables = list(src._extract_oracle_batches("SELECT * FROM t", 10))

    assert len(tables) == 3
    for table in tables:
        assert table.num_rows == 10
        assert table.column_names == ["id", "name"]


def test_oracle_empty_result():
    from unittest.mock import patch

    desc = [("ID", None, None, None, None, None, None)]
    mock_ora, _ = _make_oracle_conn_mock(
        fetchmany_side_effect=[[]],
        description=desc,
    )

    cls = get_source("sql")
    src = cls(connection="oracle+oracledb://user:pass@host:1521/svc", query="SELECT id FROM t")

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        tables = list(src._extract_oracle_batches("SELECT id FROM t", 10))

    assert tables == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_sql_source.py::test_oracle_yields_chunks tests/test_sql_source.py::test_oracle_empty_result -v
```

Expected: `AttributeError: 'SQLSource' object has no attribute '_extract_oracle_batches'`

- [ ] **Step 3: Implement `_extract_oracle_batches`**

Add as a method inside `SQLSource` in `src/siphon/plugins/sources/sql.py`, after `_extract_connectorx`:

```python
def _extract_oracle_batches(self, query: str, chunk_size: int) -> Iterator[pa.Table]:
    import oracledb

    conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)
    conn.outputtypehandler = _oracle_output_type_handler
    with conn:
        with conn.cursor() as cursor:
            cursor.arraysize = chunk_size
            cursor.execute(query)
            while rows := cursor.fetchmany(chunk_size):
                yield _oracle_rows_to_arrow(rows, cursor.description)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_sql_source.py::test_oracle_yields_chunks tests/test_sql_source.py::test_oracle_empty_result -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/siphon/plugins/sources/sql.py tests/test_sql_source.py
git commit -m "feat: add _extract_oracle_batches cursor streaming generator"
```

---

### Task 4: Wire `extract()` + `extract_batches()`, partition warning, remove old code

**Files:**
- Modify: `src/siphon/plugins/sources/sql.py`
- Test: `tests/test_sql_source.py`

- [ ] **Step 1: Write the failing test for partition warning**

Add after `test_oracle_empty_result`:

```python
def test_oracle_extract_batches_warns_on_partition(caplog):
    import logging
    from unittest.mock import patch

    rows = [(1,)]
    desc = [("ID", None, None, None, None, None, None)]
    mock_ora, _ = _make_oracle_conn_mock(
        fetchmany_side_effect=[rows, []],
        description=desc,
    )

    cls = get_source("sql")
    src = cls(
        connection="oracle+oracledb://user:pass@host:1521/svc",
        query="SELECT id FROM t",
        partition_on="id",
        partition_num=4,
    )

    with patch.dict("sys.modules", {"oracledb": mock_ora}):
        with caplog.at_level(logging.WARNING, logger="siphon.plugins.sources.sql"):
            tables = list(src.extract_batches())

    assert any("partition" in r.message.lower() for r in caplog.records)
    assert len(tables) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_sql_source.py::test_oracle_extract_batches_warns_on_partition -v
```

Expected: FAIL — `extract_batches` still calls `self.extract()` which hits the old `_extract_oracle` with `# pragma: no cover`

- [ ] **Step 3: Rewrite `extract()`, `extract_batches()`, remove `_extract_oracle`**

Replace the entire `extract`, `extract_batches`, and `_extract_oracle` methods in `SQLSource`. The full updated class body (methods only, keeping `__init__` and `_extract_connectorx` and `_oracle_dsn` unchanged):

```python
def extract(self) -> pa.Table:
    _validate_host(self.connection)
    query = variables.resolve(self.query)

    if self.connection.startswith("oracle"):
        logger.info("Extracting Oracle from %s", mask_uri(self.connection))
        return pa.concat_tables(list(self._extract_oracle_batches(query, _ORACLE_CHUNK_SIZE)))

    conn = _inject_timeout(self.connection)
    logger.info("Extracting from %s", mask_uri(conn))
    return self._extract_connectorx(conn, query)

def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
    if self.connection.startswith("oracle"):
        _validate_host(self.connection)
        query = variables.resolve(self.query)
        logger.info("Streaming Oracle from %s", mask_uri(self.connection))
        yield from self._extract_oracle_batches(query, _ORACLE_CHUNK_SIZE)
    else:
        yield self.extract()
```

Also add the partition warning inside `_extract_oracle_batches`, right before the `oracledb.connect(...)` call:

```python
def _extract_oracle_batches(self, query: str, chunk_size: int) -> Iterator[pa.Table]:
    import oracledb

    if self.partition_on is not None or self.partition_num is not None:
        logger.warning(
            "partition_on/partition_num are ignored for Oracle — "
            "Oracle parallel partitioning is not yet supported"
        )

    conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)
    conn.outputtypehandler = _oracle_output_type_handler
    with conn:
        with conn.cursor() as cursor:
            cursor.arraysize = chunk_size
            cursor.execute(query)
            while rows := cursor.fetchmany(chunk_size):
                yield _oracle_rows_to_arrow(rows, cursor.description)
```

Delete the old `_extract_oracle` method entirely (lines 62–73 in the original file).

- [ ] **Step 4: Run all tests**

```
pytest tests/test_sql_source.py -v
```

Expected: all tests pass (original 9 + new 9 = 18 total). Verify no `# pragma: no cover` is needed on the Oracle path — `_extract_oracle_batches` is now fully tested.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```
pytest -v
```

Expected: all 129+ tests pass (check the exact count matches or exceeds the previous baseline)

- [ ] **Step 6: Commit**

```bash
git add src/siphon/plugins/sources/sql.py tests/test_sql_source.py
git commit -m "feat: wire Oracle streaming into extract/extract_batches, remove pandas path, warn on partition args"
```

---

## Self-Review Checklist

Spec coverage:
- [x] Peak memory O(chunk_size): `cursor.fetchmany` loop — Task 3
- [x] No pandas in Oracle path: old `_extract_oracle` deleted — Task 4
- [x] Filtered and full-scan queries: generator works for any query string — Task 3
- [x] `SIPHON_ORACLE_CHUNK_SIZE` env var: `_ORACLE_CHUNK_SIZE` constant — Task 1
- [x] Tests previously `# pragma: no cover`: removed, all Oracle paths now tested — Task 3/4
- [x] `outputtypehandler` registered on connection — Task 3
- [x] NUMBER → int/float, DATE/TIMESTAMP → datetime, CLOB → str + warning, BLOB → bytes — Task 2
- [x] `cursor.arraysize` set equal to `chunk_size` — Task 3
- [x] `extract()` Oracle path delegates to `_extract_oracle_batches` + concat — Task 4
- [x] `partition_on`/`partition_num` for Oracle: warning + ignored — Task 4
- [x] ConnectorX path untouched — Task 4 (only Oracle branch changed)
- [x] `_oracle_dsn` and `_validate_host` unchanged — not touched
- [x] Unmapped types: no special handling → Arrow default inference, no silent corruption — Task 2 (handler returns None for unhandled types, oracledb default applies)
