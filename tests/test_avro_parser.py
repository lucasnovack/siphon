# tests/test_avro_parser.py
import io

import fastavro
import pyarrow as pa
import pytest

from siphon.plugins.parsers import get as get_parser

_SCHEMA = fastavro.parse_schema({
    "type": "record",
    "name": "TestRecord",
    "fields": [
        {"name": "id", "type": "int"},
        {"name": "name", "type": "string"},
    ],
})


def _make_avro_bytes(records: list[dict]) -> bytes:
    buf = io.BytesIO()
    fastavro.writer(buf, _SCHEMA, records)
    return buf.getvalue()


def test_avro_is_registered():
    cls = get_parser("avro")
    assert cls.__name__ == "AvroParser"


def test_parse_basic_avro():
    data = _make_avro_bytes([{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}])
    table = get_parser("avro")().parse(data)
    assert table.num_rows == 2
    assert set(table.schema.names) == {"id", "name"}
    assert table.column("name").to_pylist() == ["Alice", "Bob"]


def test_parse_single_record():
    data = _make_avro_bytes([{"id": 99, "name": "Solo"}])
    table = get_parser("avro")().parse(data)
    assert table.num_rows == 1
    assert table.column("id").to_pylist() == [99]


def test_parse_empty_avro_raises():
    with pytest.raises(ValueError, match="Empty Avro"):
        get_parser("avro")().parse(b"")
