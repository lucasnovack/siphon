# src/siphon/plugins/destinations/gcs_parquet.py
"""Native Google Cloud Storage (gs://) Parquet destination plugin."""
import os

import pyarrow.fs as pafs

from siphon.plugins.destinations import register
from siphon.plugins.destinations._object_store import (
    ObjectStoreParquetDestination,
    validate_object_store_path,
)
from siphon.plugins.destinations.gcs_auth import build_gcs_filesystem

_ALLOWED_PREFIX = os.getenv("SIPHON_ALLOWED_GCS_PREFIX", "bronze/")
_GCS_SCHEMES = ("gs://",)


@register("gcs_parquet")
class GCSParquetDestination(ObjectStoreParquetDestination):
    _SCHEMES = _GCS_SCHEMES

    def __init__(
        self,
        path: str,
        auth_mode: str = "adc",
        credentials_json: str | dict | None = None,
        project_id: str | None = None,
        target_service_account: str | None = None,
        endpoint_override: str | None = None,
        scheme: str | None = None,
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
        self.auth_mode = auth_mode
        self.credentials_json = credentials_json
        self.project_id = project_id
        self.target_service_account = target_service_account
        self.endpoint_override = endpoint_override
        self.scheme = scheme

    def _make_fs(self) -> pafs.GcsFileSystem:
        return build_gcs_filesystem(
            auth_mode=self.auth_mode,
            credentials_json=self.credentials_json,
            project_id=self.project_id,
            target_service_account=self.target_service_account,
            endpoint_override=self.endpoint_override,
            scheme=self.scheme,
        )


def _validate_path(path: str) -> None:
    """Reject paths with traversal or outside the allowed GCS prefix."""
    validate_object_store_path(
        path=path,
        schemes=_GCS_SCHEMES,
        allowed_prefix=_ALLOWED_PREFIX,
        env_var_name="SIPHON_ALLOWED_GCS_PREFIX",
    )
