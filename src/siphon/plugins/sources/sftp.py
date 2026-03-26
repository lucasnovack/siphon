import base64
import fnmatch
import logging
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager

import paramiko
import pyarrow as pa

from siphon.plugins.parsers import get as get_parser
from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE_MB = int(os.getenv("SIPHON_MAX_FILE_SIZE_MB", "500"))
_MAX_FILE_SIZE_BYTES = _MAX_FILE_SIZE_MB * 1024 * 1024


def _chunked(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


@register("sftp")
class SFTPSource(Source):
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        paths: list[str],
        parser: str,
        skip_patterns: list[str] | None = None,
        max_files: int = 1000,
        chunk_size: int = 100,
        fail_fast: bool = False,
        processing_folder: str | None = None,
        processed_folder: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.paths = paths
        self.parser = parser
        self.skip_patterns = skip_patterns if skip_patterns is not None else ["TMP_*"]
        self.max_files = max_files
        self.chunk_size = chunk_size
        self.fail_fast = fail_fast
        self.processing_folder = processing_folder
        self.processed_folder = processed_folder
        self._parser = get_parser(parser)()
        self.failed_files: list[str] = []

    def extract(self) -> pa.Table:
        tables = list(self.extract_batches(chunk_size=self.max_files))
        if not tables:
            return pa.table({})
        return pa.concat_tables(tables)

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        self.failed_files = []
        with self._single_connection() as sftp:
            files = self._list_and_filter(sftp)
            logger.info("Found %d files to process across %d paths", len(files), len(self.paths))
            if self.processing_folder:
                files = self._move_to_processing(sftp, files)
            for chunk in _chunked(files, chunk_size):
                tables = []
                for f in chunk:
                    try:
                        data = self._download_with_retry(sftp, f)
                        table = self._parser.parse(data)
                        tables.append(table)
                        if self.processed_folder:
                            self._move_to_processed(sftp, f)
                        logger.info("Processed %s (%d rows)", f, table.num_rows)
                    except Exception as exc:
                        if self.fail_fast:
                            raise
                        logger.warning("Failed to process %s: %s", f, exc)
                        self.failed_files.append(f)
                if tables:
                    yield pa.concat_tables(tables)

    @contextmanager
    def _single_connection(self):
        """Open a single SSH connection for the duration of the job."""
        ssh = paramiko.SSHClient()
        known_hosts = os.getenv("SIPHON_SFTP_KNOWN_HOSTS")
        host_key_b64 = os.getenv("SIPHON_SFTP_HOST_KEY")
        if known_hosts:
            ssh.load_host_keys(known_hosts)
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif host_key_b64:
            key = paramiko.RSAKey(data=base64.b64decode(host_key_b64))
            ssh.get_host_keys().add(self.host, "ssh-rsa", key)
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        ssh.connect(self.host, port=self.port, username=self.username, password=self.password)
        sftp = ssh.open_sftp()
        try:
            yield sftp
        finally:
            sftp.close()
            ssh.close()

    def _list_and_filter(self, sftp) -> list[str]:
        result = []
        for remote_dir in self.paths:
            try:
                entries = sftp.listdir_attr(remote_dir)
            except FileNotFoundError:
                logger.warning("Remote path not found: %s", remote_dir)
                continue
            for entry in entries:
                if stat.S_ISDIR(entry.st_mode or 0):
                    continue
                filename = entry.filename
                if any(fnmatch.fnmatch(filename, pat) for pat in self.skip_patterns):
                    logger.debug("Skipping %s (matches skip_patterns)", filename)
                    continue
                size = entry.st_size or 0
                if size > _MAX_FILE_SIZE_BYTES:
                    logger.warning(
                        "Skipping %s (%d MB exceeds %d MB limit)",
                        filename, size // (1024 * 1024), _MAX_FILE_SIZE_MB,
                    )
                    continue
                result.append(f"{remote_dir.rstrip('/')}/{filename}")
            if len(result) >= self.max_files:
                result = result[: self.max_files]
                break
        return result

    def _move_to_processing(self, sftp, files: list[str]) -> list[str]:
        new_paths = []
        for path in files:
            filename = os.path.basename(path)
            new_path = f"{self.processing_folder.rstrip('/')}/{filename}"
            sftp.rename(path, new_path)
            new_paths.append(new_path)
        return new_paths

    def _move_to_processed(self, sftp, path: str) -> None:
        filename = os.path.basename(path)
        new_path = f"{self.processed_folder.rstrip('/')}/{filename}"
        sftp.rename(path, new_path)

    def _download_with_retry(self, sftp, path: str, max_retries: int = 3) -> bytes:
        delay = 1.0
        for attempt in range(max_retries + 1):
            try:
                with sftp.open(path, "rb") as f:
                    return f.read()
            except Exception as exc:
                if attempt == max_retries:
                    raise
                logger.warning(
                    "Download attempt %d/%d failed for %s: %s",
                    attempt + 1, max_retries, path, exc,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30.0)
        raise RuntimeError("unreachable")  # pragma: no cover
