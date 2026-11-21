import os
import tempfile
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from siphon.plugins.destinations import get as get_destination
from siphon.plugins.destinations.local_parquet import (
    LocalParquetDestination,
    _resolve_and_validate_path,
)


def test_local_parquet_is_registered():
    cls = get_destination("local_parquet")
    assert cls is LocalParquetDestination


def test_resolve_and_validate_path_normal():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = _resolve_and_validate_path("customers", base_path=tmpdir)
        assert res == os.path.join(tmpdir, "customers")


def test_resolve_and_validate_path_with_file_scheme():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = _resolve_and_validate_path(f"file://{tmpdir}/orders")
        assert res == f"{tmpdir}/orders"


def test_resolve_and_validate_path_rejects_traversal():
    with pytest.raises(ValueError, match="Path traversal"):
        _resolve_and_validate_path("../../etc/passwd")


def test_write_and_promote_full_refresh():
    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = os.path.join(tmpdir, "lake", "users")
        dest = LocalParquetDestination(
            path=target_path,
            compression="snappy",
            extraction_mode="full_refresh",
            job_id="test-job-123",
        )

        table = pa.Table.from_pydict({"id": [1, 2, 3], "name": ["alice", "bob", "charlie"]})
        dest.cleanup_staging()
        written = dest.write(table)
        assert written == 3

        # Before promote, files are in staging
        staging_dir = os.path.join(target_path, "_staging", "test-job-123")
        assert os.path.exists(staging_dir)

        # Promote moves to final path and removes staging
        dest.promote()
        assert not os.path.exists(staging_dir)

        # Read back dataset from target_path
        read_table = pq.read_table(target_path)
        assert read_table.num_rows == 3
        assert read_table.column("name").to_pylist() == ["alice", "bob", "charlie"]


def test_write_and_promote_hive_partitioning():
    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = os.path.join(tmpdir, "lake", "events")
        dest = LocalParquetDestination(
            path=target_path,
            compression="snappy",
            extraction_mode="incremental",
            partition_by="ingest_date",
            job_id="test-job-hive-456",
        )

        table = pa.Table.from_pydict({"event_id": [101, 102], "val": [10.5, 20.0]})
        dest.cleanup_staging()
        dest.write(table)
        dest.promote()

        partition_dirs = [d for d in os.listdir(target_path) if d.startswith("_date=")]
        assert len(partition_dirs) == 1
        part_dir = os.path.join(target_path, partition_dirs[0])
        part_files = [f for f in os.listdir(part_dir) if f.endswith(".parquet")]
        assert len(part_files) >= 1

        read_table = pq.read_table(os.path.join(part_dir, part_files[0]))
        assert read_table.num_rows == 2
        assert "_date" in read_table.column_names
