# src/siphon/plugins/sources/__init__.py
import importlib
import pkgutil
from pathlib import Path

from siphon.plugins.sources.base import Source

_REGISTRY: dict[str, type[Source]] = {}


def register(name: str):
    """Decorator to register a Source plugin under a given name."""
    def decorator(cls: type[Source]) -> type[Source]:
        _REGISTRY[name] = cls
        return cls
    return decorator


def get(name: str) -> type[Source]:
    """Retrieve a registered Source class by name."""
    if name not in _REGISTRY:
        available = list(_REGISTRY)
        raise ValueError(f"Source '{name}' not registered. Available: {available}")
    return _REGISTRY[name]


def _autodiscover() -> None:
    """Import all modules in this package (except base) to trigger @register decorators."""
    package_dir = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        if module_name != "base":
            importlib.import_module(f"{__name__}.{module_name}")


_autodiscover()
