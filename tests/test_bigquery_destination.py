# tests/test_bigquery_destination.py
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from siphon.plugins.destinations import get as get_dest


def _make_mock_bigquery():
    mock_bq = MagicMock()
    mock_bq.WriteDisposition.WRITE_APPEND = "WRITE_APPEND"
    mock_bq.WriteDisposition.WRITE_TRUNCATE = "WRITE_TRUNCATE"
    mock_bq.LoadJobConfig = MagicMock(side_effect=lambda **kw: MagicMock(**kw))
    return mock_bq


def _bq_ctx(mock_bq, mock_sa=None):
    """Context manager that patches sys.modules with BigQuery mocks."""
    if mock_sa is None:
        mock_sa = MagicMock()
        mock_sa.Credentials.from_service_account_info.return_value = MagicMock()
    return patch.dict("sys.modules", {
        "google": MagicMock(),
        "google.cloud": MagicMock(),
        "google.cloud.bigquery": mock_bq,
        "google.oauth2": MagicMock(),
        "google.oauth2.service_account": mock_sa,
    })


def _dest(**kwargs):
    cls = get_dest("bigquery")
    defaults = {
        "project": "my-proj",
        "dataset": "my_ds",
        "table": "my_tbl",
        "credentials_json": '{"type": "service_account", "project_id": "my-proj"}',
        "write_mode": "append",
        "location": "US",
    }
    defaults.update(kwargs)
    return cls(**defaults)


# ── Registry ──────────────────────────────────────────────────────────────────


def test_bigquery_is_registered():
    cls = get_dest("bigquery")
    assert cls.__name__ == "BigQueryDestination"


# ── Write modes ───────────────────────────────────────────────────────────────


def test_write_append_uses_write_append_disposition():
    dest = _dest(write_mode="append")
    table = pa.table({"id": [1, 2], "name": ["a", "b"]})

    mock_bq = _make_mock_bigquery()
    mock_client = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_job = MagicMock()
    mock_client.load_table_from_dataframe.return_value = mock_job

    with _bq_ctx(mock_bq):
        rows = dest.write(table, is_first_chunk=True)

    assert rows == 2
    mock_job.result.assert_called_once()
    _, kwargs = mock_client.load_table_from_dataframe.call_args
    assert kwargs["job_config"].write_disposition == "WRITE_APPEND"


def test_write_replace_first_chunk_uses_write_truncate():
    dest = _dest(write_mode="replace")
    table = pa.table({"id": [1]})

    mock_bq = _make_mock_bigquery()
    mock_client = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_job = MagicMock()
    mock_client.load_table_from_dataframe.return_value = mock_job

    with _bq_ctx(mock_bq):
        rows = dest.write(table, is_first_chunk=True)

    assert rows == 1
    _, kwargs = mock_client.load_table_from_dataframe.call_args
    assert kwargs["job_config"].write_disposition == "WRITE_TRUNCATE"


def test_write_replace_subsequent_chunk_uses_write_append():
    dest = _dest(write_mode="replace")
    table = pa.table({"id": [1]})

    mock_bq = _make_mock_bigquery()
    mock_client = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_job = MagicMock()
    mock_client.load_table_from_dataframe.return_value = mock_job

    with _bq_ctx(mock_bq):
        rows = dest.write(table, is_first_chunk=False)

    _, kwargs = mock_client.load_table_from_dataframe.call_args
    assert kwargs["job_config"].write_disposition == "WRITE_APPEND"


def test_write_calls_correct_table_ref():
    dest = _dest(project="proj", dataset="ds", table="tbl")
    table = pa.table({"x": [1]})

    mock_bq = _make_mock_bigquery()
    mock_client = MagicMock()
    mock_bq.Client.return_value = mock_client
    mock_client.load_table_from_dataframe.return_value = MagicMock()

    with _bq_ctx(mock_bq):
        dest.write(table, is_first_chunk=True)

    args, _ = mock_client.load_table_from_dataframe.call_args
    assert args[1] == "proj.ds.tbl"
