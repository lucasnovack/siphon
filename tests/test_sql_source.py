from datetime import datetime
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

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


# ── Variable resolution ───────────────────────────────────────────────────────


def test_variable_resolution_applied_before_query():
    """variables.resolve() is called on the raw query before passing to cx.read_sql."""
    table = pa.table({"dt": ["2026-03-25"]})
    resolved_query = "SELECT * FROM t WHERE dt = '2026-03-25'"

    with (
        patch("connectorx.read_sql", return_value=table) as mock_cx,
        patch("siphon.variables.resolve", return_value=resolved_query) as mock_resolve,
    ):
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@host/db", query="SELECT * FROM t WHERE dt = @TODAY")
        src.extract()

    mock_resolve.assert_called_once_with("SELECT * FROM t WHERE dt = @TODAY")
    actual_query = mock_cx.call_args[0][1]
    assert actual_query == resolved_query


# ── Host validation ───────────────────────────────────────────────────────────


def test_host_validation_rejects_disallowed_host():
    """Connection is rejected when host is not in SIPHON_ALLOWED_HOSTS."""
    import siphon.plugins.sources.sql as sql_mod

    with patch.object(sql_mod, "_ALLOWED_HOSTS", "allowed.internal"):
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@evil.host/db", query="SELECT 1")
        with pytest.raises(ValueError, match="not in SIPHON_ALLOWED_HOSTS"):
            src.extract()


def test_host_validation_allows_listed_host():
    """Connection proceeds when host is in SIPHON_ALLOWED_HOSTS."""
    import siphon.plugins.sources.sql as sql_mod

    table = pa.table({"x": [1]})
    with (
        patch.object(sql_mod, "_ALLOWED_HOSTS", "allowed.internal"),
        patch("connectorx.read_sql", return_value=table),
    ):
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@allowed.internal/db", query="SELECT 1")
        result = src.extract()

    assert result.num_rows == 1


def test_host_validation_allows_cidr_range():
    """CIDR notation in SIPHON_ALLOWED_HOSTS is accepted."""
    import siphon.plugins.sources.sql as sql_mod

    table = pa.table({"x": [1]})
    with (
        patch.object(sql_mod, "_ALLOWED_HOSTS", "10.0.0.0/8"),
        patch("connectorx.read_sql", return_value=table),
    ):
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@10.1.2.3/db", query="SELECT 1")
        result = src.extract()

    assert result.num_rows == 1


def test_host_validation_skipped_when_allowlist_empty():
    """No allowlist configured → all hosts pass (permissive dev mode)."""
    import siphon.plugins.sources.sql as sql_mod

    table = pa.table({"x": [1]})
    with (
        patch.object(sql_mod, "_ALLOWED_HOSTS", ""),
        patch("connectorx.read_sql", return_value=table),
    ):
        cls = get_source("sql")
        src = cls(connection="mysql://u:p@any.host/db", query="SELECT 1")
        result = src.extract()

    assert result.num_rows == 1


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


def _make_mock_oracledb():
    mock = MagicMock()
    mock.DB_TYPE_NUMBER = "DB_TYPE_NUMBER"
    mock.DB_TYPE_DATE = "DB_TYPE_DATE"
    mock.DB_TYPE_TIMESTAMP = "DB_TYPE_TIMESTAMP"
    mock.DB_TYPE_CLOB = "DB_TYPE_CLOB"
    mock.DB_TYPE_BLOB = "DB_TYPE_BLOB"
    return mock


def _make_mock_cursor(row_batches, description):
    cursor = MagicMock()
    cursor.description = description
    cursor.fetchmany.side_effect = [*row_batches, []]
    cursor.__enter__ = lambda s: s
    cursor.__exit__ = MagicMock(return_value=False)
    return cursor


def _make_mock_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_oracle_rows_to_arrow_infers_float64():
    from siphon.plugins.sources.sql import _oracle_rows_to_arrow

    rows = [(1.5,), (2.7,)]
    description = [("PRICE", None, None, None, None, None, None)]
    table = _oracle_rows_to_arrow(rows, description)

    assert table.schema.field("price").type == pa.float64()


# ── Oracle streaming (extract_batches) ────────────────────────────────────────


def test_oracle_yields_chunks():
    """extract_batches yields one pa.Table per fetchmany batch until exhausted."""
    rows_batch = [(i, f"name{i}") for i in range(10)]
    description = [
        ("ID", None, None, None, None, None, None),
        ("NAME", None, None, None, None, None, None),
    ]
    mock_oracledb = _make_mock_oracledb()
    cursor = _make_mock_cursor([rows_batch, rows_batch, rows_batch], description)
    mock_oracledb.connect.return_value = _make_mock_conn(cursor)

    with patch.dict("sys.modules", {"oracledb": mock_oracledb}):
        cls = get_source("sql")
        src = cls(
            connection="oracle+oracledb://user:pass@host:1521/svc",
            query="SELECT id, name FROM t",
        )
        chunks = list(src.extract_batches())

    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk.num_rows == 10


def test_oracle_number_types():
    """NUMBER(p,0) maps to int64 and NUMBER(p,s>0) maps to float64 in Arrow."""
    rows = [(1, 9.99)]
    description = [
        ("ID", None, None, None, None, None, None),
        ("PRICE", None, None, None, None, None, None),
    ]
    mock_oracledb = _make_mock_oracledb()
    cursor = _make_mock_cursor([rows], description)
    mock_oracledb.connect.return_value = _make_mock_conn(cursor)

    with patch.dict("sys.modules", {"oracledb": mock_oracledb}):
        cls = get_source("sql")
        src = cls(
            connection="oracle+oracledb://user:pass@host:1521/svc",
            query="SELECT id, price FROM t",
        )
        chunks = list(src.extract_batches())

    schema = chunks[0].schema
    assert schema.field("id").type == pa.int64()
    assert schema.field("price").type == pa.float64()


def test_oracle_date_type():
    """DATE columns returned as datetime objects yield pa.timestamp('us') in Arrow."""
    rows = [(datetime(2026, 3, 28, 10, 0, 0),)]
    description = [("CREATED_AT", None, None, None, None, None, None)]
    mock_oracledb = _make_mock_oracledb()
    cursor = _make_mock_cursor([rows], description)
    mock_oracledb.connect.return_value = _make_mock_conn(cursor)

    with patch.dict("sys.modules", {"oracledb": mock_oracledb}):
        cls = get_source("sql")
        src = cls(
            connection="oracle+oracledb://user:pass@host:1521/svc",
            query="SELECT created_at FROM t",
        )
        chunks = list(src.extract_batches())

    assert chunks[0].schema.field("created_at").type == pa.timestamp("us")


def test_oracle_clob_emits_warning():
    """_oracle_output_type_handler logs a warning when it encounters a CLOB column."""
    import siphon.plugins.sources.sql as sql_mod

    mock_oracledb = _make_mock_oracledb()
    mock_cursor = MagicMock()

    with patch.dict("sys.modules", {"oracledb": mock_oracledb}), patch.object(
        sql_mod.logger, "warning"
    ) as mock_warn:
        sql_mod._oracle_output_type_handler(
            mock_cursor, "NOTES", mock_oracledb.DB_TYPE_CLOB, None, None, None
        )

    mock_warn.assert_called_once()


def test_oracle_empty_result():
    """When fetchmany returns [] immediately, extract_batches yields nothing."""
    description = [("ID", None, None, None, None, None, None)]
    mock_oracledb = _make_mock_oracledb()
    cursor = _make_mock_cursor([], description)
    mock_oracledb.connect.return_value = _make_mock_conn(cursor)

    with patch.dict("sys.modules", {"oracledb": mock_oracledb}):
        cls = get_source("sql")
        src = cls(
            connection="oracle+oracledb://user:pass@host:1521/svc",
            query="SELECT id FROM empty_table",
        )
        chunks = list(src.extract_batches())

    assert chunks == []
