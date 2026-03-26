# tests/test_sftp_source.py
"""Unit tests for SFTPSource — no real network connections."""
import stat as stat_mod
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest

from siphon.plugins.parsers import get as get_parser
from siphon.plugins.sources import get as get_source


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_sftp_entry(filename: str, size: int = 100, is_dir: bool = False):
    entry = MagicMock()
    entry.filename = filename
    entry.st_size = size
    entry.st_mode = stat_mod.S_IFDIR if is_dir else stat_mod.S_IFREG
    return entry


def _make_source(**kwargs):
    cls = get_source("sftp")
    defaults = dict(
        host="sftp.test", port=22, username="u", password="p",
        paths=["/data"], parser="example_parser",
    )
    defaults.update(kwargs)
    return cls(**defaults)


def _mock_sftp_source(source, sftp_mock):
    """Patch _single_connection on instance to yield sftp_mock."""
    @contextmanager
    def fake_conn():
        yield sftp_mock
    source._single_connection = fake_conn
    return source


# ── registry checks ───────────────────────────────────────────────────────────


class TestRegistration:
    def test_sftp_source_is_registered(self):
        cls = get_source("sftp")
        assert cls.__name__ == "SFTPSource"

    def test_example_parser_is_registered(self):
        cls = get_parser("example_parser")
        assert cls.__name__ == "ExampleParser"


# ── ExampleParser ─────────────────────────────────────────────────────────────


class TestExampleParser:
    def test_example_parser_returns_table(self):
        cls = get_parser("example_parser")
        parser = cls()
        result = parser.parse(b"data")
        assert isinstance(result, pa.Table)
        assert "raw" in result.schema.names
        assert result.num_rows == 1


# ── skip_patterns ─────────────────────────────────────────────────────────────


class TestSkipPatterns:
    def test_skip_patterns_exclude_matching_files(self):
        """Default TMP_* pattern excludes files beginning with TMP_."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("TMP_file.csv"),
            _make_sftp_entry("good_file.csv"),
        ]
        src = _make_source()
        _mock_sftp_source(src, sftp)
        files = src._list_and_filter(sftp)
        assert files == ["/data/good_file.csv"]

    def test_skip_patterns_custom(self):
        """Custom skip patterns work correctly."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("SKIP_me.csv"),
            _make_sftp_entry("keep_me.csv"),
        ]
        src = _make_source(skip_patterns=["SKIP_*"])
        _mock_sftp_source(src, sftp)
        files = src._list_and_filter(sftp)
        assert files == ["/data/keep_me.csv"]


# ── file size limit ───────────────────────────────────────────────────────────


class TestFileSizeLimit:
    def test_file_size_limit_skips_large_files(self):
        """Files exceeding the size limit are skipped."""
        big = 600 * 1024 * 1024  # 600 MB
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("big_file.bin", size=big),
        ]
        src = _make_source()
        files = src._list_and_filter(sftp)
        assert files == []

    def test_file_size_limit_allows_under_limit(self):
        """Files under the size limit are included."""
        small = 10 * 1024 * 1024  # 10 MB
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("small_file.bin", size=small),
        ]
        src = _make_source()
        files = src._list_and_filter(sftp)
        assert files == ["/data/small_file.bin"]


# ── fail_fast / partial success ───────────────────────────────────────────────


class TestFailFast:
    def test_partial_success_collects_failed_files(self):
        """fail_fast=False continues on error and collects failed_files."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("good.bin"),
            _make_sftp_entry("bad.bin"),
        ]
        # First open succeeds, second raises
        good_fh = MagicMock()
        good_fh.__enter__ = MagicMock(return_value=good_fh)
        good_fh.__exit__ = MagicMock(return_value=False)
        good_fh.read.return_value = b"hello"

        call_count = {"n": 0}

        def open_side_effect(path, mode):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return good_fh
            raise IOError("connection reset")

        sftp.open.side_effect = open_side_effect

        src = _make_source(fail_fast=False)
        _mock_sftp_source(src, sftp)

        batches = list(src.extract_batches())
        assert len(src.failed_files) == 1
        assert src.failed_files[0].endswith("bad.bin")
        assert len(batches) == 1  # only good file produced a batch

    def test_fail_fast_raises_on_first_error(self):
        """fail_fast=True raises immediately on the first error."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("bad.bin"),
        ]
        sftp.open.side_effect = IOError("connection reset")

        src = _make_source(fail_fast=True)
        _mock_sftp_source(src, sftp)

        with pytest.raises(IOError, match="connection reset"):
            list(src.extract_batches())


# ── move helpers ──────────────────────────────────────────────────────────────


class TestMoveHelpers:
    def test_move_to_processing_renames_files(self):
        sftp = MagicMock()
        src = _make_source(processing_folder="/proc")
        new_paths = src._move_to_processing(sftp, ["/data/file1.bin", "/data/file2.bin"])
        assert new_paths == ["/proc/file1.bin", "/proc/file2.bin"]
        sftp.rename.assert_any_call("/data/file1.bin", "/proc/file1.bin")
        sftp.rename.assert_any_call("/data/file2.bin", "/proc/file2.bin")

    def test_move_to_processed_renames_file(self):
        sftp = MagicMock()
        src = _make_source(processed_folder="/done")
        src._move_to_processed(sftp, "/proc/file1.bin")
        sftp.rename.assert_called_once_with("/proc/file1.bin", "/done/file1.bin")


class TestMoveBackToOrigin:
    def test_failed_file_moved_back_when_processing_folder_set(self):
        """When processing_folder is set and a file fails, it must be moved back to origin."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("good.bin"),
            _make_sftp_entry("bad.bin"),
        ]

        good_fh = MagicMock()
        good_fh.__enter__ = MagicMock(return_value=good_fh)
        good_fh.__exit__ = MagicMock(return_value=False)
        good_fh.read.return_value = b"ok"

        call_count = {"n": 0}

        def open_side_effect(path, mode):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return good_fh
            raise IOError("parse failure")

        sftp.open.side_effect = open_side_effect

        src = _make_source(
            fail_fast=False,
            processing_folder="/processing",
        )
        _mock_sftp_source(src, sftp)

        list(src.extract_batches())

        # bad.bin was in /processing/bad.bin after the initial move.
        # On failure it must be renamed back to /data/bad.bin.
        rename_calls = [str(c) for c in sftp.rename.call_args_list]
        move_back = any(
            "processing/bad.bin" in c and "/data/bad.bin" in c
            for c in rename_calls
        )
        assert move_back, (
            f"Expected rename from /processing/bad.bin to /data/bad.bin. "
            f"Actual rename calls: {rename_calls}"
        )

        # bad.bin must still appear in failed_files
        assert any("bad.bin" in f for f in src.failed_files)

    def test_no_move_back_when_no_processing_folder(self):
        """Without processing_folder, no rename-back should happen on failure."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [_make_sftp_entry("bad.bin")]
        sftp.open.side_effect = IOError("boom")

        src = _make_source(fail_fast=False)  # no processing_folder
        _mock_sftp_source(src, sftp)

        list(src.extract_batches())

        # rename should never have been called (no processing_folder)
        sftp.rename.assert_not_called()
        assert any("bad.bin" in f for f in src.failed_files)


# ── chunking ──────────────────────────────────────────────────────────────────


class TestChunking:
    def test_extract_batches_yields_chunks(self):
        """extract_batches yields one batch per chunk of files."""
        sftp = MagicMock()
        # 3 files, chunk_size=2 → 2 batches
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("a.bin"),
            _make_sftp_entry("b.bin"),
            _make_sftp_entry("c.bin"),
        ]
        fh = MagicMock()
        fh.__enter__ = MagicMock(return_value=fh)
        fh.__exit__ = MagicMock(return_value=False)
        fh.read.return_value = b"x"
        sftp.open.return_value = fh

        src = _make_source(chunk_size=2)
        _mock_sftp_source(src, sftp)

        batches = list(src.extract_batches(chunk_size=2))
        assert len(batches) == 2
        assert all(isinstance(b, pa.Table) for b in batches)


# ── retry ─────────────────────────────────────────────────────────────────────


class TestRetry:
    def test_download_retry_on_failure(self):
        """_download_with_retry retries on IOError and succeeds eventually."""
        sftp = MagicMock()
        call_count = {"n": 0}

        good_fh = MagicMock()
        good_fh.__enter__ = MagicMock(return_value=good_fh)
        good_fh.__exit__ = MagicMock(return_value=False)
        good_fh.read.return_value = b"success"

        def open_side_effect(path, mode):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise IOError("transient")
            return good_fh

        sftp.open.side_effect = open_side_effect

        src = _make_source()
        with patch("time.sleep"):  # avoid real sleeping
            result = src._download_with_retry(sftp, "/data/file.bin")

        assert result == b"success"
        assert call_count["n"] == 3
