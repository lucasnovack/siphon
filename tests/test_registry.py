# tests/test_registry.py
import pyarrow as pa
import pytest

from siphon.plugins.destinations.base import Destination
from siphon.plugins.parsers.base import Parser
from siphon.plugins.sources.base import Source

# ── helpers: inline test plugins ─────────────────────────────────────────────


class _DummySource(Source):
    def extract(self) -> pa.Table:
        return pa.table({"x": [1, 2, 3]})


class _DummyDestination(Destination):
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        return len(table)


class _DummyParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        return pa.table({"raw": [data]})


# ── source registry ───────────────────────────────────────────────────────────


class TestSourceRegistry:
    def test_register_and_get(self):
        from siphon.plugins.sources import get, register

        register("_test_src")(_DummySource)
        cls = get("_test_src")
        assert cls is _DummySource

    def test_get_unknown_raises(self):
        from siphon.plugins.sources import get

        with pytest.raises(ValueError, match="not registered"):
            get("__nonexistent__")

    def test_registered_plugin_is_callable(self):
        from siphon.plugins.sources import get, register

        register("_test_src2")(_DummySource)
        cls = get("_test_src2")
        instance = cls()
        assert isinstance(instance, Source)


# ── destination registry ──────────────────────────────────────────────────────


class TestDestinationRegistry:
    def test_register_and_get(self):
        from siphon.plugins.destinations import get, register

        register("_test_dst")(_DummyDestination)
        cls = get("_test_dst")
        assert cls is _DummyDestination

    def test_get_unknown_raises(self):
        from siphon.plugins.destinations import get

        with pytest.raises(ValueError, match="not registered"):
            get("__nonexistent__")


# ── parser registry ───────────────────────────────────────────────────────────


class TestParserRegistry:
    def test_register_and_get(self):
        from siphon.plugins.parsers import get, register

        register("_test_parser")(_DummyParser)
        cls = get("_test_parser")
        assert cls is _DummyParser

    def test_get_unknown_raises(self):
        from siphon.plugins.parsers import get

        with pytest.raises(ValueError, match="not registered"):
            get("__nonexistent__")


# ── autodiscovery ─────────────────────────────────────────────────────────────


class TestAutodiscovery:
    def test_base_not_registered(self):
        """base.py should never be auto-imported as a plugin."""
        from siphon.plugins.sources import _REGISTRY

        assert "base" not in _REGISTRY

    def test_autodiscovery_does_not_crash_on_reimport(self):
        """Re-importing the registry module should be idempotent."""
        import importlib

        import siphon.plugins.sources as m

        importlib.reload(m)  # should not raise
