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


# ── Request / response models ─────────────────────────────────────────────────

SourceConfig = Annotated[
    SQLSourceConfig | SFTPSourceConfig,
    Field(discriminator="type"),
]

# DestinationConfig: single member for now — add more types as plugins are added.
# Pydantic v2 discriminator requires 2+ members in a union, so we type directly.


class ExtractRequest(BaseModel):
    source: SourceConfig
    destination: S3ParquetDestinationConfig


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
    schema_hash: str | None = None      # computed during extraction; written to job_runs

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
