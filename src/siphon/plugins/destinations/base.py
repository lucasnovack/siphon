# src/siphon/plugins/destinations/base.py
from abc import ABC, abstractmethod

import pyarrow as pa


class Destination(ABC):
    """Base class for all data destination plugins.

    Plugins receive their configuration via __init__ (unpacked from the Pydantic model).
    No I/O must happen in __init__ — connection setup happens inside write().
    """

    @abstractmethod
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        """Write Arrow Table to destination. Returns number of rows written.

        Args:
            table: Arrow Table to write.
            is_first_chunk: If True, overwrite existing data at the destination path
                            (delete_matching semantics). If False, append to existing
                            data from this job (overwrite_or_ignore semantics).
        """
