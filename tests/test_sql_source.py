from unittest.mock import patch

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
