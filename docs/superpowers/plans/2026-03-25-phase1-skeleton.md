# Siphon Phase 1 — Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffolding completo do projeto — estrutura de pastas, modelos Pydantic, ABCs de plugins, registry com autodiscovery, e resolver de variáveis de data. Zero lógica de negócio. Todos os testes passando.

**Architecture:** Monorepo Python com `src/siphon/` como pacote principal. Plugins em `src/siphon/plugins/{sources,destinations,parsers}/` com registry por autodiscovery via `pkgutil`. Modelos Pydantic com discriminated unions. Job dataclass interno sem credenciais.

**Tech Stack:** Python 3.12+, uv, pyarrow, pydantic v2, pytest, ruff

**Spec:** `claude.md` §6 (Data Models), §7 (Plugin Contracts), §4 (Registry + Autodiscovery)

---

## File Map

| Arquivo | Responsabilidade |
|---|---|
| `pyproject.toml` | Deps, ruff config, pytest config |
| `src/siphon/__init__.py` | Pacote vazio |
| `src/siphon/models.py` | Todos os modelos Pydantic + Job dataclass + mask_uri |
| `src/siphon/variables.py` | Resolver @TODAY, @MIN_DATE, @LAST_MONTH, @NEXT_MONTH com timezone |
| `src/siphon/plugins/__init__.py` | Pacote vazio |
| `src/siphon/plugins/sources/__init__.py` | Registry + autodiscovery para sources |
| `src/siphon/plugins/sources/base.py` | ABC Source |
| `src/siphon/plugins/destinations/__init__.py` | Registry + autodiscovery para destinations |
| `src/siphon/plugins/destinations/base.py` | ABC Destination |
| `src/siphon/plugins/parsers/__init__.py` | Registry + autodiscovery para parsers |
| `src/siphon/plugins/parsers/base.py` | ABC Parser |
| `tests/__init__.py` | Pacote vazio |
| `tests/test_variables.py` | Testes do resolver de variáveis |
| `tests/test_registry.py` | Testes do registry + autodiscovery |
| `tests/test_models.py` | Testes dos modelos Pydantic e mask_uri |

---

## Task 1: Inicializar projeto com uv

**Files:**
- Create: `pyproject.toml`
- Create: `src/siphon/__init__.py`
- Create: `src/siphon/plugins/__init__.py`
- Create: `src/siphon/plugins/sources/__init__.py` (vazio por ora)
- Create: `src/siphon/plugins/destinations/__init__.py` (vazio por ora)
- Create: `src/siphon/plugins/parsers/__init__.py` (vazio por ora)
- Create: `tests/__init__.py`

- [ ] **Step 1: Inicializar projeto com uv**

```bash
cd /home/lucasnvk/projects/siphon
uv init --name siphon --python 3.12
```

- [ ] **Step 2: Remover o `hello.py` gerado pelo uv init e criar estrutura src**

```bash
rm -f hello.py
mkdir -p src/siphon/plugins/sources
mkdir -p src/siphon/plugins/destinations
mkdir -p src/siphon/plugins/parsers
mkdir -p tests
touch src/siphon/__init__.py
touch src/siphon/plugins/__init__.py
touch src/siphon/plugins/sources/__init__.py
touch src/siphon/plugins/destinations/__init__.py
touch src/siphon/plugins/parsers/__init__.py
touch tests/__init__.py
```

- [ ] **Step 3: Configurar `pyproject.toml` com todas as dependências e ferramentas**

Substituir o conteúdo do `pyproject.toml` gerado por:

```toml
[project]
name = "siphon"
version = "0.1.0"
description = "Lightweight data extraction service — replaces Spark for Bronze layer ETL"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pyarrow>=18.0",
    "connectorx>=0.3",
    "pandas>=2.2",
    "oracledb>=2.4",
    "paramiko>=3.5",
    "python-dateutil>=2.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/siphon"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
ignore = ["E501"]

[tool.ruff.lint.isort]
known-first-party = ["siphon"]
```

- [ ] **Step 4: Instalar dependências**

```bash
uv sync --extra dev
```

Expected: dependências instaladas em `.venv/` sem erros.

- [ ] **Step 5: Verificar que o ambiente funciona**

```bash
uv run python -c "import pyarrow; import pydantic; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Criar `.gitignore` antes do primeiro commit**

```bash
cat > .gitignore << 'EOF'
.venv/
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.ruff_cache/
dist/
*.egg-info/
.env
uv.lock
EOF
```

- [ ] **Step 7: Commit**

```bash
git init
git add pyproject.toml .gitignore src/ tests/
git commit -m "chore: initialize siphon project with uv"
```

---

## Task 2: `variables.py` — resolver de variáveis de data

**Files:**
- Create: `src/siphon/variables.py`
- Create: `tests/test_variables.py`

**Spec:** `claude.md` §9 — seção "Timezone: @TODAY e timestamps"

- [ ] **Step 1: Escrever testes primeiro**

```python
# tests/test_variables.py
import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from siphon.variables import resolve


def _fixed_now(year=2026, month=3, day=25, hour=10, tz="America/Sao_Paulo"):
    return datetime(year, month, day, hour, 0, 0, tzinfo=ZoneInfo(tz))


class TestResolve:
    def test_today_replaced(self):
        with patch("siphon.variables._now", return_value=_fixed_now()):
            result = resolve("SELECT * FROM t WHERE dt = @TODAY")
        assert result == "SELECT * FROM t WHERE dt = '2026-03-25'"

    def test_min_date_replaced(self):
        result = resolve("WHERE dt > @MIN_DATE")
        assert result == "WHERE dt > '1997-01-01'"

    def test_last_month_replaced(self):
        with patch("siphon.variables._now", return_value=_fixed_now(month=3)):
            result = resolve("WHERE dt >= @LAST_MONTH")
        assert result == "WHERE dt >= '2026-02-01'"

    def test_next_month_replaced(self):
        with patch("siphon.variables._now", return_value=_fixed_now(month=3)):
            result = resolve("WHERE dt < @NEXT_MONTH")
        assert result == "WHERE dt < '2026-04-01'"

    def test_multiple_variables_in_same_query(self):
        with patch("siphon.variables._now", return_value=_fixed_now()):
            result = resolve("WHERE dt BETWEEN @LAST_MONTH AND @TODAY")
        assert result == "WHERE dt BETWEEN '2026-02-01' AND '2026-03-25'"

    def test_no_variables_unchanged(self):
        query = "SELECT id, name FROM users"
        assert resolve(query) == query

    def test_timezone_env_var_respected(self):
        # UTC midnight = still yesterday in Sao Paulo (UTC-3)
        utc_midnight = datetime(2026, 3, 26, 0, 30, 0, tzinfo=ZoneInfo("UTC"))
        sp_tz = ZoneInfo("America/Sao_Paulo")
        expected_sp_date = utc_midnight.astimezone(sp_tz).strftime("'%Y-%m-%d'")
        with patch("siphon.variables._now", return_value=utc_midnight.astimezone(sp_tz)):
            result = resolve("@TODAY")
        assert result == expected_sp_date

    def test_december_last_month_wraps_year(self):
        # January → last month should be December of previous year
        with patch("siphon.variables._now", return_value=_fixed_now(year=2026, month=1)):
            result = resolve("@LAST_MONTH")
        assert result == "'2025-12-01'"

    def test_december_next_month_wraps_year(self):
        # December → next month should be January of next year
        with patch("siphon.variables._now", return_value=_fixed_now(year=2025, month=12)):
            result = resolve("@NEXT_MONTH")
        assert result == "'2026-01-01'"
```

- [ ] **Step 2: Rodar testes para confirmar que falham**

```bash
uv run pytest tests/test_variables.py -v
```

Expected: `ImportError` ou `ModuleNotFoundError` — `siphon.variables` não existe ainda.

- [ ] **Step 3: Implementar `variables.py`**

```python
# src/siphon/variables.py
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dateutil.relativedelta import relativedelta

_TIMEZONE = ZoneInfo(os.getenv("SIPHON_TIMEZONE", "America/Sao_Paulo"))
_MIN_DATE = os.getenv("SIPHON_MIN_DATE", "1997-01-01")


def _now() -> datetime:
    """Returns current datetime in the configured timezone. Separated for testability."""
    return datetime.now(tz=_TIMEZONE)


def resolve(query: str) -> str:
    """Resolve dynamic date variables in a SQL query before execution.

    Variables:
        @TODAY      → current date in SIPHON_TIMEZONE, e.g. '2026-03-25'
        @MIN_DATE   → configured minimum extraction date (SIPHON_MIN_DATE), e.g. '1997-01-01'
        @LAST_MONTH → first day of previous month, e.g. '2026-02-01'
        @NEXT_MONTH → first day of next month, e.g. '2026-04-01'
    """
    now = _now()
    last_month = now - relativedelta(months=1)
    next_month = now + relativedelta(months=1)

    return (
        query
        .replace("@TODAY", now.strftime("'%Y-%m-%d'"))
        .replace("@MIN_DATE", f"'{_MIN_DATE}'")
        .replace("@LAST_MONTH", last_month.strftime("'%Y-%m-01'"))
        .replace("@NEXT_MONTH", next_month.strftime("'%Y-%m-01'"))
    )
```

- [ ] **Step 4: Rodar testes e confirmar que passam**

```bash
uv run pytest tests/test_variables.py -v
```

Expected: todos os testes PASS.

- [ ] **Step 5: Commit**

```bash
git add src/siphon/variables.py tests/test_variables.py
git commit -m "feat: add date variable resolver with timezone support"
```

---

## Task 3: Plugin ABCs — Source, Destination, Parser

**Files:**
- Create: `src/siphon/plugins/sources/base.py`
- Create: `src/siphon/plugins/destinations/base.py`
- Create: `src/siphon/plugins/parsers/base.py`
- Create: `tests/test_abcs.py`

**Spec:** `claude.md` §7 (Plugin Contracts)

- [ ] **Step 1: Escrever testes dos contratos das ABCs primeiro**

```python
# tests/test_abcs.py
import pyarrow as pa
import pytest

from siphon.plugins.sources.base import Source
from siphon.plugins.destinations.base import Destination
from siphon.plugins.parsers.base import Parser


class TestSourceABC:
    def test_cannot_instantiate_source_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            Source()  # type: ignore

    def test_concrete_source_must_implement_extract(self):
        class BadSource(Source):
            pass  # missing extract()
        with pytest.raises(TypeError, match="abstract"):
            BadSource()

    def test_concrete_source_can_be_instantiated(self):
        class GoodSource(Source):
            def extract(self) -> pa.Table:
                return pa.table({"x": [1]})
        src = GoodSource()
        assert isinstance(src, Source)

    def test_extract_batches_default_wraps_extract(self):
        class GoodSource(Source):
            def extract(self) -> pa.Table:
                return pa.table({"x": [1, 2, 3]})
        src = GoodSource()
        batches = list(src.extract_batches())
        assert len(batches) == 1
        assert batches[0].num_rows == 3

    def test_extract_batches_can_be_overridden(self):
        class StreamingSource(Source):
            def extract(self) -> pa.Table:
                return pa.table({"x": [1, 2, 3]})
            def extract_batches(self, chunk_size: int = 100):
                yield pa.table({"x": [1]})
                yield pa.table({"x": [2, 3]})
        src = StreamingSource()
        batches = list(src.extract_batches())
        assert len(batches) == 2


class TestDestinationABC:
    def test_cannot_instantiate_destination_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            Destination()  # type: ignore

    def test_concrete_destination_must_implement_write(self):
        class BadDest(Destination):
            pass
        with pytest.raises(TypeError, match="abstract"):
            BadDest()

    def test_write_receives_is_first_chunk_flag(self):
        received = {}
        class GoodDest(Destination):
            def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
                received["is_first_chunk"] = is_first_chunk
                return len(table)
        dst = GoodDest()
        dst.write(pa.table({"x": [1]}), is_first_chunk=False)
        assert received["is_first_chunk"] is False


class TestParserABC:
    def test_cannot_instantiate_parser_directly(self):
        with pytest.raises(TypeError, match="abstract"):
            Parser()  # type: ignore

    def test_concrete_parser_must_implement_parse(self):
        class BadParser(Parser):
            pass
        with pytest.raises(TypeError, match="abstract"):
            BadParser()

    def test_parse_receives_bytes(self):
        class GoodParser(Parser):
            def parse(self, data: bytes) -> pa.Table:
                return pa.table({"raw": [data]})
        p = GoodParser()
        result = p.parse(b"hello")
        assert result.column("raw")[0].as_py() == b"hello"
```

- [ ] **Step 2: Rodar testes para confirmar que falham**

```bash
uv run pytest tests/test_abcs.py -v
```

Expected: `ImportError` — módulos não existem ainda.

- [ ] **Step 3: Implementar `plugins/sources/base.py`**

```python
# src/siphon/plugins/sources/base.py
from abc import ABC, abstractmethod
from collections.abc import Iterator

import pyarrow as pa


class Source(ABC):
    """Base class for all data source plugins.

    Plugins receive their configuration via __init__ (unpacked from the Pydantic model).
    No I/O must happen in __init__ — connection setup happens inside extract() or extract_batches().
    """

    @abstractmethod
    def extract(self) -> pa.Table:
        """Read from source and return a single Arrow Table.

        Use for sources where the full dataset fits comfortably in memory.
        For large sources, prefer overriding extract_batches() instead.
        """

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        """Stream data as batches of Arrow Tables.

        Default implementation wraps extract() into a single batch.
        Override this for memory-efficient extraction of large datasets.
        The worker calls write() incrementally for each yielded batch.
        """
        yield self.extract()
```

- [ ] **Step 2: Implementar `plugins/destinations/base.py`**

```python
# src/siphon/plugins/destinations/base.py
from abc import ABC, abstractmethod

import pyarrow as pa


class Destination(ABC):
    """Base class for all data destination plugins.

    Plugins receive their configuration via __init__ (unpacked from the Pydantic model).
    No I/O must happen in __init__ — connection setup happens inside write().
    """

    @abstractmethod
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        """Write Arrow Table to destination. Returns number of rows written.

        Args:
            table: Arrow Table to write.
            is_first_chunk: If True, overwrite existing data at the destination path
                            (delete_matching semantics). If False, append to existing
                            data from this job (overwrite_or_ignore semantics).
        """
```

- [ ] **Step 3: Implementar `plugins/parsers/base.py`**

```python
# src/siphon/plugins/parsers/base.py
from abc import ABC, abstractmethod

import pyarrow as pa


class Parser(ABC):
    """Base class for binary file parsers.

    Parsers convert raw bytes (e.g. binary files from SFTP) into Arrow Tables.
    Each parser is registered by name and selected via SFTPSourceConfig.parser.
    """

    @abstractmethod
    def parse(self, data: bytes) -> pa.Table:
        """Convert raw bytes into an Arrow Table.

        Args:
            data: Raw file contents downloaded from SFTP or another binary source.

        Returns:
            Arrow Table with the parsed data. Schema is parser-specific.
        """
```

- [ ] **Step 4: Rodar testes e confirmar que passam**

```bash
uv run pytest tests/test_abcs.py -v
```

Expected: todos os testes PASS.

- [ ] **Step 5: Commit** (apenas os arquivos base.py — __init__.py são escopo da Task 4)

```bash
git add src/siphon/plugins/sources/base.py \
        src/siphon/plugins/destinations/base.py \
        src/siphon/plugins/parsers/base.py \
        tests/test_abcs.py
git commit -m "feat: add Source, Destination, Parser ABCs"
```

---

## Task 4: Registry com autodiscovery

**Files:**
- Modify: `src/siphon/plugins/sources/__init__.py`
- Modify: `src/siphon/plugins/destinations/__init__.py`
- Modify: `src/siphon/plugins/parsers/__init__.py`
- Create: `tests/test_registry.py`

**Spec:** `claude.md` §4 — Registry pattern + Auto-descoberta de plugins

- [ ] **Step 1: Escrever testes para o registry**

```python
# tests/test_registry.py
import pyarrow as pa
import pytest

from siphon.plugins.sources.base import Source
from siphon.plugins.destinations.base import Destination
from siphon.plugins.parsers.base import Parser


# ── helpers: plugins de teste criados inline ─────────────────────────────────

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
        from siphon.plugins.sources import register, get
        register("_test_src")(_DummySource)
        cls = get("_test_src")
        assert cls is _DummySource

    def test_get_unknown_raises(self):
        from siphon.plugins.sources import get
        with pytest.raises(ValueError, match="not registered"):
            get("__nonexistent__")

    def test_registered_plugin_is_callable(self):
        from siphon.plugins.sources import register, get
        register("_test_src2")(_DummySource)
        cls = get("_test_src2")
        instance = cls()
        assert isinstance(instance, Source)


# ── destination registry ──────────────────────────────────────────────────────

class TestDestinationRegistry:
    def test_register_and_get(self):
        from siphon.plugins.destinations import register, get
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
        from siphon.plugins.parsers import register, get
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
```

- [ ] **Step 2: Rodar testes para confirmar que falham**

```bash
uv run pytest tests/test_registry.py -v
```

Expected: `ImportError` — `register`, `get` não existem ainda.

- [ ] **Step 3: Implementar o registry em `plugins/sources/__init__.py`**

```python
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
```

- [ ] **Step 4: Implementar o registry em `plugins/destinations/__init__.py`**

```python
# src/siphon/plugins/destinations/__init__.py
import importlib
import pkgutil
from pathlib import Path

from siphon.plugins.destinations.base import Destination

_REGISTRY: dict[str, type[Destination]] = {}


def register(name: str):
    """Decorator to register a Destination plugin under a given name."""
    def decorator(cls: type[Destination]) -> type[Destination]:
        _REGISTRY[name] = cls
        return cls
    return decorator


def get(name: str) -> type[Destination]:
    """Retrieve a registered Destination class by name."""
    if name not in _REGISTRY:
        available = list(_REGISTRY)
        raise ValueError(f"Destination '{name}' not registered. Available: {available}")
    return _REGISTRY[name]


def _autodiscover() -> None:
    package_dir = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        if module_name != "base":
            importlib.import_module(f"{__name__}.{module_name}")


_autodiscover()
```

- [ ] **Step 5: Implementar o registry em `plugins/parsers/__init__.py`**

```python
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
```

- [ ] **Step 6: Rodar testes e confirmar que passam**

```bash
uv run pytest tests/test_registry.py -v
```

Expected: todos os testes PASS.

- [ ] **Step 7: Commit**

```bash
git add src/siphon/plugins/ tests/test_registry.py
git commit -m "feat: add plugin registry with autodiscovery for sources, destinations, parsers"
```

---

## Task 5: `models.py` — Pydantic models + Job dataclass + mask_uri

**Files:**
- Create: `src/siphon/models.py`
- Create: `tests/test_models.py`

**Spec:** `claude.md` §6 (Data Models), §9 (mask_uri)

- [ ] **Step 1: Escrever testes primeiro**

```python
# tests/test_models.py
import pytest
from pydantic import ValidationError

from datetime import timezone

from siphon.models import (
    ExtractRequest,
    Job,
    JobStatus,
    LogsResponse,
    S3ParquetDestinationConfig,
    SQLSourceConfig,
    SFTPSourceConfig,
    mask_uri,
)


class TestMaskUri:
    def test_masks_password(self):
        uri = "mysql://user:SenhaSecreta@host:3306/db"
        assert mask_uri(uri) == "mysql://***:***@host:3306/db"

    def test_masks_oracle_uri(self):
        uri = "oracle+oracledb://admin:pass123@host:1521/service"
        assert mask_uri(uri) == "oracle+oracledb://***:***@host:1521/service"

    def test_no_credentials_unchanged(self):
        uri = "mysql://host:3306/db"
        assert mask_uri(uri) == uri

    def test_empty_string(self):
        assert mask_uri("") == ""


class TestSQLSourceConfig:
    def test_valid_minimal(self):
        cfg = SQLSourceConfig(
            type="sql",
            connection="mysql://user:pass@host/db",
            query="SELECT 1",
        )
        assert cfg.type == "sql"
        assert cfg.partition_on is None

    def test_repr_masks_password(self):
        cfg = SQLSourceConfig(
            type="sql",
            connection="mysql://user:SenhaSecreta@host/db",
            query="SELECT 1",
        )
        r = repr(cfg)
        assert "SenhaSecreta" not in r
        assert "***" in r

    def test_partition_fields_optional(self):
        cfg = SQLSourceConfig(
            type="sql",
            connection="postgresql://u:p@h/db",
            query="SELECT id FROM t",
            partition_on="id",
            partition_num=4,
        )
        assert cfg.partition_on == "id"
        assert cfg.partition_num == 4
        assert cfg.partition_range is None


class TestSFTPSourceConfig:
    def test_valid_with_defaults(self):
        cfg = SFTPSourceConfig(
            type="sftp",
            host="sftp.internal",
            port=22,
            username="sftpuser",
            password="pass",
            paths=["/remote/path"],
            parser="example_parser",
        )
        assert cfg.skip_patterns == ["TMP_*"]
        assert cfg.max_files == 1000
        assert cfg.chunk_size == 100
        assert cfg.fail_fast is False
        assert cfg.processing_folder is None
        assert cfg.processed_folder is None

    def test_repr_masks_password(self):
        cfg = SFTPSourceConfig(
            type="sftp",
            host="sftp.internal",
            port=22,
            username="user",
            password="SenhaSecreta",
            paths=["/path"],
            parser="example_parser",
        )
        r = repr(cfg)
        assert "SenhaSecreta" not in r


class TestS3ParquetDestinationConfig:
    def test_valid(self):
        cfg = S3ParquetDestinationConfig(
            type="s3_parquet",
            path="s3a://bronze/entity/2026-03-25",
            endpoint="minio.internal:9000",
            access_key="key",
            secret_key="secret",
        )
        assert cfg.compression == "snappy"

    def test_repr_masks_credentials(self):
        cfg = S3ParquetDestinationConfig(
            type="s3_parquet",
            path="s3a://bronze/x/2026-03-25",
            endpoint="minio:9000",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG",
        )
        r = repr(cfg)
        assert "AKIAIOSFODNN7EXAMPLE" not in r
        assert "wJalrXUtnFEMI" not in r


class TestExtractRequest:
    def test_discriminated_union_routes_sql(self):
        req = ExtractRequest.model_validate({
            "source": {
                "type": "sql",
                "connection": "mysql://u:p@h/db",
                "query": "SELECT 1",
            },
            "destination": {
                "type": "s3_parquet",
                "path": "s3a://bronze/x/2026-03-25",
                "endpoint": "minio:9000",
                "access_key": "k",
                "secret_key": "s",
            },
        })
        assert isinstance(req.source, SQLSourceConfig)

    def test_discriminated_union_routes_sftp(self):
        req = ExtractRequest.model_validate({
            "source": {
                "type": "sftp",
                "host": "sftp.internal",
                "port": 22,
                "username": "u",
                "password": "p",
                "paths": ["/path"],
                "parser": "example_parser",
            },
            "destination": {
                "type": "s3_parquet",
                "path": "s3a://bronze/x/2026-03-25",
                "endpoint": "minio:9000",
                "access_key": "k",
                "secret_key": "s",
            },
        })
        assert isinstance(req.source, SFTPSourceConfig)

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValidationError):
            ExtractRequest.model_validate({
                "source": {"type": "unknown", "connection": "x", "query": "y"},
                "destination": {
                    "type": "s3_parquet", "path": "s3a://x",
                    "endpoint": "e", "access_key": "k", "secret_key": "s",
                },
            })


class TestJobStatusModel:
    def test_valid_success(self):
        s = JobStatus(
            job_id="abc-123",
            status="success",
            rows_read=100,
            rows_written=100,
            duration_ms=500,
            log_count=5,
            failed_files=[],
            error=None,
        )
        assert s.status == "success"

    def test_partial_success_status(self):
        s = JobStatus(
            job_id="abc-456",
            status="partial_success",
            rows_read=80,
            rows_written=80,
            duration_ms=300,
            log_count=3,
            failed_files=["bad_file.bin"],
            error=None,
        )
        assert s.failed_files == ["bad_file.bin"]


class TestLogsResponse:
    def test_valid(self):
        r = LogsResponse(job_id="abc", logs=["line1", "line2"], next_offset=2)
        assert r.next_offset == 2


class TestJob:
    def test_created_at_is_timezone_aware(self):
        job = Job(job_id="test-123")
        assert job.created_at.tzinfo is not None

    def test_never_stores_credentials(self):
        job = Job(job_id="test-456")
        job_fields = {f.name for f in job.__dataclass_fields__.values()}
        sensitive = {"connection", "access_key", "secret_key", "password"}
        assert not job_fields.intersection(sensitive), \
            f"Job stores sensitive fields: {job_fields.intersection(sensitive)}"

    def test_to_status_returns_job_status(self):
        from datetime import datetime
        job = Job(job_id="test-789", status="success", rows_read=10, rows_written=10)
        job.started_at = datetime.now(tz=timezone.utc)
        job.finished_at = datetime.now(tz=timezone.utc)
        status = job.to_status()
        assert isinstance(status, JobStatus)
        assert status.job_id == "test-789"
        assert status.status == "success"
```

- [ ] **Step 2: Rodar testes para confirmar que falham**

```bash
uv run pytest tests/test_models.py -v
```

Expected: `ImportError` — `siphon.models` não existe ainda.

- [ ] **Step 3: Implementar `models.py`**

```python
# src/siphon/models.py
import re
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ── Credential masking ────────────────────────────────────────────────────────

def mask_uri(uri: str) -> str:
    """Replace user:password in a connection URI with ***.

    Example:
        mysql://user:SenhaSecreta@host:3306/db
        → mysql://***:***@host:3306/db
    """
    if not uri:
        return uri
    return re.sub(r"(://)[^:]+:[^@]+(@)", r"\1***:***\2", uri)


# ── Source configs ────────────────────────────────────────────────────────────

class SQLSourceConfig(BaseModel):
    type: Literal["sql"]
    connection: str
    query: str
    partition_on: str | None = None
    partition_num: int | None = None
    partition_range: tuple[int, int] | None = None

    def __repr__(self) -> str:
        return (
            f"SQLSourceConfig(connection={mask_uri(self.connection)!r}, "
            f"query={self.query[:50]!r}{'...' if len(self.query) > 50 else ''})"
        )


class SFTPSourceConfig(BaseModel):
    type: Literal["sftp"]
    host: str
    port: int = 22
    username: str
    password: str
    paths: list[str]
    parser: str
    skip_patterns: list[str] = Field(default_factory=lambda: ["TMP_*"])
    max_files: int = 1000
    chunk_size: int = 100
    fail_fast: bool = False
    processing_folder: str | None = None
    processed_folder: str | None = None

    def __repr__(self) -> str:
        return (
            f"SFTPSourceConfig(host={self.host!r}, username={self.username!r}, "
            f"paths={self.paths!r}, parser={self.parser!r})"
        )


# ── Destination configs ───────────────────────────────────────────────────────

class S3ParquetDestinationConfig(BaseModel):
    type: Literal["s3_parquet"]
    path: str
    endpoint: str
    access_key: str
    secret_key: str
    compression: str = "snappy"

    def __repr__(self) -> str:
        return (
            f"S3ParquetDestinationConfig(path={self.path!r}, "
            f"endpoint={self.endpoint!r}, access_key='***', secret_key='***')"
        )


# ── Request / response models ─────────────────────────────────────────────────

SourceConfig = Annotated[
    SQLSourceConfig | SFTPSourceConfig,
    Field(discriminator="type"),
]

# DestinationConfig: single member for now — add more types as plugins are added.
# Pydantic v2 discriminator requires 2+ members in a union, so we type directly.


class ExtractRequest(BaseModel):
    source: SourceConfig
    destination: S3ParquetDestinationConfig


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "failed", "partial_success"]
    rows_read: int | None = None
    rows_written: int | None = None
    duration_ms: int | None = None
    log_count: int = 0
    failed_files: list[str] = Field(default_factory=list)
    error: str | None = None


class LogsResponse(BaseModel):
    job_id: str
    logs: list[str]
    next_offset: int


# ── Internal Job dataclass (no credentials stored) ────────────────────────────

@dataclass
class Job:
    """Internal job state. Never stores connection strings, passwords, or keys."""
    job_id: str
    status: str = "queued"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)  # timezone-aware, não utcnow (deprecated)
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows_read: int | None = None
    rows_written: int | None = None
    failed_files: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    error: str | None = None

    def to_status(self) -> JobStatus:
        """Convert internal Job to API-facing JobStatus."""
        duration_ms = None
        if self.started_at and self.finished_at:
            duration_ms = int(
                (self.finished_at - self.started_at).total_seconds() * 1000
            )
        return JobStatus(
            job_id=self.job_id,
            status=self.status,
            rows_read=self.rows_read,
            rows_written=self.rows_written,
            duration_ms=duration_ms,
            log_count=len(self.logs),
            failed_files=self.failed_files,
            error=self.error,
        )
```

- [ ] **Step 4: Rodar testes e confirmar que passam**

```bash
uv run pytest tests/test_models.py -v
```

Expected: todos os testes PASS.

- [ ] **Step 5: Commit**

```bash
git add src/siphon/models.py tests/test_models.py
git commit -m "feat: add Pydantic models, Job dataclass, and mask_uri"
```

---

## Task 6: Verificação final — ruff + todos os testes

- [ ] **Step 1: Rodar ruff**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: sem violações. Se houver, corrigir com `uv run ruff format src/ tests/` e verificar lint manualmente.

- [ ] **Step 2: Rodar toda a suite de testes**

```bash
uv run pytest tests/ -v --tb=short
```

Expected: todos os testes PASS, nenhum FAIL ou ERROR.

- [ ] **Step 3: Verificar estrutura final do projeto**

```bash
find src/ tests/ -name "*.py" | sort
```

Expected (mínimo):
```
src/siphon/__init__.py
src/siphon/models.py
src/siphon/variables.py
src/siphon/plugins/__init__.py
src/siphon/plugins/sources/__init__.py
src/siphon/plugins/sources/base.py
src/siphon/plugins/destinations/__init__.py
src/siphon/plugins/destinations/base.py
src/siphon/plugins/parsers/__init__.py
src/siphon/plugins/parsers/base.py
tests/__init__.py
tests/test_models.py
tests/test_registry.py
tests/test_variables.py
```

- [ ] **Step 4: Commit final de fase**

```bash
git add -A
git commit -m "chore: phase 1 complete — skeleton, models, ABCs, registry, variables"
```

---

## Critérios de conclusão da Fase 1

- `uv run pytest` passa sem erros
- `uv run ruff check src/ tests/` passa sem violações
- Toda a estrutura de pastas está criada conforme §14 do `claude.md`
- `mask_uri` mascara credenciais corretamente
- Registry aceita `@register`, `get()` e autodiscovery sem modificar core
- `resolve()` lida corretamente com timezone, wrap de ano, e múltiplas variáveis na mesma query
- `Job` dataclass nunca contém campos de credenciais
