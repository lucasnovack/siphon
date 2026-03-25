# tests/test_models.py
from datetime import UTC

import pytest
from pydantic import ValidationError

from siphon.models import (
    ExtractRequest,
    Job,
    JobStatus,
    LogsResponse,
    S3ParquetDestinationConfig,
    SFTPSourceConfig,
    SQLSourceConfig,
    mask_uri,
)


class TestMaskUri:
    def test_masks_password(self):
        uri = "mysql://user:SenhaSecreta@host:3306/db"
        assert mask_uri(uri) == "mysql://***:***@host:3306/db"

    def test_masks_oracle_uri(self):
        uri = "oracle+oracledb://admin:pass123@host:1521/service"
        assert mask_uri(uri) == "oracle+oracledb://***:***@host:1521/service"

    def test_no_credentials_unchanged(self):
        uri = "mysql://host:3306/db"
        assert mask_uri(uri) == uri

    def test_empty_string(self):
        assert mask_uri("") == ""


class TestSQLSourceConfig:
    def test_valid_minimal(self):
        cfg = SQLSourceConfig(
            type="sql",
            connection="mysql://user:pass@host/db",
            query="SELECT 1",
        )
        assert cfg.type == "sql"
        assert cfg.partition_on is None

    def test_repr_masks_password(self):
        cfg = SQLSourceConfig(
            type="sql",
            connection="mysql://user:SenhaSecreta@host/db",
            query="SELECT 1",
        )
        r = repr(cfg)
        assert "SenhaSecreta" not in r
        assert "***" in r

    def test_partition_fields_optional(self):
        cfg = SQLSourceConfig(
            type="sql",
            connection="postgresql://u:p@h/db",
            query="SELECT id FROM t",
            partition_on="id",
            partition_num=4,
        )
        assert cfg.partition_on == "id"
        assert cfg.partition_num == 4
        assert cfg.partition_range is None


class TestSFTPSourceConfig:
    def test_valid_with_defaults(self):
        cfg = SFTPSourceConfig(
            type="sftp",
            host="sftp.internal",
            port=22,
            username="sftpuser",
            password="pass",
            paths=["/remote/path"],
            parser="example_parser",
        )
        assert cfg.skip_patterns == ["TMP_*"]
        assert cfg.max_files == 1000
        assert cfg.chunk_size == 100
        assert cfg.fail_fast is False
        assert cfg.processing_folder is None
        assert cfg.processed_folder is None

    def test_repr_masks_password(self):
        cfg = SFTPSourceConfig(
            type="sftp",
            host="sftp.internal",
            port=22,
            username="user",
            password="SenhaSecreta",
            paths=["/path"],
            parser="example_parser",
        )
        r = repr(cfg)
        assert "SenhaSecreta" not in r


class TestS3ParquetDestinationConfig:
    def test_valid(self):
        cfg = S3ParquetDestinationConfig(
            type="s3_parquet",
            path="s3a://bronze/entity/2026-03-25",
            endpoint="minio.internal:9000",
            access_key="key",
            secret_key="secret",
        )
        assert cfg.compression == "snappy"

    def test_repr_masks_credentials(self):
        cfg = S3ParquetDestinationConfig(
            type="s3_parquet",
            path="s3a://bronze/x/2026-03-25",
            endpoint="minio:9000",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG",
        )
        r = repr(cfg)
        assert "AKIAIOSFODNN7EXAMPLE" not in r
        assert "wJalrXUtnFEMI" not in r


class TestExtractRequest:
    def test_discriminated_union_routes_sql(self):
        req = ExtractRequest.model_validate(
            {
                "source": {
                    "type": "sql",
                    "connection": "mysql://u:p@h/db",
                    "query": "SELECT 1",
                },
                "destination": {
                    "type": "s3_parquet",
                    "path": "s3a://bronze/x/2026-03-25",
                    "endpoint": "minio:9000",
                    "access_key": "k",
                    "secret_key": "s",
                },
            }
        )
        assert isinstance(req.source, SQLSourceConfig)

    def test_discriminated_union_routes_sftp(self):
        req = ExtractRequest.model_validate(
            {
                "source": {
                    "type": "sftp",
                    "host": "sftp.internal",
                    "port": 22,
                    "username": "u",
                    "password": "p",
                    "paths": ["/path"],
                    "parser": "example_parser",
                },
                "destination": {
                    "type": "s3_parquet",
                    "path": "s3a://bronze/x/2026-03-25",
                    "endpoint": "minio:9000",
                    "access_key": "k",
                    "secret_key": "s",
                },
            }
        )
        assert isinstance(req.source, SFTPSourceConfig)

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValidationError):
            ExtractRequest.model_validate(
                {
                    "source": {"type": "unknown", "connection": "x", "query": "y"},
                    "destination": {
                        "type": "s3_parquet",
                        "path": "s3a://x",
                        "endpoint": "e",
                        "access_key": "k",
                        "secret_key": "s",
                    },
                }
            )


class TestJobStatusModel:
    def test_valid_success(self):
        s = JobStatus(
            job_id="abc-123",
            status="success",
            rows_read=100,
            rows_written=100,
            duration_ms=500,
            log_count=5,
            failed_files=[],
            error=None,
        )
        assert s.status == "success"

    def test_partial_success_status(self):
        s = JobStatus(
            job_id="abc-456",
            status="partial_success",
            rows_read=80,
            rows_written=80,
            duration_ms=300,
            log_count=3,
            failed_files=["bad_file.bin"],
            error=None,
        )
        assert s.failed_files == ["bad_file.bin"]


class TestLogsResponse:
    def test_valid(self):
        r = LogsResponse(job_id="abc", logs=["line1", "line2"], next_offset=2)
        assert r.next_offset == 2


class TestJob:
    def test_created_at_is_timezone_aware(self):
        job = Job(job_id="test-123")
        assert job.created_at.tzinfo is not None

    def test_never_stores_credentials(self):
        job = Job(job_id="test-456")
        job_fields = {f.name for f in job.__dataclass_fields__.values()}
        sensitive = {"connection", "access_key", "secret_key", "password"}
        assert not job_fields.intersection(sensitive), (
            f"Job stores sensitive fields: {job_fields.intersection(sensitive)}"
        )

    def test_to_status_returns_job_status(self):
        from datetime import datetime

        job = Job(job_id="test-789", status="success", rows_read=10, rows_written=10)
        job.started_at = datetime.now(tz=UTC)
        job.finished_at = datetime.now(tz=UTC)
        status = job.to_status()
        assert isinstance(status, JobStatus)
        assert status.job_id == "test-789"
        assert status.status == "success"
