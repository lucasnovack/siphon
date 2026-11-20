# tests/test_concurrency.py
"""Tests for Redis-backed per-connection concurrency semaphore."""
import os
from unittest.mock import MagicMock, patch

from siphon.concurrency import acquire_connection_slot, release_connection_slot


def test_acquire_returns_none_without_redis_url():
    with patch.dict(os.environ, {}, clear=True):
        assert acquire_connection_slot("conn-1", "job-1", limit=2) is None


def test_acquire_returns_true_when_lua_grants_slot():
    mock_client = MagicMock()
    mock_client.eval.return_value = [1, 1]

    with patch("siphon.concurrency._get_redis_client", return_value=mock_client):
        res = acquire_connection_slot("conn-1", "job-1", limit=2, ttl_seconds=60)

    assert res == (True, 1)
    mock_client.eval.assert_called_once()
    mock_client.close.assert_called_once()


def test_acquire_returns_false_when_limit_reached():
    mock_client = MagicMock()
    mock_client.eval.return_value = [0, 2]

    with patch("siphon.concurrency._get_redis_client", return_value=mock_client):
        res = acquire_connection_slot("conn-1", "job-2", limit=2, ttl_seconds=60)

    assert res == (False, 2)
    mock_client.close.assert_called_once()


def test_acquire_returns_none_on_redis_error():
    mock_client = MagicMock()
    mock_client.eval.side_effect = ConnectionError("redis down")

    with patch("siphon.concurrency._get_redis_client", return_value=mock_client):
        res = acquire_connection_slot("conn-1", "job-1", limit=2)

    assert res is None
    mock_client.close.assert_called_once()


def test_release_calls_zrem_and_ignores_errors():
    mock_client = MagicMock()
    with patch("siphon.concurrency._get_redis_client", return_value=mock_client):
        release_connection_slot("conn-1", "job-1")
    mock_client.zrem.assert_called_once_with("siphon:conn_slots:conn-1", "job-1")
    mock_client.close.assert_called_once()

    failing_client = MagicMock()
    failing_client.zrem.side_effect = ConnectionError("redis down")
    with patch("siphon.concurrency._get_redis_client", return_value=failing_client):
        release_connection_slot("conn-1", "job-1")  # must not raise
    failing_client.close.assert_called_once()
