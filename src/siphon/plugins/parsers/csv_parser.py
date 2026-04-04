import pyarrow as pa
import pyarrow.csv as pacsv

from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser


@register("csv")
class CsvParser(Parser):
    def __init__(self, delimiter: str = ",", encoding: str = "utf-8") -> None:
        self.delimiter = delimiter
        self.encoding = encoding

    def parse(self, data: bytes) -> pa.Table:
        return pacsv.read_csv(
            pa.BufferReader(data),
            parse_options=pacsv.ParseOptions(delimiter=self.delimiter),
            read_options=pacsv.ReadOptions(encoding=self.encoding),
        )
