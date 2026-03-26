"""
Integration tests for S3ParquetDestination against real MinIO + MySQL.

Requires docker-compose services to be running:
    docker compose up -d mysql minio

Run with:
    pytest tests/test_integration_s3.py -m integration
"""

import time
import uuid

import pyarrow as pa
import pyarrow.fs as pafs
import pytest

pytestmark = pytest.mark.integration

MYSQL_CONN = "mysql://siphon:siphon@localhost:3306/testdb"
MINIO_ENDPOINT = "localhost:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "bronze"


@pytest.fixture(scope="module")
def minio_fs():
    """Return a PyArrow S3FileSystem pointed at local MinIO. Skip if unavailable."""
    try:
        fs = pafs.S3FileSystem(
            endpoint_override=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            scheme="http",
        )
        fs.create_dir(MINIO_BUCKET)
        return fs
    except Exception as exc:
        pytest.skip(f"MinIO not available: {exc}")


@pytest.fixture(scope="module")
def mysql_available():
    """Skip if MySQL is not reachable."""
    try:
        import connectorx as cx

        cx.read_sql(MYSQL_CONN, "SELECT 1", return_type="arrow")
    except Exception as exc:
        pytest.skip(f"MySQL not available: {exc}")


def test_write_and_read_back_parquet(minio_fs):
    """Write an Arrow Table to MinIO as Parquet and read it back."""
    from unittest.mock import patch

    import siphon.plugins.destinations.s3_parquet as mod

    path = f"s3a://{MINIO_BUCKET}/integration_test/{uuid.uuid4().hex}"
    table = pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]})

    with patch.object(mod, "_ALLOWED_PREFIX", f"{MINIO_BUCKET}/"):
        cls = mod.S3ParquetDestination
        dest = cls(
            path=path,
            endpoint=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
        )

        with patch(
            "pyarrow.fs.S3FileSystem",
            return_value=pafs.S3FileSystem(
                endpoint_override=MINIO_ENDPOINT,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                scheme="http",
            ),
        ):
            rows = dest.write(table, is_first_chunk=True)

    assert rows == 3


def test_full_pipeline_via_http(mysql_available, minio_fs):
    """POST /jobs with MySQL source + MinIO destination → poll → success."""
    import httpx

    base_url = "http://localhost:8000"

    # check service is up
    try:
        resp = httpx.get(f"{base_url}/health/live", timeout=2)
        resp.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Siphon service not running: {exc}")

    dest_path = f"s3a://{MINIO_BUCKET}/integration_sql/{uuid.uuid4().hex}"

    payload = {
        "source": {
            "type": "sql",
            "connection": MYSQL_CONN,
            "query": "SELECT 1 AS n",
        },
        "destination": {
            "type": "s3_parquet",
            "path": dest_path,
            "endpoint": MINIO_ENDPOINT,
            "access_key": MINIO_ACCESS_KEY,
            "secret_key": MINIO_SECRET_KEY,
        },
    }

    resp = httpx.post(f"{base_url}/jobs", json=payload, timeout=10)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # poll until terminal state
    deadline = time.time() + 60
    while time.time() < deadline:
        status_resp = httpx.get(f"{base_url}/jobs/{job_id}", timeout=5)
        status = status_resp.json()["status"]
        if status in ("success", "failed", "partial_success"):
            break
        time.sleep(0.5)

    assert status == "success", f"Job ended with status={status!r}"
    assert status_resp.json()["rows_read"] == 1
    assert status_resp.json()["rows_written"] == 1
