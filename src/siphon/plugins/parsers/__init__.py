# src/siphon/plugins/parsers/__init__.py
import importlib
import pkgutil
from pathlib import Path

from siphon.plugins.parsers.base import Parser

_REGISTRY: dict[str, type[Parser]] = {}


def register(name: str):
    """Decorator to register a Parser plugin under a given name."""

    def decorator(cls: type[Parser]) -> type[Parser]:
        _REGISTRY[name] = cls
        return cls

    return decorator


def get(name: str) -> type[Parser]:
    """Retrieve a registered Parser class by name."""
    if name not in _REGISTRY:
        available = list(_REGISTRY)
        raise ValueError(f"Parser '{name}' not registered. Available: {available}")
    return _REGISTRY[name]


def _autodiscover() -> None:
    package_dir = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        if module_name != "base":
            importlib.import_module(f"{__name__}.{module_name}")


_autodiscover()
