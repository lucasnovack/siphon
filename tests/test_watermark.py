# tests/test_watermark.py
import pytest
from siphon.pipelines.watermark import _cast_for_dialect, inject_watermark


class TestCastForDialect:
    def test_mysql(self):
        expr = _cast_for_dialect("2024-01-01T00:00:00+00:00", "mysql+connectorx")
        assert expr == "CAST('2024-01-01T00:00:00+00:00' AS DATETIME)"

    def test_mysql_plain(self):
        assert "DATETIME" in _cast_for_dialect("2024-01-01", "mysql")

    def test_postgresql(self):
        expr = _cast_for_dialect("2024-01-01T00:00:00+00:00", "postgresql")
        assert expr == "CAST('2024-01-01T00:00:00+00:00' AS TIMESTAMPTZ)"

    def test_postgres_alias(self):
        assert "TIMESTAMPTZ" in _cast_for_dialect("2024-01-01", "postgres")

    def test_oracle(self):
        expr = _cast_for_dialect("2024-01-01T00:00:00+00:00", "oracle")
        assert "TIMESTAMP WITH TIME ZONE" in expr

    def test_mssql(self):
        expr = _cast_for_dialect("2024-01-01T00:00:00+00:00", "mssql+connectorx")
        assert "DATETIMEOFFSET" in expr

    def test_unknown_dialect_defaults_to_timestamptz(self):
        expr = _cast_for_dialect("2024-01-01", "redshift")
        assert "TIMESTAMPTZ" in expr

    def test_single_quote_in_watermark_is_escaped(self):
        expr = _cast_for_dialect("2024-01-01T00:00:00'bad", "postgresql")
        assert "''" in expr
        assert "bad" in expr


class TestInjectWatermark:
    BASE = "SELECT id, updated_at FROM orders"
    WM = "2024-06-01T00:00:00+00:00"

    def test_wraps_in_cte(self):
        result = inject_watermark(self.BASE, "updated_at", self.WM, "postgresql")
        assert "WITH _siphon_base AS (" in result
        assert self.BASE in result
        assert "SELECT * FROM _siphon_base" in result

    def test_where_clause_uses_key(self):
        result = inject_watermark(self.BASE, "updated_at", self.WM, "mysql")
        assert "WHERE updated_at >" in result

    def test_works_with_existing_cte(self):
        query = "WITH ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY id) rn FROM t) SELECT * FROM ranked WHERE rn=1"
        result = inject_watermark(query, "ts", self.WM, "postgresql")
        # Original CTE is preserved inside the wrapper CTE
        assert "WITH ranked AS" in result
        assert "WHERE ts >" in result

    def test_dialect_specific_cast_used(self):
        pg = inject_watermark(self.BASE, "ts", self.WM, "postgresql")
        my = inject_watermark(self.BASE, "ts", self.WM, "mysql")
        assert "TIMESTAMPTZ" in pg
        assert "DATETIME" in my

    def test_full_output_is_valid_sql_structure(self):
        result = inject_watermark(self.BASE, "updated_at", self.WM, "postgresql")
        lines = result.strip().splitlines()
        assert lines[0].startswith("WITH _siphon_base AS (")
        assert any("SELECT * FROM _siphon_base" in l for l in lines)
        assert any("WHERE updated_at >" in l for l in lines)
