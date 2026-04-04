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
        job_id: str = "",
    ) -> None:
        _validate_path(path)
        self.path = path
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.compression = compression
        self.extraction_mode = extraction_mode
        self.job_id = job_id

    @property
    def _staging_path(self) -> str:
        root = self.path.replace("s3a://", "").replace("s3://", "").rstrip("/")
        return f"{root}/_staging/{self.job_id}"

    def _make_fs(self) -> pafs.S3FileSystem:
        return pafs.S3FileSystem(
            endpoint_override=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            scheme=os.getenv("SIPHON_S3_SCHEME", "https"),
        )

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        fs = self._make_fs()
        if self.job_id:
            root_path = self._staging_path
            behavior = "overwrite_or_ignore"
            basename_template = f"part-{uuid.uuid4().hex}-{{i}}.parquet"
        else:
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
            table.num_rows, root_path, self.extraction_mode, behavior,
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

    def cleanup_staging(self) -> None:
        """Delete staging directory for this job_id. Called at job start."""
        if not self.job_id:
            return
        fs = self._make_fs()
        staging = self._staging_path
        try:
            fs.delete_dir(staging)
            logger.info("Cleaned up stale staging path %s", staging)
        except FileNotFoundError:
            pass  # no staging from previous run

    def promote(self) -> None:
        """Copy all files from staging to final path, then delete staging."""
        if not self.job_id:
            return
        fs = self._make_fs()
        staging = self._staging_path
        root = self.path.replace("s3a://", "").replace("s3://", "").rstrip("/")
        try:
            file_infos = fs.get_file_info(pafs.FileSelector(staging, recursive=False))
        except FileNotFoundError:
            logger.warning("Staging path %s not found during promote — nothing to promote", staging)
            return
        promoted = 0
        for info in file_infos:
            if info.type == pafs.FileType.File:
                dest = f"{root}/{info.base_name}"
                fs.copy_file(info.path, dest)
                promoted += 1
        fs.delete_dir(staging)
        logger.info("Promoted %d files from %s to %s", promoted, staging, root)


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
