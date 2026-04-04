import time
from unittest.mock import MagicMock, patch

import pytest

from siphon.utils.retry import _with_retry


def test_succeeds_on_first_attempt():
    fn = MagicMock(return_value=42)
    assert _with_retry(fn) == 42
    assert fn.call_count == 1


def test_retries_on_connection_error_then_succeeds():
    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("network down")
        return "ok"

    with patch("siphon.utils.retry.time") as mock_time:
        result = _with_retry(flaky, max_retries=3, backoff_base=1.0)

    assert result == "ok"
    assert call_count == 3
    assert mock_time.sleep.call_count == 2


def test_raises_after_max_retries():
    fn = MagicMock(side_effect=OSError("disk error"))

    with patch("siphon.utils.retry.time"), pytest.raises(OSError, match="disk error"):
        _with_retry(fn, max_retries=2)

    assert fn.call_count == 3  # 1 original + 2 retries


def test_does_not_retry_value_error():
    fn = MagicMock(side_effect=ValueError("bad input"))

    with pytest.raises(ValueError):
        _with_retry(fn, max_retries=3)

    assert fn.call_count == 1  # no retries


def test_does_not_retry_type_error():
    fn = MagicMock(side_effect=TypeError("wrong type"))

    with pytest.raises(TypeError):
        _with_retry(fn, max_retries=3)

    assert fn.call_count == 1
