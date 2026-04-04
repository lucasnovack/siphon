import logging
import os
import uuid

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from siphon.plugins.destinations import register
from siphon.plugins.destinations.base import Destination

logger = logging.getLogger(__name__)

_ALLOWED_PREFIX = os.getenv("SIPHON_ALLOWED_S3_PREFIX", "bronze/")


@register("s3_parquet")
class S3ParquetDestination(Destination):
    def __init__(
        self,
        path: str,
        endpoint: str,
        access_key: str,
        secret_key: str,
        compression: str = "snappy",
        extraction_mode: str = "full_refresh",
    ) -> None:
        _validate_path(path)
        self.path = path
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.compression = compression
        self.extraction_mode = extraction_mode

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        fs = pafs.S3FileSystem(
            endpoint_override=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            scheme=os.getenv("SIPHON_S3_SCHEME", "https"),
        )
        root_path = self.path.replace("s3a://", "").replace("s3://", "")
        if self.extraction_mode == "incremental":
            behavior = "overwrite_or_ignore"
            # Unique basename so each incremental run appends new files
            # instead of overwriting the previous run's part-0.parquet
            basename_template = f"part-{uuid.uuid4().hex}-{{i}}.parquet"
        else:
            behavior = "delete_matching" if is_first_chunk else "overwrite_or_ignore"
            basename_template = "part-{i}.parquet"
        logger.info(
            "Writing %d rows to %s (mode=%s, behavior=%s)",
            table.num_rows, self.path, self.extraction_mode, behavior,
        )
        pq.write_to_dataset(
            table,
            root_path=root_path,
            filesystem=fs,
            compression=self.compression,
            existing_data_behavior=behavior,
            basename_template=basename_template,
        )
        return table.num_rows


def _validate_path(path: str) -> None:
    """Reject paths with traversal or outside the allowed S3 prefix.

    Called in __init__ so validation fails before any I/O is attempted.
    URL-decodes the path before checking to catch encoded traversal sequences
    like %2e%2e or %2F..%2F.
    """
    from urllib.parse import unquote
    stripped = path.replace("s3a://", "").replace("s3://", "")
    normalized = unquote(stripped)
    if ".." in normalized:
        raise ValueError(f"Path traversal detected in destination path: {path!r}")
    if not normalized.startswith(_ALLOWED_PREFIX):
        raise ValueError(
            f"Destination path {path!r} is outside allowed prefix {_ALLOWED_PREFIX!r}. "
            f"Set SIPHON_ALLOWED_S3_PREFIX to change."
        )
