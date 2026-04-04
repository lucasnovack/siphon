from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.fs as pafs
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


# ── Staging support ──────────────────────────────────────────────────────────


def test_staging_path_property():
    dest = _make_dest(job_id="abc-123")
    assert dest._staging_path == "bronze/entity/2026-03-25/_staging/abc-123"


def test_write_uses_staging_when_job_id_set():
    dest = _make_dest(job_id="job-1")
    table = pa.table({"x": [1, 2]})

    with patch("siphon.plugins.destinations.s3_parquet.pafs") as mock_pafs, \
         patch("siphon.plugins.destinations.s3_parquet.pq") as mock_pq:
        mock_pafs.S3FileSystem.return_value = MagicMock()
        dest.write(table)

    call_kwargs = mock_pq.write_to_dataset.call_args
    assert "_staging/job-1" in call_kwargs.kwargs["root_path"]


def test_write_uses_final_path_without_job_id():
    dest = _make_dest(job_id="")
    table = pa.table({"x": [1, 2]})

    with patch("siphon.plugins.destinations.s3_parquet.pafs") as mock_pafs, \
         patch("siphon.plugins.destinations.s3_parquet.pq") as mock_pq:
        mock_pafs.S3FileSystem.return_value = MagicMock()
        dest.write(table)

    call_kwargs = mock_pq.write_to_dataset.call_args
    assert "_staging" not in call_kwargs.kwargs["root_path"]


def test_cleanup_staging_deletes_dir():
    dest = _make_dest(job_id="job-1")

    mock_fs = MagicMock()
    with patch.object(dest, "_make_fs", return_value=mock_fs):
        dest.cleanup_staging()

    mock_fs.delete_dir.assert_called_once_with("bronze/entity/2026-03-25/_staging/job-1")


def test_cleanup_staging_ignores_not_found():
    dest = _make_dest(job_id="job-1")

    mock_fs = MagicMock()
    mock_fs.delete_dir.side_effect = FileNotFoundError
    with patch.object(dest, "_make_fs", return_value=mock_fs):
        dest.cleanup_staging()  # should not raise


def test_cleanup_staging_noop_without_job_id():
    dest = _make_dest(job_id="")
    with patch.object(dest, "_make_fs") as mock_make_fs:
        dest.cleanup_staging()
    mock_make_fs.assert_not_called()


def test_promote_copies_files_to_final_path():
    dest = _make_dest(job_id="job-1")

    file1 = MagicMock()
    file1.type = pafs.FileType.File
    file1.path = "bronze/entity/2026-03-25/_staging/job-1/part-abc-0.parquet"
    file1.base_name = "part-abc-0.parquet"

    mock_fs = MagicMock()
    mock_fs.get_file_info.return_value = [file1]

    with patch.object(dest, "_make_fs", return_value=mock_fs):
        dest.promote()

    mock_fs.copy_file.assert_called_once_with(
        "bronze/entity/2026-03-25/_staging/job-1/part-abc-0.parquet",
        "bronze/entity/2026-03-25/part-abc-0.parquet",
    )
    mock_fs.delete_dir.assert_called_once_with("bronze/entity/2026-03-25/_staging/job-1")


def test_promote_noop_without_job_id():
    dest = _make_dest(job_id="")
    with patch.object(dest, "_make_fs") as mock_make_fs:
        dest.promote()
    mock_make_fs.assert_not_called()
