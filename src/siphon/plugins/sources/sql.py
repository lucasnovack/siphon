import ipaddress
import logging
import os
from collections.abc import Iterator
from datetime import datetime
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
_ORACLE_CHUNK_SIZE = int(os.getenv("SIPHON_ORACLE_CHUNK_SIZE", "50000"))


def _oracle_rows_to_arrow(rows: list, description) -> pa.Table:
    columns = [col[0].lower() for col in description]
    arrays = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
    return pa.Table.from_pydict(arrays)


def _oracle_output_type_handler(cursor, name, default_type, size, precision, scale):
    import oracledb

    if default_type == oracledb.DB_TYPE_NUMBER:
        if scale == 0:
            return cursor.var(int, arraysize=cursor.arraysize)
        return cursor.var(float, arraysize=cursor.arraysize)
    if default_type in (oracledb.DB_TYPE_DATE, oracledb.DB_TYPE_TIMESTAMP):
        return cursor.var(datetime, arraysize=cursor.arraysize)
    if default_type == oracledb.DB_TYPE_CLOB:
        logger.warning(
            "CLOB column '%s' detected — large values may cause memory spikes within a chunk",
            name,
        )
        return cursor.var(str, arraysize=cursor.arraysize)
    if default_type == oracledb.DB_TYPE_BLOB:
        return cursor.var(bytes, arraysize=cursor.arraysize)


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
            return pa.concat_tables(list(self._extract_oracle_batches(query)))

        conn = _inject_timeout(self.connection)
        logger.info("Extracting from %s", mask_uri(conn))
        return self._extract_connectorx(conn, query)

    def extract_batches(self, chunk_size: int = _ORACLE_CHUNK_SIZE) -> Iterator[pa.Table]:
        _validate_host(self.connection)
        query = variables.resolve(self.query)

        if self.connection.startswith("oracle"):
            yield from self._extract_oracle_batches(query, chunk_size)
        else:
            yield self._extract_connectorx(_inject_timeout(self.connection), query)

    def _extract_connectorx(self, conn: str, query: str) -> pa.Table:
        kwargs: dict = {"return_type": "arrow"}
        if self.partition_on is not None:
            kwargs["partition_on"] = self.partition_on
        if self.partition_num is not None:
            kwargs["partition_num"] = self.partition_num
        if self.partition_range is not None:
            kwargs["partition_range"] = self.partition_range
        return cx.read_sql(conn, query, **kwargs)

    def _extract_oracle_batches(
        self, query: str, chunk_size: int = _ORACLE_CHUNK_SIZE
    ) -> Iterator[pa.Table]:
        import oracledb

        if self.partition_on is not None or self.partition_num is not None:
            logger.warning(
                "Oracle partition_on/partition_num are not supported in streaming mode and will be ignored. "
                "Parallel Oracle partitioning is planned for a future phase."
            )

        conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)
        conn.outputtypehandler = _oracle_output_type_handler
        with conn, conn.cursor() as cursor:
            cursor.arraysize = chunk_size
            cursor.execute(query)
            while rows := cursor.fetchmany(chunk_size):
                yield _oracle_rows_to_arrow(rows, cursor.description)

    def _oracle_dsn(self) -> str:
        """Parse oracle+oracledb://user:pass@host:1521/service → user/pass@host:1521/service."""
        parsed = urlparse(self.connection)
        service = parsed.path.lstrip("/")
        port = parsed.port or 1521
        return f"{parsed.username}/{parsed.password}@{parsed.hostname}:{port}/{service}"


def _inject_timeout(conn: str) -> str:
    """Inject connect_timeout into connection string for PostgreSQL only.

    ConnectorX's MySQL driver does not support connect_timeout as a URL parameter.
    Only PostgreSQL (libpq-backed) supports it.
    """
    parsed = urlparse(conn)
    if not parsed.scheme.startswith("postgres"):
        return conn
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
