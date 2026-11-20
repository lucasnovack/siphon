# src/siphon/models.py
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# ── Credential masking ────────────────────────────────────────────────────────


def mask_uri(uri: str) -> str:
    """Replace user:password in a connection URI with ***.

    Example:
        mysql://user:SenhaSecreta@host:3306/db
        → mysql://***:***@host:3306/db
    """
    if not uri:
        return uri
    return re.sub(r"(://)[^:]+:[^@]+(@)", r"\1***:***\2", uri)


# ── Source configs ────────────────────────────────────────────────────────────


class SQLSourceConfig(BaseModel):
    type: Literal["sql"]
    connection: str
    query: str
    partition_on: str | None = None
    partition_num: int | None = None
    partition_range: tuple[int, int] | None = None

    def __repr__(self) -> str:
        return (
            f"SQLSourceConfig(connection={mask_uri(self.connection)!r}, "
            f"query={self.query[:50]!r}{'...' if len(self.query) > 50 else ''})"
        )


class SFTPSourceConfig(BaseModel):
    type: Literal["sftp"]
    host: str
    port: int = 22
    username: str
    password: str
    paths: list[str]
    parser: str
    parser_config: dict = Field(default_factory=dict)
    skip_patterns: list[str] = Field(default_factory=lambda: ["TMP_*"])
    max_files: int = 1000
    chunk_size: int = 100
    fail_fast: bool = False
    processing_folder: str | None = None
    processed_folder: str | None = None

    def __repr__(self) -> str:
        return (
            f"SFTPSourceConfig(host={self.host!r}, username={self.username!r}, "
            f"paths={self.paths!r}, parser={self.parser!r})"
        )


class HTTPRestSourceConfig(BaseModel):
    type: Literal["http_rest"]
    url: str
    auth_type: Literal["none", "bearer", "api_key", "oauth2_client_credentials"] = "none"
    auth_config: dict = Field(default_factory=dict)
    results_key: str | None = None
    pagination_type: Literal["none", "cursor", "page", "offset"] = "none"
    pagination_config: dict = Field(default_factory=dict)
    rate_limit_seconds: float = 0.0
    max_pages: int = 100
    headers: dict = Field(default_factory=dict)


# ── Destination configs ───────────────────────────────────────────────────────


class S3ParquetDestinationConfig(BaseModel):
    type: Literal["s3_parquet"]
    path: str
    endpoint: str
    access_key: str
    secret_key: str
    compression: str = "snappy"
    extraction_mode: Literal["full_refresh", "incremental"] = "full_refresh"
    partition_by: Literal["none", "ingest_date"] = "none"

    def __repr__(self) -> str:
        return (
            f"S3ParquetDestinationConfig(path={self.path!r}, "
            f"endpoint={self.endpoint!r}, access_key='***', secret_key='***')"
        )


class GCSParquetDestinationConfig(BaseModel):
    type: Literal["gcs_parquet"]
    path: str
    auth_mode: Literal["adc", "service_account", "anonymous"] = "adc"
    credentials_json: str | dict | None = None
    project_id: str | None = None
    target_service_account: str | None = None
    endpoint_override: str | None = None
    scheme: str | None = None
    compression: str = "snappy"
    extraction_mode: Literal["full_refresh", "incremental"] = "full_refresh"
    partition_by: Literal["none", "ingest_date"] = "none"

    def __repr__(self) -> str:
        return (
            f"GCSParquetDestinationConfig(path={self.path!r}, "
            f"auth_mode={self.auth_mode!r}, project_id={self.project_id!r}, "
            f"credentials_json={'***' if self.credentials_json else None})"
        )


def build_destination_payload(
    dest_type: str,
    dest_config: dict,
    dest_path: str,
    extraction_mode: str = "full_refresh",
    partition_by: str = "none",
) -> dict:
    """Build a validated destination plugin payload from a Connection's decrypted config."""
    if dest_type == "s3_parquet":
        return {
            "type": "s3_parquet",
            "path": dest_path,
            "endpoint": dest_config["endpoint"],
            "access_key": dest_config["access_key"],
            "secret_key": dest_config["secret_key"],
            "extraction_mode": extraction_mode,
            "partition_by": partition_by or "none",
        }
    if dest_type == "gcs_parquet":
        payload = {
            "type": "gcs_parquet",
            "path": dest_path,
            "auth_mode": dest_config.get("auth_mode") or "adc",
            "extraction_mode": extraction_mode,
            "partition_by": partition_by or "none",
        }
        for opt_key in (
            "credentials_json",
            "project_id",
            "target_service_account",
            "endpoint_override",
            "scheme",
        ):
            val = dest_config.get(opt_key)
            if val not in (None, ""):
                payload[opt_key] = val
        return payload
    raise ValueError(f"Unsupported destination connection type: {dest_type!r}")


# ── Request / response models ─────────────────────────────────────────────────

SourceConfig = Annotated[
    SQLSourceConfig | SFTPSourceConfig | HTTPRestSourceConfig,
    Field(discriminator="type"),
]

DestinationConfig = Annotated[
    S3ParquetDestinationConfig | GCSParquetDestinationConfig,
    Field(discriminator="type"),
]


class ExtractRequest(BaseModel):
    source: SourceConfig
    destination: DestinationConfig


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "failed", "partial_success"]
    rows_read: int | None = None
    rows_written: int | None = None
    duration_ms: int | None = None
    log_count: int = 0
    failed_files: list[str] = Field(default_factory=list)
    error: str | None = None


class LogsResponse(BaseModel):
    job_id: str
    logs: list[str]
    next_offset: int


# ── Internal Job dataclass (no credentials stored) ────────────────────────────


@dataclass
class Job:
    """Internal job state. Never stores connection strings, passwords, or keys."""

    job_id: str
    priority: str = "normal"   # "low" | "normal" | "high"
    status: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows_read: int | None = None
    rows_written: int | None = None
    failed_files: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    # Pipeline-specific fields (populated by trigger_pipeline; None for legacy /jobs)
    run_id: int | None = None           # existing job_runs row to UPDATE on completion
    pipeline_id: str | None = None      # UUID string; used to update watermark/schema hash
    pipeline_dq: dict | None = None     # {min_rows_expected, max_rows_drop_pct, prev_rows}
    pipeline_schema_hash: str | None = None  # last stored SHA-256 hash for schema evolution
    pipeline_pii: dict | None = None   # {column: "sha256" | "redact"}
    is_backfill: bool = False           # True when triggered with date_from/date_to
    pipeline_alert: dict | None = None  # {webhook_url: str, alert_on: list[str]}
    pipeline_expected_schema: list[dict] | None = None  # expected Arrow schema for DQ
    schema_hash: str | None = None      # computed during extraction; written to job_runs
    source_connection_id: str | None = None   # UUID string; populated by trigger_pipeline
    destination_path: str | None = None        # resolved S3 path
    pipeline_prev_rows: int | None = None      # rows_read from previous successful run
    pipeline_extraction_mode: str | None = None  # "full_refresh" | "incremental"
    _actual_schema: object = field(default=None, repr=False)  # transient; not serialized

    # NOTE: Only used in tests. Production status reads come from job_runs DB table.
    def to_status(self) -> JobStatus:
        """Convert internal Job to API-facing JobStatus."""
        duration_ms = None
        if self.started_at and self.finished_at:
            duration_ms = int((self.finished_at - self.started_at).total_seconds() * 1000)
        return JobStatus(
            job_id=self.job_id,
            status=self.status,
            rows_read=self.rows_read,
            rows_written=self.rows_written,
            duration_ms=duration_ms,
            log_count=len(self.logs),
            failed_files=self.failed_files,
            error=self.error,
        )
