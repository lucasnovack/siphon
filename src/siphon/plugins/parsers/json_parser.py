import json

import pyarrow as pa
import pyarrow.json as pajson

from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser


@register("json")
class JsonParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        """Parse JSON or JSONL data into an Arrow Table.

        Supports:
        - JSON arrays: [{"col": val}, ...]
        - Line-delimited JSON (JSONL): {"col": val}\n{"col": val}\n...
        """
        text = data.decode("utf-8").strip()
        if not text:
            raise ValueError("Empty data cannot be parsed as JSON")

        # Try to detect format: JSON array vs JSONL
        if text.startswith("["):
            # JSON array format: parse as JSON array and convert to table
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError("Expected JSON array or JSONL format")
            if not parsed:
                raise ValueError("Empty JSON array cannot be parsed")
            # Convert list of dicts to Arrow table
            return pa.Table.from_pylist(parsed)
        else:
            # JSONL format: use PyArrow's native reader
            return pajson.read_json(pa.BufferReader(data))
