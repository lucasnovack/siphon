# tests/test_gcs_parquet.py
import datetime
import json
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.fs as pafs
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from siphon.plugins.destinations import get as get_destination
from siphon.plugins.destinations.gcs_auth import (
    build_gcs_filesystem,
    exchange_service_account_token,
)
from siphon.plugins.destinations.gcs_parquet import GCSParquetDestination, _validate_path


def _generate_sa_json() -> str:
    """Generate a real ephemeral RSA keypair formatted as a GCP Service Account JSON."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return json.dumps({
        "type": "service_account",
        "project_id": "my-gcp-project",
        "client_email": "siphon@my-gcp-project.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    })


# ── Registry ──────────────────────────────────────────────────────────────────


def test_gcs_parquet_is_registered():
    cls = get_destination("gcs_parquet")
    assert cls is GCSParquetDestination


# ── Path validation ───────────────────────────────────────────────────────────


def test_validate_path_allows_valid_gs():
    _validate_path("gs://bronze/orders/2026-11-20")


def test_validate_path_rejects_traversal():
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("gs://bronze/../secret/data")


def test_validate_path_rejects_encoded_traversal():
    with pytest.raises(ValueError, match="Path traversal"):
        _validate_path("gs://bronze/%2e%2e/secret/data")


def test_validate_path_rejects_outside_prefix():
    with pytest.raises(ValueError, match="outside allowed prefix"):
        _validate_path("gs://silver/orders/2026-11-20")


def test_validate_path_custom_prefix():
    import siphon.plugins.destinations.gcs_parquet as mod

    with patch.object(mod, "_ALLOWED_PREFIX", "my-bucket/"):
        mod._validate_path("gs://my-bucket/orders")
        with pytest.raises(ValueError, match="outside allowed prefix"):
            mod._validate_path("gs://bronze/orders")


def test_validate_path_called_in_init():
    with pytest.raises(ValueError, match="outside allowed prefix"):
        GCSParquetDestination(path="gs://forbidden/path")


# ── Authentication modes ──────────────────────────────────────────────────────


def test_build_gcs_fs_adc_default_uses_no_explicit_token():
    """ADC mode lets PyArrow GcsFileSystem resolve GCP VM / GKE / Cloud Run credentials."""
    with patch("pyarrow.fs.GcsFileSystem") as mock_fs:
        build_gcs_filesystem(auth_mode="adc", project_id="proj-1")

    mock_fs.assert_called_once_with(project_id="proj-1")


def test_build_gcs_fs_adc_with_impersonation():
    with patch("pyarrow.fs.GcsFileSystem") as mock_fs:
        build_gcs_filesystem(
            auth_mode="adc",
            target_service_account="writer@proj.iam.gserviceaccount.com",
        )

    mock_fs.assert_called_once_with(
        target_service_account="writer@proj.iam.gserviceaccount.com"
    )


def test_build_gcs_fs_anonymous_with_emulator_endpoint():
    with patch("pyarrow.fs.GcsFileSystem") as mock_fs:
        build_gcs_filesystem(
            auth_mode="anonymous",
            endpoint_override="localhost:4443",
            scheme="http",
        )

    mock_fs.assert_called_once_with(
        anonymous=True,
        endpoint_override="localhost:4443",
        scheme="http",
    )


def test_exchange_service_account_token_posts_signed_jwt():
    sa_json = _generate_sa_json()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": "ya29.fake-token", "expires_in": 3600}

    with patch("siphon.plugins.destinations.gcs_auth.requests.post", return_value=mock_resp) as mock_post:
        token, expiration = exchange_service_account_token(sa_json)

    assert token == "ya29.fake-token"
    assert expiration > datetime.datetime.now(tz=datetime.UTC)
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert len(call_kwargs["data"]["assertion"].split(".")) == 3  # valid JWT


def test_build_gcs_fs_service_account_mode_passes_token_and_expiration():
    sa_json = _generate_sa_json()
    fake_exp = datetime.datetime(2026, 11, 20, 15, 0, 0, tzinfo=datetime.UTC)

    with (
        patch(
            "siphon.plugins.destinations.gcs_auth.exchange_service_account_token",
            return_value=("ya29.token", fake_exp),
        ) as mock_exchange,
        patch("pyarrow.fs.GcsFileSystem") as mock_fs,
    ):
        build_gcs_filesystem(auth_mode="service_account", credentials_json=sa_json)

    mock_exchange.assert_called_once_with(sa_json)
    mock_fs.assert_called_once_with(
        access_token="ya29.token",
        credential_token_expiration=fake_exp,
    )


def test_build_gcs_fs_service_account_requires_credentials_json():
    with pytest.raises(ValueError, match="credentials_json is required"):
        build_gcs_filesystem(auth_mode="service_account", credentials_json=None)


# ── Write, staging, promote, partitioning ─────────────────────────────────────


def test_gcs_write_strips_gs_scheme_and_writes_parquet():
    dest = GCSParquetDestination(path="gs://bronze/orders/2026-11-20")
    table = pa.table({"id": [1, 2, 3]})

    with (
        patch("pyarrow.fs.GcsFileSystem", return_value=MagicMock()),
        patch("pyarrow.parquet.write_to_dataset") as mock_write,
    ):
        rows = dest.write(table, is_first_chunk=True)

    assert rows == 3
    _, kwargs = mock_write.call_args
    assert kwargs["root_path"] == "bronze/orders/2026-11-20"
    assert kwargs["existing_data_behavior"] == "delete_matching"
    assert kwargs["compression"] == "snappy"


def test_gcs_staging_and_promote():
    dest = GCSParquetDestination(path="gs://bronze/orders", job_id="job-gcs-1")
    assert dest._staging_path == "bronze/orders/_staging/job-gcs-1"

    file1 = MagicMock()
    file1.type = pafs.FileType.File
    file1.path = "bronze/orders/_staging/job-gcs-1/_date=2026-11-20/part-0.parquet"

    mock_fs = MagicMock()
    mock_fs.get_file_info.return_value = [file1]

    with patch.object(dest, "_make_fs", return_value=mock_fs):
        dest.promote()

    mock_fs.copy_file.assert_called_once_with(
        "bronze/orders/_staging/job-gcs-1/_date=2026-11-20/part-0.parquet",
        "bronze/orders/_date=2026-11-20/part-0.parquet",
    )
    mock_fs.delete_dir.assert_called_once_with("bronze/orders/_staging/job-gcs-1")


def test_gcs_partitioned_write_adds_date_column():
    dest = GCSParquetDestination(
        path="gs://bronze/events",
        partition_by="ingest_date",
    )
    table = pa.table({"id": [1, 2]})

    with (
        patch.object(dest, "_make_fs", return_value=MagicMock()),
        patch.object(dest, "_today", return_value=datetime.date(2026, 11, 20)),
        patch("pyarrow.parquet.write_to_dataset") as mock_write,
    ):
        dest.write(table)

    assert mock_write.call_args.kwargs["partition_cols"] == ["_date"]
    written_table = mock_write.call_args.args[0]
    assert written_table.column("_date").to_pylist() == ["2026-11-20", "2026-11-20"]
