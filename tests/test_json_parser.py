import json

import pyarrow as pa
import pytest

from siphon.plugins.parsers import get as get_parser


def _get_json():
    return get_parser("json")


def test_json_is_registered():
    cls = _get_json()
    assert cls.__name__ == "JsonParser"


def test_parse_json_array():
    records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    data = json.dumps(records).encode()
    table = _get_json()().parse(data)
    assert table.num_rows == 2
    assert set(table.schema.names) == {"id", "name"}
    assert table.column("name").to_pylist() == ["Alice", "Bob"]


def test_parse_jsonl():
    lines = [
        json.dumps({"id": 1, "val": 10}),
        json.dumps({"id": 2, "val": 20}),
    ]
    data = "\n".join(lines).encode()
    table = _get_json()().parse(data)
    assert table.num_rows == 2
    assert table.column("val").to_pylist() == [10, 20]


def test_parse_empty_json_raises():
    with pytest.raises(Exception):
        _get_json()().parse(b"")
