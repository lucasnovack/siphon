"""
Integration tests for SFTPSource.
Requires atmoz/sftp container from docker-compose.yml:
    docker compose up -d sftp
Run with:
    pytest tests/test_integration_sftp.py -m integration
"""

import pytest

pytestmark = pytest.mark.integration

SFTP_HOST = "localhost"
SFTP_PORT = 2222
SFTP_USER = "siphon"
SFTP_PASS = "siphon"
SFTP_PATH = "/upload"


@pytest.fixture(scope="module")
def sftp_available():
    try:
        import paramiko

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS, timeout=3)
        ssh.close()
    except Exception as exc:
        pytest.skip(f"SFTP not available: {exc}")


def test_sftp_extract_stub_files(sftp_available):
    """SFTPSource with example_parser extracts files and returns Arrow Tables."""
    import io
    import os
    from contextlib import contextmanager

    import paramiko
    import paramiko as pm
    import pyarrow as pa

    from siphon.plugins.sources import get as get_source

    # Upload a test file first
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS)
    sftp = ssh.open_sftp()
    sftp.putfo(io.BytesIO(b"hello world"), f"{SFTP_PATH}/test_file.bin")
    sftp.close()
    ssh.close()

    os.environ["SIPHON_SFTP_KNOWN_HOSTS"] = ""  # skip for integration test

    cls = get_source("sftp")
    src = cls(
        host=SFTP_HOST,
        port=SFTP_PORT,
        username=SFTP_USER,
        password=SFTP_PASS,
        paths=[SFTP_PATH],
        parser="example_parser",
    )

    # Override _single_connection to use AutoAddPolicy for test
    @contextmanager
    def test_connection():
        ssh2 = pm.SSHClient()
        ssh2.set_missing_host_key_policy(pm.AutoAddPolicy())
        ssh2.connect(SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS)
        s = ssh2.open_sftp()
        try:
            yield s
        finally:
            s.close()
            ssh2.close()

    src._single_connection = test_connection

    batches = list(src.extract_batches())
    assert len(batches) >= 1
    assert all(isinstance(b, pa.Table) for b in batches)
