# tests/test_snowflake_destination.py
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from siphon.plugins.destinations import get as get_dest


def _make_mock_snowflake():
    mock_sf = MagicMock()
    mock_conn = MagicMock()
    mock_sf.connect.return_value = mock_conn
    return mock_sf, mock_conn


def _sf_ctx(mock_sf, mock_pandas_tools):
    return patch.dict("sys.modules", {
        "snowflake": MagicMock(),
        "snowflake.connector": mock_sf,
        "snowflake.connector.pandas_tools": mock_pandas_tools,
    })


def _dest(**kwargs):
    cls = get_dest("snowflake")
    defaults = {
        "account": "acct.us-east-1",
        "user": "svc_user",
        "password": "s3cr3t",
        "database": "PROD",
        "schema": "PUBLIC",
        "warehouse": "COMPUTE_WH",
        "table": "orders",
        "write_mode": "append",
    }
    defaults.update(kwargs)
    return cls(**defaults)


# ── Registry ──────────────────────────────────────────────────────────────────


def test_snowflake_is_registered():
    cls = get_dest("snowflake")
    assert cls.__name__ == "SnowflakeDestination"


# ── Write modes ───────────────────────────────────────────────────────────────


def test_write_append_uses_overwrite_false():
    dest = _dest(write_mode="append")
    table = pa.table({"id": [1, 2], "name": ["a", "b"]})

    mock_sf, mock_conn = _make_mock_snowflake()
    mock_pandas_tools = MagicMock()
    mock_pandas_tools.write_pandas.return_value = (True, 1, 2, [])

    with _sf_ctx(mock_sf, mock_pandas_tools):
        rows = dest.write(table, is_first_chunk=True)

    assert rows == 2
    _, kwargs = mock_pandas_tools.write_pandas.call_args
    assert kwargs["overwrite"] is False


def test_write_replace_first_chunk_uses_overwrite_true():
    dest = _dest(write_mode="replace")
    table = pa.table({"id": [1]})

    mock_sf, mock_conn = _make_mock_snowflake()
    mock_pandas_tools = MagicMock()
    mock_pandas_tools.write_pandas.return_value = (True, 1, 1, [])

    with _sf_ctx(mock_sf, mock_pandas_tools):
        rows = dest.write(table, is_first_chunk=True)

    assert rows == 1
    _, kwargs = mock_pandas_tools.write_pandas.call_args
    assert kwargs["overwrite"] is True


def test_write_replace_subsequent_chunk_uses_overwrite_false():
    dest = _dest(write_mode="replace")
    table = pa.table({"id": [1]})

    mock_sf, mock_conn = _make_mock_snowflake()
    mock_pandas_tools = MagicMock()
    mock_pandas_tools.write_pandas.return_value = (True, 1, 1, [])

    with _sf_ctx(mock_sf, mock_pandas_tools):
        rows = dest.write(table, is_first_chunk=False)

    _, kwargs = mock_pandas_tools.write_pandas.call_args
    assert kwargs["overwrite"] is False


def test_write_uses_uppercase_table_name():
    dest = _dest(table="orders")
    table = pa.table({"id": [1]})

    mock_sf, mock_conn = _make_mock_snowflake()
    mock_pandas_tools = MagicMock()
    mock_pandas_tools.write_pandas.return_value = (True, 1, 1, [])

    with _sf_ctx(mock_sf, mock_pandas_tools):
        dest.write(table, is_first_chunk=True)

    args, _ = mock_pandas_tools.write_pandas.call_args
    assert args[1] == "ORDERS"


def test_connection_is_closed_after_write():
    dest = _dest()
    table = pa.table({"id": [1]})

    mock_sf, mock_conn = _make_mock_snowflake()
    mock_pandas_tools = MagicMock()
    mock_pandas_tools.write_pandas.return_value = (True, 1, 1, [])

    with _sf_ctx(mock_sf, mock_pandas_tools):
        dest.write(table, is_first_chunk=True)

    mock_conn.close.assert_called_once()


def test_write_pandas_failure_raises():
    dest = _dest()
    table = pa.table({"id": [1]})

    mock_sf, mock_conn = _make_mock_snowflake()
    mock_pandas_tools = MagicMock()
    mock_pandas_tools.write_pandas.return_value = (False, 0, 0, [])

    with _sf_ctx(mock_sf, mock_pandas_tools), pytest.raises(RuntimeError, match="Snowflake write failed"):
        dest.write(table, is_first_chunk=True)
