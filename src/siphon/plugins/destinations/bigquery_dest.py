# src/siphon/plugins/destinations/bigquery_dest.py
import importlib
import json

import pyarrow as pa
import structlog

from siphon.plugins.destinations import register
from siphon.plugins.destinations.base import Destination

logger = structlog.get_logger()


@register("bigquery")
class BigQueryDestination(Destination):
    def __init__(
        self,
        project: str,
        dataset: str,
        table: str,
        credentials_json: str,
        write_mode: str = "append",
        location: str = "US",
        job_id: str = "",
    ) -> None:
        self.project = project
        self.dataset = dataset
        self.table = table
        self.credentials_json = credentials_json
        self.write_mode = write_mode
        self.location = location
        # job_id accepted but unused — BigQuery does not use staging paths

    def _make_client(self):
        bigquery = importlib.import_module("google.cloud.bigquery")
        service_account = importlib.import_module("google.oauth2.service_account")

        creds = service_account.Credentials.from_service_account_info(
            json.loads(self.credentials_json),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return bigquery.Client(project=self.project, credentials=creds, location=self.location)

    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        bigquery = importlib.import_module("google.cloud.bigquery")

        client = self._make_client()
        table_ref = f"{self.project}.{self.dataset}.{self.table}"

        if self.write_mode == "replace" and is_first_chunk:
            disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
        else:
            disposition = bigquery.WriteDisposition.WRITE_APPEND

        job_config = bigquery.LoadJobConfig(write_disposition=disposition)

        df = table.to_pandas()
        logger.info(
            "Writing %d rows to BigQuery %s (mode=%s)",
            table.num_rows, table_ref, self.write_mode,
        )
        job = client.load_table_from_dataframe(df, table_ref, job_config=job_config)
        job.result()
        return table.num_rows
