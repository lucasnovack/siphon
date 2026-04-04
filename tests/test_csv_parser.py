import pyarrow as pa
import pytest

from siphon.plugins.parsers import get as get_parser


def _get_csv():
    return get_parser("csv")


def test_csv_is_registered():
    cls = _get_csv()
    assert cls.__name__ == "CsvParser"


def test_parse_basic_csv():
    data = b"id,name,score\n1,Alice,9.5\n2,Bob,8.0\n"
    table = _get_csv()().parse(data)
    assert table.num_rows == 2
    assert table.schema.names == ["id", "name", "score"]
    assert table.column("name").to_pylist() == ["Alice", "Bob"]


def test_parse_semicolon_delimiter():
    data = b"id;name\n1;Alice\n2;Bob\n"
    table = _get_csv()(delimiter=";").parse(data)
    assert table.num_rows == 2
    assert table.schema.names == ["id", "name"]


def test_parse_latin1_encoding():
    data = "id,cidade\n1,S\xe3o Paulo\n".encode("latin-1")
    table = _get_csv()(encoding="latin-1").parse(data)
    assert table.num_rows == 1
    assert table.column("cidade").to_pylist() == ["São Paulo"]


def test_parse_empty_csv_raises():
    with pytest.raises(Exception):
        _get_csv()().parse(b"")
