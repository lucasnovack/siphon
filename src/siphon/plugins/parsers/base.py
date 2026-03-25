# src/siphon/plugins/parsers/base.py
from abc import ABC, abstractmethod

import pyarrow as pa


class Parser(ABC):
    """Base class for binary file parsers.

    Parsers convert raw bytes (e.g. binary files from SFTP) into Arrow Tables.
    Each parser is registered by name and selected via SFTPSourceConfig.parser.
    """

    @abstractmethod
    def parse(self, data: bytes) -> pa.Table:
        """Convert raw bytes into an Arrow Table.

        Args:
            data: Raw file contents downloaded from SFTP or another binary source.

        Returns:
            Arrow Table with the parsed data. Schema is parser-specific.
        """
