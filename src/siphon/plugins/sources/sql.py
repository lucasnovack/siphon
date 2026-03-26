import ipaddress
import logging
import os
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse, urlunparse

import connectorx as cx
import pyarrow as pa

from siphon import variables
from siphon.models import mask_uri
from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = int(os.getenv("SIPHON_CONNECT_TIMEOUT", "30"))
_ALLOWED_HOSTS = os.getenv("SIPHON_ALLOWED_HOSTS", "")


@register("sql")
class SQLSource(Source):
    def __init__(
        self,
        connection: str,
        query: str,
        partition_on: str | None = None,
        partition_num: int | None = None,
        partition_range: tuple[int, int] | None = None,
    ) -> None:
        self.connection = connection
        self.query = query
        self.partition_on = partition_on
        self.partition_num = partition_num
        self.partition_range = partition_range

    def extract(self) -> pa.Table:
        _validate_host(self.connection)
        query = variables.resolve(self.query)

        if self.connection.startswith("oracle"):
            logger.info("Extracting Oracle from %s", mask_uri(self.connection))
            return self._extract_oracle(query)

        conn = _inject_timeout(self.connection)
        logger.info("Extracting from %s", mask_uri(conn))
        return self._extract_connectorx(conn, query)

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        yield self.extract()

    def _extract_connectorx(self, conn: str, query: str) -> pa.Table:
        kwargs: dict = {"return_type": "arrow"}
        if self.partition_on is not None:
            kwargs["partition_on"] = self.partition_on
        if self.partition_num is not None:
            kwargs["partition_num"] = self.partition_num
        if self.partition_range is not None:
            kwargs["partition_range"] = self.partition_range
        return cx.read_sql(conn, query, **kwargs)

    def _extract_oracle(self, query: str) -> pa.Table:  # pragma: no cover
        import oracledb
        import pandas as pd

        conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)
        if self.partition_num and self.partition_num > 1:
            chunks = pd.read_sql(query, conn, chunksize=100_000)
            return pa.concat_tables(
                [pa.Table.from_pandas(chunk, preserve_index=False) for chunk in chunks]
            )
        df = pd.read_sql(query, conn)
        return pa.Table.from_pandas(df, preserve_index=False)

    def _oracle_dsn(self) -> str:  # pragma: no cover
        """Parse oracle+oracledb://user:pass@host:1521/service → user/pass@host:1521/service."""
        parsed = urlparse(self.connection)
        service = parsed.path.lstrip("/")
        port = parsed.port or 1521
        return f"{parsed.username}/{parsed.password}@{parsed.hostname}:{port}/{service}"


def _inject_timeout(conn: str) -> str:
    """Inject connect_timeout into connection string if not present."""
    parsed = urlparse(conn)
    params = parse_qs(parsed.query)
    if "connect_timeout" not in params and "connectTimeout" not in params:
        separator = "&" if parsed.query else ""
        new_query = f"{parsed.query}{separator}connect_timeout={_CONNECT_TIMEOUT}"
        return urlunparse(parsed._replace(query=new_query))
    return conn


def _validate_host(conn: str) -> None:
    """Reject connections to hosts outside the allowlist. Mitigates SSRF."""
    if not _ALLOWED_HOSTS:
        return

    host = urlparse(conn).hostname
    allowed = [h.strip() for h in _ALLOWED_HOSTS.split(",")]

    for entry in allowed:
        try:
            network = ipaddress.ip_network(entry, strict=False)
            if ipaddress.ip_address(host) in network:
                return
        except ValueError:
            if host == entry or host.endswith(f".{entry}"):
                return

    raise ValueError(f"Host '{host}' not in SIPHON_ALLOWED_HOSTS. Connection rejected.")
