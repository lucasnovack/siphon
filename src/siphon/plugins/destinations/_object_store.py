# src/siphon/plugins/destinations/_object_store.py
"""Shared base class for object-store Parquet destinations (S3, GCS)."""
import datetime
import uuid
from abc import abstractmethod
from urllib.parse import unquote

import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq
import structlog

from siphon.plugins.destinations.base import Destination

logger = structlog.get_logger()


def validate_object_store_path(
    path: str,
    schemes: tuple[str, ...],
    allowed_prefix: str,
    env_var_name: str,
) -> None:
    """Reject paths with traversal or outside the allowed prefix.

    URL-decodes the path before checking to catch encoded traversal sequences
    like %2e%2e or %2F..%2F.
    """
    stripped = strip_schemes(path, schemes)
    normalized = unquote(stripped)
    if ".." in normalized:
        raise ValueError(f"Path traversal detected in destination path: {path!r}")
    if allowed_prefix and not normalized.startswith(allowed_prefix):
        raise ValueError(
            f"Destination path {path!r} is outside allowed prefix {allowed_prefix!r}. "
            f"Set {env_var_name} to change."
        )


def strip_schemes(path: str, schemes: tuple[str, ...]) -> str:
    result = path
    for scheme in schemes:
        if result.startswith(scheme):
            return result[len(scheme):]
    return result


class ObjectStoreParquetDestination(Destination):
    """Parquet writer with atomic staging, Hive partitioning, and path validation."""

    _SCHEMES: tuple[str, ...] = ()

    def __init__(
        self,
        path: str,
        compression: str = "snappy",
        extraction_mode: str = "full_refresh",
        job_id: str = "",
        partition_by: str = "none",
    ) -> None:
        self.path = path
        self.compression = compression
        self.extraction_mode = extraction_mode
        self.job_id = job_id
        self.partition_by = partition_by

    @abstractmethod
    def _make_fs(self) -> pafs.FileSystem:
        """Instantiate and return the underlying PyArrow FileSystem."""

    def _get_pq(self):
        return pq

    def _today(self) -> datetime.date:
        return datetime.date.today()

    def _stripped_path(self) -> str:
        return strip_schemes(self.path, self._SCHEMES)

    @property
    def _staging_path(self) -> str:
        root = self._stripped_path().rstrip("/")
        return f"{root}/_staging/{self.job_id}"

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        fs = self._make_fs()
        partition_cols = None
        if self.partition_by == "ingest_date":
            date_str = str(self._today())
            date_col = pa.array([date_str] * table.num_rows, type=pa.string())
            table = table.append_column(pa.field("_date", pa.string()), date_col)
            partition_cols = ["_date"]

        if self.job_id:
            root_path = self._staging_path
            behavior = "overwrite_or_ignore"
            basename_template = f"part-{uuid.uuid4().hex}-{{i}}.parquet"
        else:
            root_path = self._stripped_path()
            if self.extraction_mode == "incremental":
                behavior = "overwrite_or_ignore"
                # Unique basename so each incremental run appends new files
                # instead of overwriting the previous run's part-0.parquet
                basename_template = f"part-{uuid.uuid4().hex}-{{i}}.parquet"
            else:
                behavior = "delete_matching" if is_first_chunk else "overwrite_or_ignore"
                basename_template = "part-{i}.parquet"

        logger.info(
            "writing_rows",
            row_count=table.num_rows,
            root_path=root_path,
            extraction_mode=self.extraction_mode,
            behavior=behavior,
            partition_by=self.partition_by,
        )
        write_kwargs = {}
        if partition_cols:
            write_kwargs["partition_cols"] = partition_cols

        self._get_pq().write_to_dataset(
            table,
            root_path=root_path,
            filesystem=fs,
            compression=self.compression,
            existing_data_behavior=behavior,
            basename_template=basename_template,
            **write_kwargs,
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
            logger.info("staging_cleaned_up", staging_path=staging)
        except FileNotFoundError:
            pass  # no staging from previous run

    def promote(self) -> None:
        """Copy all files from staging to final path, then delete staging."""
        if not self.job_id:
            return
        fs = self._make_fs()
        staging = self._staging_path
        root = self._stripped_path().rstrip("/")
        try:
            file_infos = fs.get_file_info(pafs.FileSelector(staging, recursive=True))
        except FileNotFoundError:
            logger.warning("staging_path_not_found", staging_path=staging)
            return
        promoted = 0
        for info in file_infos:
            if info.type == pafs.FileType.File:
                rel_path = info.path[len(staging):].lstrip("/")
                dest_file = f"{root}/{rel_path}"
                dest_dir = "/".join(dest_file.split("/")[:-1])
                if dest_dir:
                    try:
                        fs.create_dir(dest_dir, recursive=True)
                    except Exception:
                        pass
                fs.copy_file(info.path, dest_file)
                promoted += 1
        fs.delete_dir(staging)
        logger.info("staging_promoted", file_count=promoted, staging_path=staging, dest_path=root)
