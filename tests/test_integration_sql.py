"""
Integration tests for SQLSource against a real MySQL instance.

Requires the services in docker-compose.yml to be running:
    docker compose up -d mysql
    docker compose exec mysql mysqladmin ping -h localhost -usiphon -psiphon

Run with:
    pytest tests/test_integration_sql.py -m integration
"""

import pyarrow as pa
import pytest

from siphon.plugins.sources import get as get_source

pytestmark = pytest.mark.integration

MYSQL_CONN = "mysql://siphon:siphon@localhost:3306/testdb"


@pytest.fixture(scope="module")
def mysql_conn():
    """Return MySQL connection string. Skip if MySQL is not reachable."""
    try:
        import connectorx as cx

        cx.read_sql(MYSQL_CONN, "SELECT 1", return_type="arrow")
    except Exception as exc:
        pytest.skip(f"MySQL not available: {exc}")
    return MYSQL_CONN


def test_extract_basic_select(mysql_conn):
    """SQLSource extracts a trivial query and returns an Arrow Table."""
    cls = get_source("sql")
    src = cls(connection=mysql_conn, query="SELECT 1 AS n")
    result = src.extract()

    assert isinstance(result, pa.Table)
    assert result.num_rows == 1
    assert "n" in result.column_names


def test_extract_with_variable_resolution(mysql_conn):
    """@TODAY variable is resolved before query execution."""
    cls = get_source("sql")
    src = cls(
        connection=mysql_conn,
        query="SELECT CAST(@TODAY AS CHAR) AS today_str",
    )
    # If variable substitution works, the query becomes valid SQL.
    result = src.extract()
    assert result.num_rows == 1


def test_extract_batches_yields_table(mysql_conn):
    """extract_batches() yields at least one Arrow Table."""
    cls = get_source("sql")
    src = cls(connection=mysql_conn, query="SELECT 1 AS n")
    batches = list(src.extract_batches())

    assert len(batches) >= 1
    assert all(isinstance(b, pa.Table) for b in batches)
