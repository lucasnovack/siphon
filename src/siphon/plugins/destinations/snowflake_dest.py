# src/siphon/plugins/destinations/snowflake_dest.py
import importlib

import pyarrow as pa
import structlog

from siphon.plugins.destinations import register
from siphon.plugins.destinations.base import Destination

logger = structlog.get_logger()


@register("snowflake")
class SnowflakeDestination(Destination):
    def __init__(
        self,
        account: str,
        user: str,
        password: str,
        database: str,
        schema: str,
        warehouse: str,
        table: str,
        write_mode: str = "append",
        job_id: str = "",
    ) -> None:
        self.account = account
        self.user = user
        self.password = password
        self.database = database
        self.schema = schema
        self.warehouse = warehouse
        self.table = table
        self.write_mode = write_mode
        # job_id accepted but unused — Snowflake does not use staging paths

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        sf = importlib.import_module("snowflake.connector")
        write_pandas = importlib.import_module("snowflake.connector.pandas_tools").write_pandas

        conn = sf.connect(
            account=self.account,
            user=self.user,
            password=self.password,
            database=self.database,
            schema=self.schema,
            warehouse=self.warehouse,
        )
        try:
            df = table.to_pandas()
            overwrite = self.write_mode == "replace" and is_first_chunk
            table_name = self.table.upper()
            logger.info(
                "Writing %d rows to Snowflake %s.%s.%s (mode=%s, overwrite=%s)",
                table.num_rows, self.database, self.schema, table_name,
                self.write_mode, overwrite,
            )
            success, nchunks, nrows, _ = write_pandas(
                conn,
                table_name,
                overwrite=overwrite,
                auto_create_table=True,
                df=df,
            )
            if not success:
                raise RuntimeError(
                    f"Snowflake write failed: {nchunks} chunks, {nrows} rows written"
                )
            return table.num_rows
        finally:
            conn.close()
