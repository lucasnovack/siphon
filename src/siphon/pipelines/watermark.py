# src/siphon/pipelines/watermark.py
"""Type-aware watermark injection for incremental extraction.

Wraps the original query in a CTE so the WHERE clause works regardless
of whether the query already has its own WHERE, GROUP BY, ORDER BY, etc.
"""
import re

# Only plain SQL identifiers are allowed as watermark keys (letters, digits, underscores).
# Dots are permitted for schema-qualified names (e.g. "schema.column").
# This prevents SQL injection via the incremental_key field.
_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _validate_key(key: str) -> None:
    """Validate that key is a valid SQL identifier."""
    if not _VALID_IDENTIFIER.match(key):
        raise ValueError(
            f"Invalid incremental_key {key!r}: must be a plain SQL identifier "
            "(letters, digits, underscores, dots only — no spaces or special characters)."
        )


def inject_watermark(query: str, key: str, watermark: str, dialect: str) -> str:
    """Return a new SQL string that filters rows newer than *watermark*.

    Args:
        query:     Original SQL (may contain CTEs, subqueries, ORDER BY, …).
        key:       Column name used as the incremental cursor (e.g. "updated_at").
        watermark: ISO-8601 UTC string stored in ``pipelines.last_watermark``.
        dialect:   Connection-string prefix (e.g. "mysql+connectorx", "postgresql").

    Returns:
        SQL string: ``WITH _siphon_base AS (<query>) SELECT * … WHERE key > cast``.
    """
    _validate_key(key)
    cast_expr = _cast_for_dialect(watermark, dialect)
    return (
        f"WITH _siphon_base AS (\n"
        f"{query}\n"
        f")\n"
        f"SELECT * FROM _siphon_base\n"
        f"WHERE {key} > {cast_expr}"
    )


def inject_backfill_window(
    query: str, key: str, from_dt: str, to_dt: str, dialect: str
) -> str:
    """Return SQL that filters rows in the half-open interval [from_dt, to_dt).

    Uses a CTE wrapper identical to inject_watermark.  The upper bound is
    exclusive (<) so contiguous windows tile without overlap.

    Args:
        query:     Original SQL (may contain CTEs, subqueries, ORDER BY, …).
        key:       Column name used as the incremental cursor (e.g. "updated_at").
        from_dt:   ISO-8601 UTC start time (inclusive, >=).
        to_dt:     ISO-8601 UTC end time (exclusive, <).
        dialect:   Connection-string prefix (e.g. "mysql+connectorx", "postgresql").

    Returns:
        SQL string: ``WITH _siphon_base AS (<query>) SELECT * … WHERE key >= from AND key < to``.
    """
    _validate_key(key)
    cast_from = _cast_for_dialect(from_dt, dialect)
    cast_to = _cast_for_dialect(to_dt, dialect)
    return (
        f"WITH _siphon_base AS (\n"
        f"{query}\n"
        f")\n"
        f"SELECT * FROM _siphon_base\n"
        f"WHERE {key} >= {cast_from}\n"
        f"  AND {key} < {cast_to}"
    )


def _cast_for_dialect(watermark: str, dialect: str) -> str:
    """Return a dialect-specific CAST expression for *watermark*."""
    d = dialect.lower()
    # Escape single quotes inside the watermark value (defensive)
    safe = watermark.replace("'", "''")
    if "mysql" in d:
        return f"CAST('{safe}' AS DATETIME)"
    if "oracle" in d:
        return f"CAST('{safe}' AS TIMESTAMP WITH TIME ZONE)"
    if "mssql" in d or "sqlserver" in d:
        return f"CAST('{safe}' AS DATETIMEOFFSET)"
    # postgresql, default (covers "postgresql", "postgres", "redshift", …)
    return f"CAST('{safe}' AS TIMESTAMPTZ)"
