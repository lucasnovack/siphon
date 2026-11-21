# src/siphon/plugins/destinations/local_parquet.py
"""Native Local Filesystem Parquet destination plugin."""
import os
from urllib.parse import unquote

import pyarrow.fs as pafs
import structlog

from siphon.plugins.destinations import register
from siphon.plugins.destinations._object_store import (
    ObjectStoreParquetDestination,
    strip_schemes,
)

logger = structlog.get_logger(__name__)

_LOCAL_SCHEMES = ("file://", "local://")
_ALLOWED_LOCAL_PREFIX = os.getenv("SIPHON_ALLOWED_LOCAL_PATH", "")


def _resolve_and_validate_path(path: str, base_path: str = "") -> str:
    """Normalize and validate local filesystem path against traversal."""
    stripped = strip_schemes(path, _LOCAL_SCHEMES)
    normalized = unquote(stripped)

    # Check for path traversal attempts
    if ".." in normalized.split("/"):
        raise ValueError(f"Path traversal detected in local path: {path!r}")

    # Combine with base_path if path is relative
    if base_path and not os.path.isabs(normalized):
        base_stripped = strip_schemes(base_path, _LOCAL_SCHEMES)
        full_path = os.path.normpath(os.path.join(base_stripped, normalized))
    else:
        full_path = os.path.normpath(normalized)

    # Enforce allowed prefix if configured in environment
    if _ALLOWED_LOCAL_PREFIX:
        allowed = os.path.normpath(_ALLOWED_LOCAL_PREFIX)
        if not full_path.startswith(allowed):
            raise ValueError(
                f"Destination path {full_path!r} is outside allowed local prefix {allowed!r}. "
                "Set SIPHON_ALLOWED_LOCAL_PATH to change."
            )

    return full_path


@register("local_parquet")
class LocalParquetDestination(ObjectStoreParquetDestination):
    """Parquet destination that writes to local disk or mounted directories."""

    _SCHEMES = _LOCAL_SCHEMES

    def __init__(
        self,
        path: str,
        base_path: str = "",
        compression: str = "snappy",
        extraction_mode: str = "full_refresh",
        job_id: str = "",
        partition_by: str = "none",
    ) -> None:
        resolved = _resolve_and_validate_path(path, base_path)
        super().__init__(
            path=resolved,
            compression=compression,
            extraction_mode=extraction_mode,
            job_id=job_id,
            partition_by=partition_by,
        )
        self.base_path = base_path

    def _make_fs(self) -> pafs.LocalFileSystem:
        return pafs.LocalFileSystem()
