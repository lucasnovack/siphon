"""Tests for _schema_to_dict and _check_schema in worker.py."""
import pyarrow as pa
import pytest


def test_schema_to_dict_round_trip():
    """_schema_to_dict converts Arrow schema to list of dicts preserving name, type, nullable."""
    from siphon.worker import _schema_to_dict

    schema = pa.schema([
        pa.field("id", pa.int64(), nullable=False),
        pa.field("name", pa.string(), nullable=True),
        pa.field("score", pa.float64(), nullable=True),
    ])
    result = _schema_to_dict(schema)

    assert result == [
        {"name": "id", "type": "int64", "nullable": False},
        {"name": "name", "type": "string", "nullable": True},
        {"name": "score", "type": "double", "nullable": True},
    ]


def test_schema_to_dict_empty_schema():
    """Empty schema returns empty list."""
    from siphon.worker import _schema_to_dict

    result = _schema_to_dict(pa.schema([]))
    assert result == []


def test_check_schema_passes_when_expected_matches():
    """_check_schema returns None when actual schema satisfies expected."""
    from siphon.worker import _check_schema

    actual = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("name", pa.string()),
    ])
    expected = [
        {"name": "id", "type": "int64"},
        {"name": "name", "type": "string"},
    ]
    assert _check_schema(actual, expected) is None


def test_check_schema_error_on_missing_column():
    """_check_schema returns error string when expected column is absent from actual."""
    from siphon.worker import _check_schema

    actual = pa.schema([pa.field("id", pa.int64())])
    expected = [{"name": "id", "type": "int64"}, {"name": "name", "type": "string"}]

    result = _check_schema(actual, expected)
    assert result is not None
    assert "name" in result


def test_check_schema_error_on_type_mismatch():
    """_check_schema returns error string when column type differs from expected."""
    from siphon.worker import _check_schema

    actual = pa.schema([pa.field("id", pa.string())])  # string, not int64
    expected = [{"name": "id", "type": "int64"}]

    result = _check_schema(actual, expected)
    assert result is not None
    assert "int64" in result


def test_check_schema_warning_only_for_extra_column():
    """_check_schema returns None (no error) when actual has extra columns not in expected."""
    from siphon.worker import _check_schema

    actual = pa.schema([
        pa.field("id", pa.int64()),
        pa.field("extra_col", pa.string()),  # not in expected
    ])
    expected = [{"name": "id", "type": "int64"}]

    # Extra columns are a warning, not an error
    assert _check_schema(actual, expected) is None
