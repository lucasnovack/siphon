# tests/test_abcs.py
import pyarrow as pa
import pytest

from siphon.plugins.sources.base import Source
from siphon.plugins.destinations.base import Destination
from siphon.plugins.parsers.base import Parser


class TestSourceABC:
    def test_cannot_instantiate_source_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            Source()  # type: ignore

    def test_concrete_source_must_implement_extract(self):
        class BadSource(Source):
            pass  # missing extract()
        with pytest.raises(TypeError, match="abstract"):
            BadSource()

    def test_concrete_source_can_be_instantiated(self):
        class GoodSource(Source):
            def extract(self) -> pa.Table:
                return pa.table({"x": [1]})
        src = GoodSource()
        assert isinstance(src, Source)

    def test_extract_batches_default_wraps_extract(self):
        class GoodSource(Source):
            def extract(self) -> pa.Table:
                return pa.table({"x": [1, 2, 3]})
        src = GoodSource()
        batches = list(src.extract_batches())
        assert len(batches) == 1
        assert batches[0].num_rows == 3

    def test_extract_batches_can_be_overridden(self):
        class StreamingSource(Source):
            def extract(self) -> pa.Table:
                return pa.table({"x": [1, 2, 3]})
            def extract_batches(self, chunk_size: int = 100):
                yield pa.table({"x": [1]})
                yield pa.table({"x": [2, 3]})
        src = StreamingSource()
        batches = list(src.extract_batches())
        assert len(batches) == 2


class TestDestinationABC:
    def test_cannot_instantiate_destination_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            Destination()  # type: ignore

    def test_concrete_destination_must_implement_write(self):
        class BadDest(Destination):
            pass
        with pytest.raises(TypeError, match="abstract"):
            BadDest()

    def test_write_receives_is_first_chunk_flag(self):
        received = {}
        class GoodDest(Destination):
            def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
                received["is_first_chunk"] = is_first_chunk
                return len(table)
        dst = GoodDest()
        dst.write(pa.table({"x": [1]}), is_first_chunk=False)
        assert received["is_first_chunk"] is False


class TestParserABC:
    def test_cannot_instantiate_parser_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            Parser()  # type: ignore

    def test_concrete_parser_must_implement_parse(self):
        class BadParser(Parser):
            pass
        with pytest.raises(TypeError, match="abstract"):
            BadParser()

    def test_parse_receives_bytes(self):
        class GoodParser(Parser):
            def parse(self, data: bytes) -> pa.Table:
                return pa.table({"raw": [data]})
        p = GoodParser()
        result = p.parse(b"hello")
        assert result.column("raw")[0].as_py() == b"hello"
