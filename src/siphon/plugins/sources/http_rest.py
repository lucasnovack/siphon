# src/siphon/plugins/sources/http_rest.py
import time
from collections.abc import Iterator

import pyarrow as pa
import requests
import structlog

from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source

logger = structlog.get_logger()

_session = requests.Session()


@register("http_rest")
class HTTPRestSource(Source):
    def __init__(
        self,
        url: str,
        auth_type: str = "none",
        auth_config: dict | None = None,
        results_key: str | None = None,
        pagination_type: str = "none",
        pagination_config: dict | None = None,
        rate_limit_seconds: float = 0.0,
        max_pages: int = 100,
        headers: dict | None = None,
    ) -> None:
        self.url = url
        self.auth_type = auth_type
        self.auth_config = auth_config or {}
        self.results_key = results_key
        self.pagination_type = pagination_type
        self.pagination_config = pagination_config or {}
        self.rate_limit_seconds = rate_limit_seconds
        self.max_pages = max_pages
        self.headers = headers or {}

    def _build_headers(self) -> dict:
        headers = dict(self.headers)
        if self.auth_type == "bearer":
            headers["Authorization"] = f"Bearer {self.auth_config['token']}"
        elif self.auth_type == "api_key":
            headers[self.auth_config["header"]] = self.auth_config["key"]
        elif self.auth_type == "oauth2_client_credentials":
            token = self._fetch_oauth2_token()
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _fetch_oauth2_token(self) -> str:
        resp = _session.post(
            self.auth_config["token_url"],
            data={
                "grant_type": "client_credentials",
                "client_id": self.auth_config["client_id"],
                "client_secret": self.auth_config["client_secret"],
                "scope": self.auth_config.get("scope", ""),
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _extract_records(self, response_data) -> list:
        if isinstance(response_data, list):
            return response_data
        if self.results_key:
            return response_data[self.results_key]
        return [response_data]

    def extract(self) -> pa.Table:
        batches = list(self.extract_batches())
        if not batches:
            return pa.table({})
        return pa.concat_tables(batches)

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        headers = self._build_headers()
        logger.info("Extracting from %s (auth=%s, pagination=%s)", self.url, self.auth_type, self.pagination_type)
        cfg = self.pagination_config

        if self.pagination_type == "none":
            resp = _session.get(self.url, headers=headers, timeout=30)
            resp.raise_for_status()
            records = self._extract_records(resp.json())
            if records:
                yield pa.Table.from_pylist(records)
            return

        if self.pagination_type == "cursor":
            cursor_key = cfg.get("cursor_key", "cursor")
            next_key = cfg.get("next_key", "next_cursor")
            params: dict = {}
            for _ in range(self.max_pages):
                resp = _session.get(self.url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                records = self._extract_records(data)
                if not records:
                    break
                yield pa.Table.from_pylist(records)
                cursor = data.get(next_key)
                if not cursor:
                    break
                params = {cursor_key: cursor}
                if self.rate_limit_seconds > 0:
                    time.sleep(self.rate_limit_seconds)

        elif self.pagination_type == "page":
            page_param = cfg.get("page_param", "page")
            page_size_param = cfg.get("page_size_param", "page_size")
            page_size = cfg.get("page_size", 100)
            for page_num in range(1, self.max_pages + 1):
                params = {page_param: page_num, page_size_param: page_size}
                resp = _session.get(self.url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                records = self._extract_records(resp.json())
                if not records:
                    break
                yield pa.Table.from_pylist(records)
                if self.rate_limit_seconds > 0:
                    time.sleep(self.rate_limit_seconds)

        elif self.pagination_type == "offset":
            page_size_param = cfg.get("page_size_param", "limit")
            offset_param = cfg.get("offset_param", "offset")
            page_size = cfg.get("page_size", 100)
            offset = 0
            for _ in range(self.max_pages):
                params = {offset_param: offset, page_size_param: page_size}
                resp = _session.get(self.url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                records = self._extract_records(resp.json())
                if not records:
                    break
                yield pa.Table.from_pylist(records)
                offset += len(records)
                if self.rate_limit_seconds > 0:
                    time.sleep(self.rate_limit_seconds)
