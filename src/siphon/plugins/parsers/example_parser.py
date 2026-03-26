import pyarrow as pa

from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser


@register("example_parser")
class ExampleParser(Parser):
    """Stub parser: returns raw bytes as a single-column Arrow Table.
    Replace with real parsing logic for specific binary formats.
    """

    def parse(self, data: bytes) -> pa.Table:
        return pa.table({"raw": pa.array([data], type=pa.large_binary())})
