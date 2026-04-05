# tests/test_http_rest_source.py
import time
from unittest.mock import MagicMock, call, patch

import pyarrow as pa
import pytest

from siphon.plugins.sources import get as get_source


def _make_response(json_data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


def _src(**kwargs):
    cls = get_source("http_rest")
    defaults = {"url": "https://api.example.com/data"}
    defaults.update(kwargs)
    return cls(**defaults)


# ── Registry ──────────────────────────────────────────────────────────────────


def test_http_rest_is_registered():
    cls = get_source("http_rest")
    assert cls.__name__ == "HTTPRestSource"


# ── No auth, no pagination ─────────────────────────────────────────────────────


def test_no_auth_list_response():
    src = _src()
    payload = [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]
    with patch("requests.get", return_value=_make_response(payload)):
        table = src.extract()
    assert table.num_rows == 2
    assert table.column("id").to_pylist() == [1, 2]


def test_no_auth_dict_with_results_key():
    src = _src(results_key="data")
    payload = {"data": [{"id": 1}], "meta": {"total": 1}}
    with patch("requests.get", return_value=_make_response(payload)):
        table = src.extract()
    assert table.num_rows == 1


def test_empty_results_returns_empty_table():
    src = _src(results_key="items")
    payload = {"items": []}
    with patch("requests.get", return_value=_make_response(payload)):
        batches = list(src.extract_batches())
    assert batches == []


# ── Auth headers ──────────────────────────────────────────────────────────────


def test_bearer_auth_sets_authorization_header():
    src = _src(auth_type="bearer", auth_config={"token": "tok123"})
    with patch("requests.get", return_value=_make_response([{"x": 1}])) as mock_get:
        src.extract()
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok123"


def test_api_key_auth_sets_custom_header():
    src = _src(
        auth_type="api_key",
        auth_config={"header": "X-API-Key", "key": "secret"},
    )
    with patch("requests.get", return_value=_make_response([{"x": 1}])) as mock_get:
        src.extract()
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["X-API-Key"] == "secret"


def test_oauth2_fetches_token_then_calls_api():
    src = _src(
        auth_type="oauth2_client_credentials",
        auth_config={
            "token_url": "https://auth.example.com/token",
            "client_id": "cid",
            "client_secret": "csec",
        },
    )
    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "oauth_token_xyz"}
    token_resp.raise_for_status = MagicMock()

    api_resp = _make_response([{"id": 1}])

    with patch("requests.post", return_value=token_resp) as mock_post, \
         patch("requests.get", return_value=api_resp) as mock_get:
        src.extract()

    mock_post.assert_called_once()
    post_kwargs = mock_post.call_args
    assert post_kwargs[0][0] == "https://auth.example.com/token"
    assert post_kwargs[1]["data"]["grant_type"] == "client_credentials"

    _, get_kwargs = mock_get.call_args
    assert get_kwargs["headers"]["Authorization"] == "Bearer oauth_token_xyz"


# ── Pagination ────────────────────────────────────────────────────────────────


def test_cursor_pagination_follows_next_cursor():
    src = _src(
        results_key="items",
        pagination_type="cursor",
        pagination_config={"cursor_key": "after", "next_key": "next_cursor"},
    )
    responses = [
        _make_response({"items": [{"id": 1}], "next_cursor": "abc"}),
        _make_response({"items": [{"id": 2}], "next_cursor": "def"}),
        _make_response({"items": [], "next_cursor": None}),
    ]
    with patch("requests.get", side_effect=responses) as mock_get:
        batches = list(src.extract_batches())

    assert len(batches) == 2
    assert batches[0].column("id").to_pylist() == [1]
    assert batches[1].column("id").to_pylist() == [2]
    calls = mock_get.call_args_list
    assert calls[1][1]["params"] == {"after": "abc"}
    assert calls[2][1]["params"] == {"after": "def"}
    assert mock_get.call_count == 3  # two pages + one empty fetch to detect end


def test_page_pagination_increments_page_number():
    src = _src(
        results_key="rows",
        pagination_type="page",
        pagination_config={"page_param": "page", "page_size_param": "per_page", "page_size": 2},
    )
    responses = [
        _make_response({"rows": [{"id": 1}, {"id": 2}]}),
        _make_response({"rows": [{"id": 3}]}),
        _make_response({"rows": []}),
    ]
    with patch("requests.get", side_effect=responses) as mock_get:
        batches = list(src.extract_batches())

    assert len(batches) == 2
    calls = mock_get.call_args_list
    assert calls[0][1]["params"] == {"page": 1, "per_page": 2}
    assert calls[1][1]["params"] == {"page": 2, "per_page": 2}


def test_offset_pagination_increments_offset_by_record_count():
    src = _src(
        results_key="data",
        pagination_type="offset",
        pagination_config={"offset_param": "offset", "page_size_param": "limit", "page_size": 2},
    )
    responses = [
        _make_response({"data": [{"id": 1}, {"id": 2}]}),
        _make_response({"data": [{"id": 3}]}),
        _make_response({"data": []}),
    ]
    with patch("requests.get", side_effect=responses) as mock_get:
        batches = list(src.extract_batches())

    assert len(batches) == 2
    calls = mock_get.call_args_list
    assert calls[0][1]["params"] == {"offset": 0, "limit": 2}
    assert calls[1][1]["params"] == {"offset": 2, "limit": 2}


def test_max_pages_stops_pagination():
    src = _src(
        results_key="items",
        pagination_type="cursor",
        pagination_config={"cursor_key": "after", "next_key": "next"},
        max_pages=2,
    )
    always_more = _make_response({"items": [{"id": 1}], "next": "cursor_x"})
    with patch("requests.get", return_value=always_more) as mock_get:
        batches = list(src.extract_batches())

    assert len(batches) == 2
    assert mock_get.call_count == 2


def test_rate_limit_calls_sleep_between_pages():
    src = _src(
        results_key="items",
        pagination_type="page",
        pagination_config={"page_param": "p", "page_size_param": "s", "page_size": 1},
        rate_limit_seconds=0.5,
    )
    responses = [
        _make_response({"items": [{"id": 1}]}),
        _make_response({"items": []}),
    ]
    with patch("requests.get", side_effect=responses), \
         patch("siphon.plugins.sources.http_rest.time.sleep") as mock_sleep:
        list(src.extract_batches())

    mock_sleep.assert_called_once_with(0.5)
