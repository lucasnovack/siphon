# src/siphon/plugins/sources/base.py
from abc import ABC, abstractmethod
from collections.abc import Iterator

import pyarrow as pa


class Source(ABC):
    """Base class for all data source plugins.

    Plugins receive their configuration via __init__ (unpacked from the Pydantic model).
    No I/O must happen in __init__ — connection setup happens inside extract() or extract_batches().
    """

    @abstractmethod
    def extract(self) -> pa.Table:
        """Read from source and return a single Arrow Table.

        Use for sources where the full dataset fits comfortably in memory.
        For large sources, prefer overriding extract_batches() instead.
        """

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        """Stream data as batches of Arrow Tables.

        Default implementation wraps extract() into a single batch.
        Override this for memory-efficient extraction of large datasets.
        The worker calls write() incrementally for each yielded batch.
        """
        yield self.extract()
