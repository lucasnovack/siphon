# Phase 3 — SQL Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `SQLSource` — the SQL extraction plugin that covers MySQL, PostgreSQL, MSSQL, SQLite via ConnectorX and Oracle via pandas + oracledb thin mode.

**Architecture:** A single file `src/siphon/plugins/sources/sql.py` implements `SQLSource(Source)` decorated with `@register("sql")`. It reads `SQLSourceConfig`, resolves date variables before executing the query, validates the host against `SIPHON_ALLOWED_HOSTS`, injects `connect_timeout`, and routes to ConnectorX or the Oracle path based on the connection string prefix.

**Tech Stack:** ConnectorX (`connectorx`), PyArrow (`pyarrow`), oracledb (`oracledb`), pandas (`pandas`), Python stdlib `urllib.parse`, `ipaddress`, `os`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/siphon/plugins/sources/sql.py` | `SQLSource` implementation |
| Create | `tests/test_sql_source.py` | Unit tests for SQLSource |
| No change | `src/siphon/plugins/sources/__init__.py` | Already autodiscovers — no changes needed |
| No change | `src/siphon/models.py` | `mask_uri` already exists, `SQLSourceConfig` already defined |
| No change | `src/siphon/variables.py` | `resolve()` already exists |

---

### Task 1: Core SQLSource scaffold + ConnectorX path

**Files:**
- Create: `src/siphon/plugins/sources/sql.py`
- Create: `tests/test_sql_source.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sql_source.py
import pyarrow as pa
import pytest
from unittest.mock import MagicMock, patch

from siphon.plugins.sources import get as get_source


# ── Registry ──────────────────────────────────────────────────────────────────

def test_sql_source_is_registered():
    cls = get_source("sql")
    assert cls.__name__ == "SQLSource"


# ── ConnectorX path ───────────────────────────────────────────────────────────

def test_extract_connectorx_passes_partition_args():
    """partition_on/num/range are forwarded to cx.read_sql."""
    table = pa.table({"id": [1, 2, 3]})

    with patch("connectorx.read_sql", return_value=table) as mock_cx:
        cls = get_source("sql")
        src = cls(
            connection="mysql://user:pass@db:3306/mydb",
            query="SELECT id FROM t",
            partition_on="id",
            partition_num=4,
            partition_range=(1, 100),
        )
        result = src.extract()

    mock_cx.assert_called_once()
    _, kwargs = mock_cx.call_args
    assert kwargs.get("partition_on") == "id"
    assert kwargs.get("partition_num") == 4
    assert kwargs.get("partition_range") == (1, 100)
    assert kwargs.get("return_type") == "arrow"
    assert result is table


def test_extract_connectorx_no_partition():
    """None partition args are not forwarded."""
    table = pa.table({"x": [1]})

    with patch("connectorx.read_sql", return_value=table) as mock_cx:
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@host/db", query="SELECT 1")
        src.extract()

    _, kwargs = mock_cx.call_args
    assert "partition_on" not in kwargs
    assert "partition_num" not in kwargs
    assert "partition_range" not in kwargs
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ValueError: Source 'sql' not registered`

- [ ] **Step 3: Implement the scaffold**

```python
# src/siphon/plugins/sources/sql.py
import os
import ipaddress
import logging
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse, parse_qs

import connectorx as cx
import pyarrow as pa

from siphon.models import SQLSourceConfig, mask_uri
from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source
from siphon import variables

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = int(os.getenv("SIPHON_CONNECT_TIMEOUT", "30"))
_ALLOWED_HOSTS = os.getenv("SIPHON_ALLOWED_HOSTS", "")


@register("sql")
class SQLSource(Source):
    def __init__(
        self,
        connection: str,
        query: str,
        partition_on: str | None = None,
        partition_num: int | None = None,
        partition_range: tuple[int, int] | None = None,
    ) -> None:
        self.connection = connection
        self.query = query
        self.partition_on = partition_on
        self.partition_num = partition_num
        self.partition_range = partition_range

    @classmethod
    def from_config(cls, config: SQLSourceConfig) -> "SQLSource":
        return cls(
            connection=config.connection,
            query=config.query,
            partition_on=config.partition_on,
            partition_num=config.partition_num,
            partition_range=config.partition_range,
        )

    def extract(self) -> pa.Table:
        _validate_host(self.connection)
        query = variables.resolve(self.query)

        if self.connection.startswith("oracle"):
            logger.info("Extracting Oracle from %s", mask_uri(self.connection))
            return self._extract_oracle(query)

        conn = _inject_timeout(self.connection)
        logger.info("Extracting from %s", mask_uri(conn))
        return self._extract_connectorx(conn, query)

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        yield self.extract()

    def _extract_connectorx(self, conn: str, query: str) -> pa.Table:
        kwargs: dict = {"return_type": "arrow"}
        if self.partition_on is not None:
            kwargs["partition_on"] = self.partition_on
        if self.partition_num is not None:
            kwargs["partition_num"] = self.partition_num
        if self.partition_range is not None:
            kwargs["partition_range"] = self.partition_range
        return cx.read_sql(conn, query, **kwargs)

    # Oracle path defined in Task 3
    def _extract_oracle(self, query: str) -> pa.Table:  # pragma: no cover
        raise NotImplementedError("Oracle path not yet implemented")

    def _oracle_dsn(self) -> str:  # pragma: no cover
        raise NotImplementedError("Oracle DSN parsing not yet implemented")


def _inject_timeout(conn: str) -> str:
    """Inject connect_timeout into connection string if not present."""
    parsed = urlparse(conn)
    params = parse_qs(parsed.query)
    if "connect_timeout" not in params and "connectTimeout" not in params:
        separator = "&" if parsed.query else ""
        new_query = f"{parsed.query}{separator}connect_timeout={_CONNECT_TIMEOUT}"
        return urlunparse(parsed._replace(query=new_query))
    return conn


def _validate_host(conn: str) -> None:
    """Reject connections to hosts outside the allowlist. Mitigates SSRF."""
    if not _ALLOWED_HOSTS:
        return

    host = urlparse(conn).hostname
    allowed = [h.strip() for h in _ALLOWED_HOSTS.split(",")]

    for entry in allowed:
        try:
            network = ipaddress.ip_network(entry, strict=False)
            if ipaddress.ip_address(host) in network:
                return
        except ValueError:
            if host == entry or host.endswith(f".{entry}"):
                return

    raise ValueError(f"Host '{host}' not in SIPHON_ALLOWED_HOSTS. Connection rejected.")
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py::test_sql_source_is_registered tests/test_sql_source.py::test_extract_connectorx_passes_partition_args tests/test_sql_source.py::test_extract_connectorx_no_partition -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
cd /home/lucasnvk/projects/siphon && git add src/siphon/plugins/sources/sql.py tests/test_sql_source.py && git commit -m "feat: add SQLSource scaffold with ConnectorX path"
```

---

### Task 2: Variable resolution, timeout injection, host validation

**Files:**
- Modify: `tests/test_sql_source.py` (add new tests)

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_sql_source.py

# ── Variable resolution ───────────────────────────────────────────────────────

def test_variable_resolution_applied_before_query():
    """@TODAY is resolved before the query is sent to ConnectorX."""
    table = pa.table({"n": [1]})

    with patch("connectorx.read_sql", return_value=table) as mock_cx, \
         patch("siphon.variables.resolve", wraps=lambda q: q.replace("@TODAY", "'2025-01-15'")) as mock_resolve:
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@host/db", query="SELECT * WHERE dt = @TODAY")
        src.extract()

    mock_resolve.assert_called_once_with("SELECT * WHERE dt = @TODAY")
    call_args = mock_cx.call_args
    assert "'2025-01-15'" in call_args[0][1]  # second positional arg is the query


# ── Timeout injection ─────────────────────────────────────────────────────────

def test_inject_timeout_adds_param_when_absent():
    from siphon.plugins.sources.sql import _inject_timeout
    result = _inject_timeout("mysql://user:pass@host/db")
    assert "connect_timeout=" in result


def test_inject_timeout_skips_when_present():
    from siphon.plugins.sources.sql import _inject_timeout
    conn = "mysql://user:pass@host/db?connect_timeout=10"
    assert _inject_timeout(conn) == conn


def test_inject_timeout_preserves_existing_params():
    from siphon.plugins.sources.sql import _inject_timeout
    conn = "mysql://user:pass@host/db?charset=utf8"
    result = _inject_timeout(conn)
    assert "charset=utf8" in result
    assert "connect_timeout=" in result


# ── Host validation ───────────────────────────────────────────────────────────

def test_validate_host_passes_when_no_allowlist(monkeypatch):
    from siphon.plugins.sources import sql as sql_mod
    monkeypatch.setattr(sql_mod, "_ALLOWED_HOSTS", "")
    sql_mod._validate_host("mysql://u:p@any-host/db")  # must not raise


def test_validate_host_passes_for_allowed_hostname(monkeypatch):
    from siphon.plugins.sources import sql as sql_mod
    monkeypatch.setattr(sql_mod, "_ALLOWED_HOSTS", "db.internal.example.com")
    sql_mod._validate_host("mysql://u:p@db.internal.example.com/db")  # must not raise


def test_validate_host_rejects_disallowed_host(monkeypatch):
    from siphon.plugins.sources import sql as sql_mod
    monkeypatch.setattr(sql_mod, "_ALLOWED_HOSTS", "safe-host.internal")
    with pytest.raises(ValueError, match="not in SIPHON_ALLOWED_HOSTS"):
        sql_mod._validate_host("mysql://u:p@evil.attacker.com/db")


def test_validate_host_allows_ip_cidr(monkeypatch):
    from siphon.plugins.sources import sql as sql_mod
    monkeypatch.setattr(sql_mod, "_ALLOWED_HOSTS", "10.0.0.0/8")
    sql_mod._validate_host("mysql://u:p@10.1.2.3/db")  # must not raise


def test_validate_host_rejects_ip_outside_cidr(monkeypatch):
    from siphon.plugins.sources import sql as sql_mod
    monkeypatch.setattr(sql_mod, "_ALLOWED_HOSTS", "10.0.0.0/8")
    with pytest.raises(ValueError, match="not in SIPHON_ALLOWED_HOSTS"):
        sql_mod._validate_host("mysql://u:p@192.168.1.1/db")


# ── Credential masking in repr ────────────────────────────────────────────────

def test_mask_uri_hides_credentials():
    from siphon.models import mask_uri
    masked = mask_uri("mysql://admin:S3cr3t@db:3306/prod")
    assert "S3cr3t" not in masked
    assert "admin" not in masked
    assert "db:3306/prod" in masked
    assert "***:***" in masked
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py -k "timeout or host or variable or mask" -v 2>&1 | head -40
```

Expected: most fail (functions exist but tests are new and specific)

- [ ] **Step 3: Verify implementation covers all cases**

The `_inject_timeout` and `_validate_host` functions were added in Task 1. Check if all test cases pass as-is. If any fail, fix the implementation:

- `_inject_timeout` must preserve existing query params when appending (use `&` separator)
- `_validate_host` must handle both hostnames and IP CIDRs
- Variable resolution: `variables.resolve(self.query)` must be called before `cx.read_sql`

- [ ] **Step 4: Run all new tests**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py -v
```

Expected: all tests pass (skip the Oracle ones marked `xfail` or `NotImplementedError`)

- [ ] **Step 5: Commit**

```bash
cd /home/lucasnvk/projects/siphon && git add tests/test_sql_source.py && git commit -m "test: add variable resolution, timeout injection, and host validation tests"
```

---

### Task 3: Oracle path — `_extract_oracle()` and `_oracle_dsn()`

**Files:**
- Modify: `src/siphon/plugins/sources/sql.py`
- Modify: `tests/test_sql_source.py`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_sql_source.py

# ── Oracle path ───────────────────────────────────────────────────────────────

def test_oracle_dsn_parsing():
    """_oracle_dsn returns Easy Connect string with embedded credentials."""
    cls = get_source("sql")
    src = cls(connection="oracle+oracledb://scott:tiger@orahost:1521/ORCL", query="SELECT 1 FROM DUAL")
    dsn = src._oracle_dsn()
    assert dsn == "scott/tiger@orahost:1521/ORCL"


def test_extract_routes_oracle_connection():
    """Connections starting with 'oracle' go to _extract_oracle, not ConnectorX."""
    table = pa.table({"x": [1]})

    cls = get_source("sql")
    src = cls(connection="oracle+oracledb://u:p@host:1521/SVC", query="SELECT 1 FROM DUAL")

    with patch.object(src, "_extract_oracle", return_value=table) as mock_oracle, \
         patch("connectorx.read_sql") as mock_cx:
        result = src.extract()

    mock_oracle.assert_called_once_with("SELECT 1 FROM DUAL")  # resolved query passed
    mock_cx.assert_not_called()
    assert result is table


def test_extract_oracle_without_partition():
    """Oracle extraction without partition_num calls pd.read_sql once, no chunksize."""
    from siphon.plugins.sources.sql import _CONNECT_TIMEOUT

    table = pa.table({"n": [1, 2]})
    mock_df = MagicMock()

    cls = get_source("sql")
    src = cls(connection="oracle+oracledb://u:p@host:1521/SVC", query="SELECT n FROM t")

    with patch("oracledb.connect") as mock_connect, \
         patch("pandas.read_sql", return_value=mock_df) as mock_read_sql, \
         patch("pyarrow.Table.from_pandas", return_value=table) as mock_from_pandas:
        result = src._extract_oracle("SELECT n FROM t")

    # tcp_connect_timeout must be forwarded
    assert mock_connect.call_args.kwargs.get("tcp_connect_timeout") == _CONNECT_TIMEOUT
    mock_read_sql.assert_called_once()
    # chunksize must NOT be passed when partition_num is None
    call_kwargs = mock_read_sql.call_args.kwargs
    assert "chunksize" not in call_kwargs
    mock_from_pandas.assert_called_once_with(mock_df, preserve_index=False)
    assert result is table


def test_extract_oracle_with_partition_uses_chunksize():
    """Oracle extraction with partition_num > 1 uses pd.read_sql with chunksize=100_000."""
    chunk1 = MagicMock()
    chunk2 = MagicMock()
    t1 = pa.table({"n": [1]})
    t2 = pa.table({"n": [2]})

    cls = get_source("sql")
    src = cls(
        connection="oracle+oracledb://u:p@host:1521/SVC",
        query="SELECT n FROM big_table",
        partition_num=2,
    )

    with patch("oracledb.connect"), \
         patch("pandas.read_sql", return_value=iter([chunk1, chunk2])) as mock_read_sql, \
         patch("pyarrow.Table.from_pandas", side_effect=[t1, t2]), \
         patch("pyarrow.concat_tables", return_value=pa.table({"n": [1, 2]})) as mock_concat:
        result = src._extract_oracle("SELECT n FROM big_table")

    # chunksize=100_000 must be passed for partition_num > 1
    assert mock_read_sql.call_args.kwargs.get("chunksize") == 100_000
    mock_concat.assert_called_once()
    assert result.num_rows == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py -k "oracle" -v 2>&1 | head -30
```

Expected: `NotImplementedError` or failures

- [ ] **Step 3: Implement Oracle path in sql.py**

Replace the stub `_extract_oracle` and `_oracle_dsn` methods with:

```python
def _extract_oracle(self, query: str) -> pa.Table:
    import oracledb
    import pandas as pd

    # oracledb Easy Connect DSN format: "user/pass@host:port/service"
    conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)

    if self.partition_num and self.partition_num > 1:
        chunks = pd.read_sql(query, conn, chunksize=100_000)
        return pa.concat_tables([
            pa.Table.from_pandas(chunk, preserve_index=False)
            for chunk in chunks
        ])
    df = pd.read_sql(query, conn)
    return pa.Table.from_pandas(df, preserve_index=False)

def _oracle_dsn(self) -> str:
    """Build oracledb Easy Connect DSN: user/pass@host:port/service."""
    parsed = urlparse(self.connection)
    port = parsed.port or 1521
    service = parsed.path.lstrip("/")
    return f"{parsed.username}/{parsed.password}@{parsed.hostname}:{port}/{service}"
```

Note: `urlparse` is already imported at the top of the file.

- [ ] **Step 4: Run Oracle tests**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py -k "oracle" -v
```

Expected: 4 Oracle tests PASSED

- [ ] **Step 5: Run full test suite**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest tests/test_sql_source.py -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
cd /home/lucasnvk/projects/siphon && git add src/siphon/plugins/sources/sql.py tests/test_sql_source.py && git commit -m "feat: implement Oracle path with oracledb thin mode"
```

---

### Task 4: Full test suite verification

**Files:**
- No changes — run only

- [ ] **Step 1: Run ruff linter**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/ruff check src/siphon/plugins/sources/sql.py tests/test_sql_source.py
```

Expected: no errors. If there are import-sort (I001) or unused-import (F401) violations, fix them:

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/ruff check --fix src/siphon/plugins/sources/sql.py tests/test_sql_source.py
```

- [ ] **Step 2: Run entire test suite**

```bash
cd /home/lucasnvk/projects/siphon && .venv/bin/pytest -v
```

Expected: all 22+ tests pass (including pre-existing Phase 1 and 2 tests)

- [ ] **Step 3: Commit lint fixes if any**

```bash
cd /home/lucasnvk/projects/siphon && git add -p && git commit -m "style: fix ruff violations in sql.py"
```

Only run this step if step 1 produced errors.

---

## Important Spec Details

- **`extract_batches()`** on `SQLSource` uses the default base implementation (yield the single Arrow table from `extract()`). ConnectorX loads the full result set in memory — there is no streaming API — so the default batch wrapper is correct.
- **Oracle `extract_batches()`** relies on the same default wrapper. The Oracle chunking (chunksize=100_000) is an internal memory optimization during conversion, not streaming to the destination.
- **`_validate_host()` and `_inject_timeout()`** are module-level functions (not methods) so they can be tested in isolation without constructing a `SQLSource`.
- **`_ALLOWED_HOSTS`** is a module-level variable read at import time. Tests must `monkeypatch.setattr(sql_mod, "_ALLOWED_HOSTS", ...)` to override it — do NOT use `os.environ` patching since the variable is captured at import.
- **Credential masking**: `mask_uri()` already exists in `models.py`. Do not redefine it in `sql.py` — import it.
- **Variable resolution**: `extract()` calls `variables.resolve(self.query)` once and passes the resolved `query` string as an argument to both `_extract_connectorx(conn, query)` and `_extract_oracle(query)`. Neither branch re-resolves — no double resolution.
- **`_inject_timeout()`** is only called for the ConnectorX branch. Oracle uses `tcp_connect_timeout=_CONNECT_TIMEOUT` kwarg on `oracledb.connect()` directly.
- **`_oracle_dsn()`** returns an oracledb Easy Connect string: `"user/pass@host:port/service"`. The spec's `oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=...)` requires a single DSN argument.
- **`@register("sql")`** triggers autodiscovery — no changes to `__init__.py` are needed.
- **`from_config()` classmethod**: The main.py `_make_job_and_plugins()` currently does `SourceCls(**config.model_dump(exclude={"type"}))`. The `from_config()` method is a convenience, not required for the factory.
