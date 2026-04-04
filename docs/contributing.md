# Contributing

This guide covers how to run Siphon locally, understand the codebase, run tests, and add new functionality.

---

## Running locally

### Prerequisites

- Docker + Docker Compose
- Python 3.12+ (for running tests outside Docker)
- Node 22+ + pnpm (for frontend dev)
- uv (Python package manager): `pip install uv`

### Start the full stack

```bash
# Start Siphon + PostgreSQL
docker compose up --build

# Or use the helper script
./siphon.sh start
```

The API is at `http://localhost:8000`. Log in with `admin@example.com` / `changeme123`.

### Start the test environment (MySQL + MinIO)

The `testenv/` directory provides a local MySQL database and MinIO instance for end-to-end testing:

```bash
./testenv/mysql.sh start     # starts MySQL 8.0 on :3306 and MinIO on :9010/:9011
./testenv/mysql.sh stop      # stop containers
./testenv/mysql.sh destroy   # stop + delete volumes
./testenv/mysql.sh shell     # open MySQL shell
./testenv/mysql.sh status    # check health
```

**Connection strings** (Siphon runs inside Docker, so use `host.docker.internal`):

```
MySQL:  mysql://siphon:siphon@host.docker.internal:3306/testdb
MinIO:  endpoint=host.docker.internal:9010, key=minioadmin, secret=minioadmin
```

The schema and seed data are in `testenv/init/`:
- `01_schema.sql` — tables: orders, customers, products, order_items
- `02_seed.sql` — 10 customers, 10 products, 15 orders, 25 order_items

---

## Running tests

```bash
# Install dev dependencies
uv sync

# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/test_worker.py

# Run with coverage
uv run pytest --cov=siphon --cov-report=html
```

### Test structure

```
tests/
├── conftest.py          # Shared fixtures (app, db, auth tokens)
├── test_auth.py         # Login, refresh, logout, token rotation
├── test_connections.py  # Connection CRUD, encryption, test endpoint
├── test_pipelines.py    # Pipeline CRUD, schedule upsert
├── test_runs.py         # Run history, logs, cancel
├── test_worker.py       # Extraction, DQ checks, schema hash
├── test_worker_phase9.py
├── test_preview.py      # Preview endpoint
├── test_watermark.py    # inject_watermark for all dialects
└── test_scheduler.py    # Schedule sync, APScheduler integration
```

### Test isolation

Tests use `importlib.reload()` to reset module-level state between tests. If you write a test that fails when run with the full suite but passes in isolation, check whether your test is polluting module globals (especially the `queue` singleton in `main.py`).

See `tests/conftest.py` for the fixture patterns that handle this correctly.

---

## Frontend dev server

```bash
cd frontend
pnpm install
pnpm dev
```

Starts the Vite dev server at `http://localhost:5173`. API calls are proxied to `http://localhost:8000`. The backend must be running.

**Linting and type checking:**

```bash
pnpm lint       # ESLint
pnpm typecheck  # tsc --noEmit
```

**Building:**

```bash
pnpm build      # outputs to frontend/dist/
```

---

## Code style

**Python:**
- Ruff is used for linting and formatting: `uv run ruff check` and `uv run ruff format`
- Type hints on all public functions
- `async` for I/O, sync for CPU-bound (worker runs in executor)

**TypeScript:**
- Strict mode enabled
- Prefer named exports
- All API responses have TypeScript interfaces in `lib/api.ts`

---

## Adding a new source plugin

Sources live in `src/siphon/plugins/sources/`. To add a new source type (e.g., a REST API):

### 1. Create the plugin file

```python
# src/siphon/plugins/sources/rest_api.py
import pyarrow as pa
import requests
from siphon.plugins.sources import register
from siphon.plugins.sources.base import Source


@register("rest_api")
class RestAPISource(Source):
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def extract(self) -> pa.Table:
        resp = requests.get(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        records = resp.json()
        return pa.Table.from_pylist(records)
```

The `@register("rest_api")` decorator adds the class to the registry. The `extract()` method must return a PyArrow Table. Autodiscovery imports all modules in the package at startup — no other wiring required.

For large sources, override `extract_batches()` to stream data in chunks:

```python
def extract_batches(self, chunk_size: int = 1000):
    page = 0
    while True:
        resp = requests.get(f"{self.base_url}?page={page}&size={chunk_size}", ...)
        records = resp.json()
        if not records:
            break
        yield pa.Table.from_pylist(records)
        page += 1
```

### 2. Register the connection type

In `src/siphon/connections/router.py`, add to `_CONNECTION_TYPES`:

```python
{
    "type": "rest_api",
    "fields": [
        {"name": "base_url", "type": "string",  "label": "Base URL",  "required": True,
         "placeholder": "https://api.example.com/data"},
        {"name": "api_key",  "type": "string",  "label": "API Key",   "required": True, "secret": True},
    ],
}
```

Field schema:

| Key | Values | Description |
|---|---|---|
| `name` | string | Key in `config` dict; must match `__init__` parameter name exactly |
| `type` | `string`, `integer` | Input type |
| `label` | string | Display label in the UI |
| `secret` | bool | Renders as password input; excluded from logs |
| `required` | bool | Shown with `*` in UI |
| `placeholder` | string | Input placeholder text |
| `default` | any | Default value pre-filled in UI |

### 3. Add the connection test

In the `_test_connection` function in `connections/router.py`:

```python
elif conn_type == "rest_api":
    import requests as _requests
    _validate_host(f"dummy://{urlparse(config['base_url']).hostname}")
    resp = _requests.get(
        config["base_url"],
        headers={"Authorization": f"Bearer {config['api_key']}"},
        timeout=10,
    )
    resp.raise_for_status()
```

Call `_validate_host()` to respect `SIPHON_ALLOWED_HOSTS`.

### 4. Wire the config in the worker

The worker in `src/siphon/worker.py` passes the decrypted config directly to the Source constructor. Verify that all keys in your `config` dict match the parameter names in your `__init__`.

---

## Adding a destination plugin

Destinations live in `src/siphon/plugins/destinations/`. The `write()` method receives an Arrow Table and returns the number of rows written:

```python
# src/siphon/plugins/destinations/http_sink.py
import pyarrow as pa
from siphon.plugins.destinations import register
from siphon.plugins.destinations.base import Destination


@register("http_sink")
class HttpSinkDestination(Destination):
    def __init__(self, url: str, api_key: str) -> None:
        self.url = url
        self.api_key = api_key

    def write(self, table: pa.Table, is_first_chunk: bool) -> int:
        records = table.to_pylist()
        requests.post(self.url, json=records,
                      headers={"Authorization": f"Bearer {self.api_key}"})
        return len(records)
```

`is_first_chunk` is `True` on the first call per job. Use it to truncate or reset the destination before appending.

---

## Adding a parser plugin

Parsers live in `src/siphon/plugins/parsers/`. They are used by `SFTPSource` to convert raw binary file content into an Arrow Table:

```python
# src/siphon/plugins/parsers/csv_parser.py
import pyarrow as pa
import pyarrow.csv as pacsv
from siphon.plugins.parsers import register
from siphon.plugins.parsers.base import Parser


@register("csv")
class CsvParser(Parser):
    def parse(self, data: bytes) -> pa.Table:
        return pacsv.read_csv(pa.BufferReader(data))
```

To use it in a pipeline, set `parser: "csv"` in the SFTP source config.

---

## Database migrations

When you add or change a database table, create a new Alembic migration:

```bash
# Generate a migration automatically (detect schema changes from ORM)
uv run alembic revision --autogenerate -m "add my_table"

# Apply migrations
uv run alembic upgrade head

# Check current version
uv run alembic current
```

Migration files go in `alembic/versions/`. Always review autogenerated migrations before committing — Alembic sometimes misses things like indexes or column defaults.

---

## Debugging tips

### Backend

Add `logger.debug()` calls — they're cheap and removed in production by log level:

```python
import logging
logger = logging.getLogger(__name__)

logger.debug("Processing job %s with query: %s", job.job_id, query[:100])
```

To see debug logs locally:

```bash
# Add to docker-compose.yml environment:
LOG_LEVEL: DEBUG
```

### Frontend

React Query DevTools are not included in the production build but can be added during development:

```tsx
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'

// Inside QueryClientProvider:
<ReactQueryDevtools initialIsOpen={false} />
```

### Inspecting the database

```bash
docker compose exec postgres psql -U siphon -d siphon

-- Useful queries:
SELECT id, name, status, created_at FROM job_runs ORDER BY created_at DESC LIMIT 10;
SELECT name, last_watermark, last_schema_hash FROM pipelines;
SELECT name, conn_type, key_version FROM connections;
```

---

## Architecture decisions to be aware of

**In-memory job queue**: jobs are lost on ungraceful pod restart. This is a known trade-off. For Bronze-layer pipelines, the next scheduled run re-extracts the same data, so data loss is not a concern. If you need guaranteed delivery, wrap the trigger in an Airflow task with retry logic.

**Single worker process**: uvicorn runs with `--workers 1` because the job queue is in-memory and cannot be shared across processes. To scale horizontally, run multiple pods (each with their own queue) rather than multiple workers in a single pod.

**No hard-kill for running jobs**: calling `POST /api/v1/runs/{id}/cancel` on a running job logs a cancellation request but doesn't interrupt the extraction. The job will finish its current batch. True mid-extraction cancellation would require cooperative polling in the source plugin — a future enhancement.

**Oracle uses oracledb, not ConnectorX**: ConnectorX's Oracle support is incomplete. The `oracledb` driver uses thin mode (no Oracle client required) and streams results in chunks via cursor.

**connect_timeout only for PostgreSQL**: ConnectorX's MySQL driver doesn't support `connect_timeout` as a URL parameter. Siphon only injects the timeout for PostgreSQL connections.
