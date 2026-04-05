# src/siphon/plugins/parsers/avro_parser.py
import io

import fastavro
import pyarrow as pa

from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser


@register("avro")
class AvroParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        if not data:
            raise ValueError("Empty Avro file cannot be parsed")
        reader = fastavro.reader(io.BytesIO(data))
        records = list(reader)
        if not records:
            raise ValueError("Empty Avro file cannot be parsed")
        return pa.Table.from_pylist(records)
