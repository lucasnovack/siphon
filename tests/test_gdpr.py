# tests/test_gdpr.py
"""Tests for GDPR purge API and audit log."""
import uuid
from datetime import UTC, datetime, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from siphon.auth.deps import Principal


def _admin():
    user = MagicMock()
    user.id = uuid.uuid4()
    user.role = "admin"
    return Principal(type="user", user=user)


def _make_pipeline(dest_path="s3://bucket/pipeline-data"):
    p = MagicMock()
    p.id = uuid.uuid4()
    p.destination_path = dest_path
    p.deleted_at = None
    return p


def test_purge_s3_task_lists_and_deletes_files(monkeypatch):
    """purge_s3_data_task must list + delete Parquet files and return counts."""
    from siphon.tasks import _purge_s3_files

    deleted = []

    def fake_list(path):
        return [f"{path}/part1.parquet", f"{path}/part2.parquet"]

    def fake_delete(path):
        deleted.append(path)
        return 1024  # bytes

    result = _purge_s3_files(
        base_path="s3://bucket/pipeline-data",
        before_date=None,
        partition_filter=None,
        list_fn=fake_list,
        delete_fn=fake_delete,
    )

    assert result["files_deleted"] == 2
    assert result["bytes_deleted"] == 2048
    assert len(deleted) == 2
