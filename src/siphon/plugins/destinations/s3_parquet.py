import datetime
import os

import pyarrow.fs as pafs
import pyarrow.parquet as pq
import structlog

from siphon.plugins.destinations import register
from siphon.plugins.destinations._object_store import (
    ObjectStoreParquetDestination,
    validate_object_store_path,
)

logger = structlog.get_logger()

_ALLOWED_PREFIX = os.getenv("SIPHON_ALLOWED_S3_PREFIX", "bronze/")
_S3_SCHEMES = ("s3a://", "s3://")


@register("s3_parquet")
class S3ParquetDestination(ObjectStoreParquetDestination):
    _SCHEMES = _S3_SCHEMES

    def __init__(
        self,
        path: str,
        endpoint: str,
        access_key: str,
        secret_key: str,
        compression: str = "snappy",
        extraction_mode: str = "full_refresh",
        job_id: str = "",
        partition_by: str = "none",
    ) -> None:
        _validate_path(path)
        super().__init__(
            path=path,
            compression=compression,
            extraction_mode=extraction_mode,
            job_id=job_id,
            partition_by=partition_by,
        )
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key

    def _get_pq(self):
        return pq

    def _today(self) -> datetime.date:
        return datetime.date.today()

    def _make_fs(self) -> pafs.S3FileSystem:
        return pafs.S3FileSystem(
            endpoint_override=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            scheme=os.getenv("SIPHON_S3_SCHEME", "https"),
        )


def _validate_path(path: str) -> None:
    """Reject paths with traversal or outside the allowed S3 prefix."""
    validate_object_store_path(
        path=path,
        schemes=_S3_SCHEMES,
        allowed_prefix=_ALLOWED_PREFIX,
        env_var_name="SIPHON_ALLOWED_S3_PREFIX",
    )
