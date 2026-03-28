from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from siphon.plugins.destinations import get as get_destination
from siphon.plugins.destinations.s3_parquet import _validate_path

# ── Registry ──────────────────────────────────────────────────────────────────


def test_s3_parquet_is_registered():
    cls = get_destination("s3_parquet")
    assert cls.__name__ == "S3ParquetDestination"


# ── Path validation ───────────────────────────────────────────────────────────


def test_validate_path_allows_valid_s3a():
    _validate_path("s3a://bronze/entity/2026-03-25")  # should not raise


def test_validate_path_allows_valid_s3():
    _validate_path("s3://bronze/entity/2026-03-25")  # should not raise


def test_validate_path_rejects_traversal():
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("s3a://bronze/../secret/path")


def test_validate_path_rejects_outside_prefix():
    with pytest.raises(ValueError, match="outside allowed prefix"):
        _validate_path("s3a://silver/entity/2026-03-25")


def test_validate_path_custom_prefix():
    import siphon.plugins.destinations.s3_parquet as mod

    # Use mod._validate_path to ensure we target the live module after any reload.
    with patch.object(mod, "_ALLOWED_PREFIX", "data/"):
        mod._validate_path("s3a://data/entity/2026-03-25")  # should not raise
        with pytest.raises(ValueError, match="outside allowed prefix"):
            mod._validate_path("s3a://bronze/entity/2026-03-25")


def test_validate_path_called_in_init():
    """Invalid path raises at instantiation, before any I/O."""
    cls = get_destination("s3_parquet")
    with pytest.raises(ValueError, match="outside allowed prefix"):
        cls(
            path="s3a://silver/bad/path",
            endpoint="minio:9000",
            access_key="k",
            secret_key="s",
        )


# ── write() behavior ──────────────────────────────────────────────────────────


def _make_dest(**kwargs):
    cls = get_destination("s3_parquet")
    defaults = dict(
        path="s3a://bronze/entity/2026-03-25",
        endpoint="minio:9000",
        access_key="key",
        secret_key="secret",
    )
    defaults.update(kwargs)
    return cls(**defaults)


def test_write_first_chunk_uses_delete_matching():
    table = pa.table({"x": [1, 2, 3]})
    dest = _make_dest()

    with (
        patch("pyarrow.fs.S3FileSystem") as mock_fs_cls,
        patch("pyarrow.parquet.write_to_dataset") as mock_write,
    ):
        mock_fs_cls.return_value = MagicMock()
        rows = dest.write(table, is_first_chunk=True)

    mock_write.assert_called_once()
    _, kwargs = mock_write.call_args
    assert kwargs["existing_data_behavior"] == "delete_matching"
    assert rows == 3


def test_write_subsequent_chunk_uses_overwrite_or_ignore():
    table = pa.table({"x": [1]})
    dest = _make_dest()

    with (
        patch("pyarrow.fs.S3FileSystem") as mock_fs_cls,
        patch("pyarrow.parquet.write_to_dataset") as mock_write,
    ):
        mock_fs_cls.return_value = MagicMock()
        rows = dest.write(table, is_first_chunk=False)

    _, kwargs = mock_write.call_args
    assert kwargs["existing_data_behavior"] == "overwrite_or_ignore"
    assert rows == 1


def test_write_strips_s3a_scheme_from_root_path():
    table = pa.table({"x": [1]})
    dest = _make_dest(path="s3a://bronze/entity/2026-03-25")

    with (
        patch("pyarrow.fs.S3FileSystem") as mock_fs_cls,
        patch("pyarrow.parquet.write_to_dataset") as mock_write,
    ):
        mock_fs_cls.return_value = MagicMock()
        dest.write(table)

    _, kwargs = mock_write.call_args
    assert kwargs["root_path"] == "bronze/entity/2026-03-25"


def test_write_passes_compression():
    table = pa.table({"x": [1]})
    dest = _make_dest()

    with (
        patch("pyarrow.fs.S3FileSystem") as mock_fs_cls,
        patch("pyarrow.parquet.write_to_dataset") as mock_write,
    ):
        mock_fs_cls.return_value = MagicMock()
        dest.write(table)

    _, kwargs = mock_write.call_args
    assert kwargs["compression"] == "snappy"


# ── SIPHON_S3_SCHEME ──────────────────────────────────────────────────────────


def test_s3_scheme_defaults_to_https():
    table = pa.table({"x": [1]})
    dest = _make_dest()

    with (
        patch("pyarrow.fs.S3FileSystem") as mock_fs_cls,
        patch("pyarrow.parquet.write_to_dataset"),
        patch.dict("os.environ", {}, clear=False),
    ):
        mock_fs_cls.return_value = MagicMock()
        import os

        os.environ.pop("SIPHON_S3_SCHEME", None)
        dest.write(table)

    _, kwargs = mock_fs_cls.call_args
    assert kwargs.get("scheme") == "https"


def test_s3_scheme_configurable(monkeypatch):
    monkeypatch.setenv("SIPHON_S3_SCHEME", "http")
    table = pa.table({"x": [1]})
    dest = _make_dest()

    with (
        patch("pyarrow.fs.S3FileSystem") as mock_fs_cls,
        patch("pyarrow.parquet.write_to_dataset"),
    ):
        mock_fs_cls.return_value = MagicMock()
        dest.write(table)

    _, kwargs = mock_fs_cls.call_args
    assert kwargs.get("scheme") == "http"


# ── rows_read != rows_written → job fails ─────────────────────────────────────


@pytest.mark.asyncio
async def test_row_mismatch_fails_job():
    """Worker must mark job as failed when destination returns fewer rows than read."""
    from concurrent.futures import ThreadPoolExecutor

    from siphon.models import Job
    from siphon.plugins.destinations.base import Destination
    from siphon.plugins.sources.base import Source
    from siphon.worker import run_job

    class _Source(Source):
        def extract(self) -> pa.Table:
            return pa.table({"x": [1, 2, 3]})

    class _ShortDest(Destination):
        def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
            return table.num_rows - 1  # simulate under-reporting

    with ThreadPoolExecutor(max_workers=1) as ex:
        job = Job(job_id="j-mismatch")
        await run_job(_Source(), _ShortDest(), job, ex, timeout=5)

    assert job.status == "failed"
    assert "mismatch" in job.error.lower()
    assert job.rows_read == 3
    assert job.rows_written == 2
