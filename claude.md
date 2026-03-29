# Siphon — Project Specification

> **Nome do projeto:** `siphon`

> Document to be used as project context for implementation.
> Covers motivation, requirements, architecture decisions, and technical contracts.

---

## 1. Context and Motivation

### The problem

A project runs a data pipeline built on a **medallion architecture** (Bronze → Silver → Gold) orchestrated by Apache Airflow. The Bronze layer — responsible for extracting raw data from source systems and saving it as Parquet files in MinIO (S3-compatible) — currently uses **Apache Spark** as its execution engine.

Spark is a distributed processing framework. It excels at in-memory transformations, joins, and aggregations over large datasets. The Bronze layer does none of that. A typical Bronze job looks like this:

```python
df = source_db("SELECT id, name, code FROM example_table")
write_bronze(df, entity="example_table")
```

Three lines. A SQL read and a Parquet write. For this, every job:

- Waits **30–60 seconds** for the Spark driver and executor pods to spin up in Kubernetes before executing a single line of the query
- Allocates **2GB+ of memory** regardless of table size
- Requires a **~2GB Docker image** containing the JVM, all JDBC drivers as JARs, Delta Lake libraries, and Hadoop binaries
- Generates Kubernetes `SparkApplication` manifests via XCom, requiring a `task_conf_spark()` task before every extraction

There are **200+ Bronze DAGs** following this same pattern. The overhead is systemic and constant.

Additionally, the pipeline extracts data from SFTP servers (binary files from network equipment). This is handled separately with Pandas in a `KubernetesPodOperator`, but the SFTP connection itself is poorly implemented: each file creates its own SSH connection (up to 32 in parallel), there are hardcoded workarounds for temporary files (`TMP_`), and errors are silently swallowed.

### The goal

Build a **standalone, lightweight extraction service** that:

1. Replaces Spark for all Bronze layer extractions (SQL databases)
2. Provides a proper SFTP source implementation for binary file ingestion
3. Is designed as an open source tool, publishable on GitHub
4. Is extensible via a plugin architecture for new sources and destinations

Spark remains the right tool for Silver and Gold layers (complex transformations, Delta Lake, UDFs). This project only targets extraction.

---

## 2. What Already Exists (and Must Be Preserved)

### Source systems

| Source | Type | Notes |
|---|---|---|
| MySQL | MySQL | Multiple databases, ~100+ tables total |
| Oracle / SQL Server | Oracle / SQL Server | |
| Oracle | Oracle | Multiple databases |
| PostgreSQL | PostgreSQL | Multiple databases |
| SFTP | SSH/SFTP | Binary files |

### Destination

All Bronze data is written as **Parquet + Snappy** to **MinIO** (S3-compatible), following the path convention:

```
s3a://bronze/{entity}/{YYYY-MM-DD}/
```

This path is consumed directly by Silver Spark jobs via `read_bronze()`. The path structure must be preserved exactly.

### Orchestration

**Apache Airflow** remains the orchestrator. It will not be replaced. Individual DAGs per entity continue to exist — they call the Siphon service via HTTP instead of launching Spark jobs.

### Asset events (data lineage)

Airflow uses [Assets](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/assets.html) to trigger Silver DAGs when Bronze completes. The asset URI is based on the MinIO path:

```python
Asset(uri="s3a://bronze/cidades/")
```

The Bronze DAG emits this event via `outlets` on the operator. This mechanism must continue working — the Siphon service does not emit asset events itself. Airflow does, after receiving a successful response from the service.

### Query patterns

Queries are **not** simple table replications. They include:

- Specific column selection (not `SELECT *`)
- `CASE WHEN` expressions
- CTEs (`WITH ... AS (...)`)
- Window functions (`ROW_NUMBER() OVER (PARTITION BY ...)`)
- Multi-table `JOIN`s
- Dynamic date variables: `@TODAY`, `@MIN_DATE`, `@LAST_MONTH`, `@NEXT_MONTH`
- Expressions like `HEX(CAST(...))`, `CONCAT(...)`, `COALESCE(...)`

The service must execute arbitrary SQL as provided, without restriction.

### Variable substitution

Queries may contain named variables that must be resolved at runtime:

| Variable | Resolves to |
|---|---|
| `@TODAY` | Current date: `'2026-03-25'` |
| `@MIN_DATE` | Configured minimum extraction date: `'1997-01-01'` |
| `@LAST_MONTH` | First day of previous month: `'2026-02-01'` |
| `@NEXT_MONTH` | First day of next month: `'2026-04-01'` |

---

## 3. Requirements

### Functional

**FR-01** — Accept SQL extraction jobs via HTTP POST, containing: connection string, SQL query, and destination configuration.

**FR-02** — Accept SFTP extraction jobs via HTTP POST, containing: connection details, remote paths, parser name, file handling folders, and destination configuration.

**FR-03** — Execute SQL queries against MySQL, PostgreSQL, Oracle, and SQL Server databases using the connection string provided in the request.

**FR-04** — Resolve dynamic query variables (`@TODAY`, `@MIN_DATE`, `@LAST_MONTH`, `@NEXT_MONTH`) before executing SQL.

**FR-05** — Connect to SFTP servers, list files from multiple remote paths, filter files matching skip patterns (e.g. `TMP_*`), and download files using a single persistent SSH connection per job (not per file).

**FR-06** — Pass downloaded SFTP file bytes through a named parser that converts binary content into a columnar Arrow Table.

**FR-07** — Write extracted data as Parquet (Snappy compression) to S3-compatible storage (MinIO), at the path specified by the caller.

**FR-08** — Maintain an internal job queue with a configurable maximum number of concurrent workers. Requests that exceed queue capacity must be rejected with HTTP 429.

**FR-09** — Expose job status and structured logs per job via HTTP GET endpoints.

**FR-10** — Support synchronous extraction (caller blocks until complete) and asynchronous extraction (caller receives a `job_id` and polls for status).

**FR-11** — Implement a plugin architecture for sources and destinations. New plugins must be registerable without modifying core service code.

**FR-12** — Expose a health check endpoint.

### Non-functional

**NFR-01** — Must run as a single Docker container. No external dependencies required at runtime (no Redis, no database, no sidecar).

**NFR-02** — The Docker image must be under 500MB.

**NFR-03** — Job startup time (from receiving request to first byte read from source) must be under 2 seconds for SQL sources.

**NFR-04** — Must handle at least 20 concurrent extraction jobs without degradation (worker count is configurable).

**NFR-05** — Written in Python 3.12+. Managed with `uv`. Linted with `Ruff`.

**NFR-06** — Must be deployable on any Kubernetes cluster via a standard `KubernetesPodOperator` or as a long-running `Deployment`.

**NFR-07** — All source plugins must return data as Apache Arrow Tables. This ensures zero-copy interoperability with Polars, DuckDB, Pandas, and Spark without conversion.

**NFR-08** — Must be open source, publishable on GitHub, with a permissive license (MIT or Apache 2.0).

**NFR-09** — Expose a `/metrics` endpoint (Prometheus format) with at minimum: jobs_total (by status), jobs_active, queue_size, job_duration_seconds (histogram). Implementation can be deferred to v1.1 but the endpoint path must be reserved.

**NFR-10** — Support structured JSON logging via `SIPHON_LOG_FORMAT=json` env var. Default is `text` for local dev. JSON format is required for production log aggregation (ELK, Loki, etc.).

**NFR-11** — O serviço deve completar jobs em execução ao receber SIGTERM antes de encerrar (graceful drain). O tempo máximo de drain é configurável via `SIPHON_DRAIN_TIMEOUT` (padrão: igual a `SIPHON_JOB_TIMEOUT`). Após o drain timeout, jobs ainda ativos são marcados como `failed` e o processo encerra. O Kubernetes `terminationGracePeriodSeconds` deve ser maior que `SIPHON_DRAIN_TIMEOUT`.

**NFR-12** — Expor endpoints de health separados: `GET /health/live` (liveness — retorna 200 enquanto o processo está vivo) e `GET /health/ready` (readiness — retorna 503 quando a queue está cheia ou o serviço está em drain). `GET /health` é mantido para debug humano.

**NFR-13** — O container deve rodar como usuário não-root (UID 1000). O filesystem deve ser read-only exceto `/tmp`. Nenhuma Linux capability deve ser adicionada (`allowPrivilegeEscalation: false`).

**NFR-14** — A porta do serviço deve ser configurável via `SIPHON_PORT` (padrão: `8000`). Consistente no `Dockerfile EXPOSE`, Kubernetes `containerPort`, e Kubernetes `Service`.

**NFR-15** — O protocolo S3/MinIO deve ser configurável via `SIPHON_S3_SCHEME` (`http` ou `https`, padrão: `https`). Nunca hardcoded no código.

**NFR-16** — O SQLSource deve injetar `connect_timeout` na connection string automaticamente se não estiver presente, usando o valor de `SIPHON_CONNECT_TIMEOUT` (padrão: `30` segundos). Evita threads presas indefinidamente em bancos inacessíveis.

**NFR-17** — O serviço deve validar o host da `connection` string contra uma allowlist configurável (`SIPHON_ALLOWED_HOSTS`) antes de tentar qualquer conexão. Rejeitar com HTTP 400 se o host não estiver na lista. Mitigação de SSRF. Se `SIPHON_ALLOWED_HOSTS` não estiver configurado, o serviço sobe com warning mas não bloqueia conexões (modo permissivo para dev).

**NFR-18** — O serviço deve suportar autenticação via API key (`SIPHON_API_KEY`). Se configurado, todas as rotas exigem o header `Authorization: Bearer <key>`. Se não configurado, o serviço sobe com um warning em startup: `"WARNING: API authentication is disabled. Set SIPHON_API_KEY for production use."` Desativado por padrão para zero-config em desenvolvimento.

**NFR-19** — Conexões SFTP devem verificar o host key do servidor. O comportamento padrão é `RejectPolicy` (rejeita servidores desconhecidos). A chave pública do servidor deve ser configurável via `SIPHON_SFTP_KNOWN_HOSTS` (path para arquivo `known_hosts`) ou `SIPHON_SFTP_HOST_KEY` (chave pública em base64). `AutoAddPolicy` é explicitamente proibido em produção.

**NFR-20** — O request body deve ter tamanho máximo configurável via `SIPHON_MAX_REQUEST_SIZE_MB` (padrão: `1`). Requests maiores são rejeitados com HTTP 413 antes de atingir a validação Pydantic.

**NFR-21** — O path de destino S3 deve ser validado contra um prefixo permitido configurável via `SIPHON_ALLOWED_S3_PREFIX` (ex: `bronze/`). Paths com `..` ou fora do prefixo são rejeitados com HTTP 400.

**NFR-22** — Arquivos SFTP têm tamanho máximo configurável via `SIPHON_MAX_FILE_SIZE_MB` (padrão: `500`). Arquivos maiores são pulados com log de warning.

---

## 4. Architecture

### Overview

```
┌─────────────────────────────────────────────────────────┐
│  Caller (Airflow DAG, curl, any HTTP client)            │
└───────────────────┬─────────────────────────────────────┘
                    │ POST /extract  or  POST /jobs
                    ▼
┌─────────────────────────────────────────────────────────┐
│  Siphon Service  (FastAPI)                            │
│                                                         │
│  ┌─────────────┐    ┌──────────────────────────────┐   │
│  │  REST API   │───▶│  Job Queue (asyncio)         │   │
│  │  /extract   │    │  max_workers = N (env var)   │   │
│  │  /jobs      │    └──────────────┬───────────────┘   │
│  │  /health    │                   │                   │
│  └─────────────┘          ┌────────▼────────┐          │
│                           │    Worker        │          │
│                           │                 │          │
│                           │  Source Plugin  │          │
│                           │       ↓         │          │
│                           │  pa.Table       │          │
│                           │       ↓         │          │
│                           │  Dest Plugin    │          │
│                           └─────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

### Plugin architecture

Sources, destinations, and parsers are all plugins. The core service (`worker.py`) is agnostic to all of them — it only knows the base interfaces.

```
plugins/
├── sources/
│   ├── base.py          # ABC Source
│   ├── __init__.py      # registry: @register("sql"), get("sql")
│   ├── sql.py           # SQLSource   — ConnectorX
│   └── sftp.py          # SFTPSource  — Paramiko
├── destinations/
│   ├── base.py          # ABC Destination
│   ├── __init__.py      # registry: @register("s3_parquet"), get(...)
│   └── s3_parquet.py    # S3ParquetDestination — PyArrow
└── parsers/
    ├── base.py          # ABC Parser: bytes → pa.Table
    ├── __init__.py      # registry: @register("example_parser"), get(...)
    └── example_parser.py     # example binary parser
```

**Adding a new source** = create `plugins/sources/my_source.py`, implement `Source`, decorate with `@register("my_source")`. Nothing else changes.

**Adding a new destination** = same pattern under `plugins/destinations/`.

### Registry pattern

```python
# plugins/sources/__init__.py
_REGISTRY: dict[str, type[Source]] = {}

def register(name: str):
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator

def get(name: str) -> type[Source]:
    if name not in _REGISTRY:
        raise ValueError(f"Source '{name}' not registered. Available: {list(_REGISTRY)}")
    return _REGISTRY[name]
```

All three plugin types (sources, destinations, parsers) use the same pattern.

### Auto-descoberta de plugins

O decorator `@register` só executa se o módulo for importado. Para que novos plugins sejam registrados automaticamente sem tocar no core, o `__init__.py` de cada registry escaneia seu próprio diretório:

```python
# plugins/sources/__init__.py  (mesmo padrão para destinations e parsers)
import importlib
import pkgutil
from pathlib import Path

def _autodiscover():
    package_dir = Path(__file__).parent
    for finder, name, _ in pkgutil.iter_modules([str(package_dir)]):
        if name not in ("base",):
            importlib.import_module(f"{__name__}.{name}")

_autodiscover()
```

Isso garante que criar `plugins/sources/my_source.py` com `@register("my_source")` é suficiente — o registry o importa na inicialização do serviço, sem nenhuma outra mudança.

### Modelo de segurança

```
Request recebido
      │
      ├── 1. Tamanho do body ≤ SIPHON_MAX_REQUEST_SIZE_MB?    → 413 se não
      ├── 2. Authorization: Bearer <SIPHON_API_KEY> válido?    → 401 se não (quando configurado)
      ├── 3. Host da connection string está em ALLOWED_HOSTS?  → 400 se não (quando configurado)
      ├── 4. Path S3 começa com SIPHON_ALLOWED_S3_PREFIX?      → 400 se não
      └── 5. Path S3 contém ".."?                             → 400 se não
```

Todas as validações acontecem **antes** de qualquer I/O. Nenhuma conexão é estabelecida com source ou destination se as validações falharem.

**Startup warnings** (não bloqueiam o serviço, mas aparecem nos logs):
```
WARNING: SIPHON_API_KEY not set — API authentication is disabled
WARNING: SIPHON_ALLOWED_HOSTS not set — all hosts are permitted (SSRF risk)
```

---

## 5. API Contract

### `POST /extract` — synchronous (apenas para testes e uso local)

Bloqueia até o job completar. **Não usar para integração com Airflow em produção** — conexões HTTP abertas por minutos são frágeis (proxies, load balancers, timeouts). Para Airflow, usar sempre `POST /jobs` + polling.

Útil para: testes locais, scripts one-off, e chamadas via `curl` para debug.

**Request body:**

```json
{
  "source": {
    "type": "sql",
    "connection": "mysql://user:pass@host:3306/mydb",
    "query": "SELECT id, name, code FROM example_table"
  },
  "destination": {
    "type": "s3_parquet",
    "path": "s3a://bronze/cidades/2026-03-25",
    "endpoint": "minio.internal:9000",
    "access_key": "minioadmin",
    "secret_key": "minioadmin",
    "compression": "snappy"
  }
}
```

**Response 200:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "success",
  "rows": 1247,
  "duration_ms": 843,
  "logs": [
    "Resolved @TODAY → '2026-03-25'",
    "Connected to mysql://host:3306/mydb",
    "Read 1247 rows (2.1 MB) in 612ms",
    "Written to s3a://bronze/cidades/2026-03-25 in 231ms"
  ]
}
```

**Response 429 — queue full:**

```json
{
  "detail": "Queue is full. Max workers: 10, queued: 5. Retry later."
}
```

---

### `POST /jobs` — asynchronous

Returns immediately with a `job_id`. Caller polls for status.

**Response 202:**

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "queued"
}
```

---

### `GET /jobs/{job_id}`

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "success",
  "rows_read": 1247,
  "rows_written": 1247,
  "duration_ms": 843,
  "log_count": 12,
  "error": null
}
```

Note: `logs` is removed from this endpoint — use `GET /jobs/{id}/logs?since=N` instead. `log_count` stays for reference.

`status` values: `queued` | `running` | `success` | `failed`

### `GET /jobs/{job_id}/logs?since=N`

Retorna apenas as linhas de log a partir do índice `N` (exclusivo). Evita que o polling receba e reimprima linhas já vistas.

```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "logs": ["linha 5", "linha 6", "linha 7"],
  "next_offset": 7
}
```

O `SiphonOperator` usa este endpoint para streaming incremental de logs:

```python
log_offset = 0
while True:
    status = self._http.get(f"/jobs/{job_id}")
    new_logs = self._http.get(f"/jobs/{job_id}/logs?since={log_offset}")
    for line in new_logs["logs"]:
        self.log.info("[siphon] %s", line)
    log_offset = new_logs["next_offset"]

    if status["status"] in ("success", "failed"):
        break
    time.sleep(self.poll_interval)
```

---

### `GET /health/live` — liveness probe

Retorna 200 enquanto o processo estiver rodando. Nunca retorna 503 (exceto se o processo estiver morto).

```json
{ "status": "ok" }
```

### `GET /health/ready` — readiness probe

Retorna 503 quando a queue está cheia ou o serviço está em modo drain (não deve receber mais tráfego).

```json
{ "status": "ok", "accepting_jobs": true }
```

```json
{ "status": "degraded", "accepting_jobs": false, "reason": "queue_full" }
```

### `GET /health` — debug completo

```json
{
  "status": "ok",
  "accepting_jobs": true,
  "queue": {
    "workers_active": 3,
    "workers_max": 10,
    "jobs_queued": 1,
    "jobs_total": 142
  },
  "uptime_seconds": 3842
}
```

Kubernetes usa `/health/live` para liveness e `/health/ready` para readiness. `GET /health` é para operadores humanos e dashboards.

---

## 6. Data Models (Pydantic)

```python
# Discriminated union — the "type" field routes to the correct plugin

# Sources
SQLSourceConfig:
  type: Literal["sql"]
  connection: str        # full connection URL: "mysql://...", "postgresql://...", "oracle+oracledb://..."
  query: str             # arbitrary SQL, may contain @TODAY etc.
  partition_on: str | None      # optional: column name for parallel partitioned reads
  partition_num: int | None     # optional: number of partitions (default: 4 when partition_on is set)
  partition_range: tuple[int, int] | None  # optional: (min, max) override — ConnectorX auto-detects if omitted

SFTPSourceConfig:
  type: Literal["sftp"]
  host: str
  port: int              # default: 22
  username: str
  password: str
  paths: list[str]       # remote directories to list
  parser: str            # registered parser name, e.g. "my_parser"
  skip_patterns: list[str]      # default: ["TMP_*"]
  max_files: int                # max total files per job, default: 1000
  chunk_size: int               # files per streaming chunk in extract_batches(), default: 100
  fail_fast: bool               # default: False — se True, aborta no primeiro erro de parse
  processing_folder: str | None # move files here before processing
  processed_folder: str | None  # move files here after success

# Destinations
S3ParquetDestinationConfig:
  type: Literal["s3_parquet"]
  path: str              # full S3 path: "s3a://bronze/cidades/2026-03-25"
  endpoint: str          # MinIO endpoint: "minio.internal:9000"
  access_key: str
  secret_key: str
  compression: str       # default: "snappy"

# Job
ExtractRequest:
  source: SQLSourceConfig | SFTPSourceConfig    # discriminated by "type"
  destination: S3ParquetDestinationConfig       # union, discriminated by "type"

JobStatus:
  job_id: str
  status: "queued" | "running" | "success" | "failed" | "partial_success"
  rows_read: int | None
  rows_written: int | None
  duration_ms: int | None
  log_count: int
  failed_files: list[str]
  error: str | None

# Nota: status "partial_success" usado pelo SFTPSource quando alguns arquivos
# falham no parse mas outros são escritos com sucesso (ver §10).

LogsResponse:
  job_id: str
  logs: list[str]
  next_offset: int
```

### Separação entre ExtractRequest e Job (isolamento de credenciais)

`ExtractRequest` contém credenciais (connection string, access_key, secret_key). O objeto `Job` armazenado em memória **não deve** conter a request original — apenas os campos necessários para execução e reporte de status.

```python
# ExtractRequest: existe apenas durante a validação e início do job
# Destruído logo após source e destination serem instanciados

@dataclass
class Job:
    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    rows_read: int | None
    rows_written: int | None
    failed_files: list[str]
    logs: list[str]
    error: str | None
    # NUNCA armazenar: connection string, access_key, secret_key, password
```

**Sobrescrever `__repr__` nos modelos sensíveis** para evitar vazamento acidental em logs de exceção:

```python
class SQLSourceConfig(BaseModel):
    type: Literal["sql"]
    connection: str
    query: str
    ...

    def __repr__(self) -> str:
        return f"SQLSourceConfig(connection={mask_uri(self.connection)!r}, query=...)"
```

---

## 7. Plugin Contracts (ABCs)

```python
class Source(ABC):
    @abstractmethod
    def extract(self) -> pa.Table:
        """Read from source and return an Arrow Table.
        Use for sources where the full dataset fits comfortably in memory."""

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        """Stream data as batches of Arrow Tables.
        Default implementation wraps extract() — override for memory-efficient sources.
        Sources with large datasets (e.g. SFTPSource) should override this method."""
        yield self.extract()

class Destination(ABC):
    @abstractmethod
    def write(self, table: pa.Table, is_first_chunk: bool = True) -> int:
        """Write Arrow Table to destination. Returns row count.
        is_first_chunk=True: limpa dados existentes antes de escrever (delete_matching).
        is_first_chunk=False: append ao dataset já iniciado (overwrite_or_ignore)."""

class Parser(ABC):
    @abstractmethod
    def parse(self, data: bytes) -> pa.Table:
        """Convert raw bytes from a binary source into an Arrow Table."""
```

Note: the `is_first_chunk` parameter resolves the ambiguity in §10 about when to use `delete_matching` vs `overwrite_or_ignore` for incremental writes.

All plugins receive their configuration via `__init__` (unpacked from the Pydantic model). They must not perform I/O in `__init__` — connection setup happens inside `extract()` or `write()`.

---

## 8. Queue Design

The queue is **in-memory** (asyncio). No Redis or external broker required for v1.

```
POST /jobs arrives (padrão) ou POST /extract (síncrono, apenas dev/debug)
      │
      ▼
  queue.submit(job)
      │
      ├── workers < MAX_WORKERS?  →  dispatch immediately
      │
      ├── queue size < MAX_QUEUE? →  enqueue, return job_id
      │
      └── queue full?             →  raise HTTP 429
```

Configuration via environment variables:

| Variable | Default | Description |
|---|---|---|
| `SIPHON_MAX_WORKERS` | `10` | Max parallel extraction jobs |
| `SIPHON_MAX_QUEUE` | `50` | Max jobs waiting in queue |
| `SIPHON_JOB_TIMEOUT` | `3600` | Seconds before a job is killed |
| `SIPHON_LOG_LEVEL` | `INFO` | Logging verbosity |
| `SIPHON_TIMEZONE` | `America/Sao_Paulo` | Timezone para variáveis de data (@TODAY etc.) |
| `SIPHON_LOG_FORMAT` | `text` | Formato de log: `text` (dev) ou `json` (produção) |
| `SIPHON_PORT` | `8000` | Porta HTTP do serviço |
| `SIPHON_DRAIN_TIMEOUT` | `3600` | Segundos para aguardar jobs em execução ao receber SIGTERM (deve ser ≤ terminationGracePeriodSeconds) |
| `SIPHON_S3_SCHEME` | `https` | Protocolo para conexão S3/MinIO (`http` ou `https`) |
| `SIPHON_CONNECT_TIMEOUT` | `30` | Timeout de conexão com bancos de dados (segundos) |
| `SIPHON_API_KEY` | _(not set)_ | Bearer token para autenticação da API. Se não configurado, auth desabilitada com warning. |
| `SIPHON_ALLOWED_HOSTS` | _(not set)_ | Lista de hosts/CIDRs permitidos nas connection strings (ex: `10.0.0.0/8,db.internal`). Se não configurado, modo permissivo com warning. |
| `SIPHON_ALLOWED_S3_PREFIX` | `bronze/` | Prefixo obrigatório para paths de destino S3. Rejeita escritas fora deste prefixo. |
| `SIPHON_MAX_REQUEST_SIZE_MB` | `1` | Tamanho máximo do request body em MB. |
| `SIPHON_MAX_FILE_SIZE_MB` | `500` | Tamanho máximo de arquivo SFTP em MB. Arquivos maiores são pulados. |
| `SIPHON_SFTP_KNOWN_HOSTS` | _(not set)_ | Path para arquivo known_hosts para verificação de host key SFTP. |
| `SIPHON_SFTP_HOST_KEY` | _(not set)_ | Chave pública do servidor SFTP em base64 (alternativa ao known_hosts). |

Job state is kept in memory. If the service restarts, in-flight jobs are lost — Airflow retries handle recovery.

### Mecanismo de timeout

`ThreadPoolExecutor` não interrompe threads bloqueadas em I/O. Para garantir que jobs travados (ex: query Oracle sem resposta) sejam encerrados, o `worker.py` usa `asyncio.wait_for`:

```python
async def run_job(job: Job) -> None:
    try:
        await asyncio.wait_for(
            _execute_in_thread(source, destination),
            timeout=SIPHON_JOB_TIMEOUT,
        )
    except asyncio.TimeoutError:
        job.status = "failed"
        job.error = f"Job exceeded timeout of {SIPHON_JOB_TIMEOUT}s"
```

**Limitação conhecida:** `asyncio.wait_for` cancela a coroutine, mas a thread no `ThreadPoolExecutor` continua rodando até o I/O retornar (não é possível matar threads em Python). O job é marcado como `failed` imediatamente, e o worker slot fica disponível — mas a thread pode permanecer viva até a query retornar ou a conexão TCP dar timeout no nível de rede. Para jobs com queries verdadeiramente travadas, o timeout de rede do banco de dados (`connect_timeout`, `query_timeout` na connection string) é a linha de defesa real.

### Graceful Shutdown (SIGTERM → drain → exit)

Quando o Kubernetes encerra o pod (rolling update, scale down, node drain), o processo recebe SIGTERM. Sem tratamento, jobs em execução são mortos no meio — podendo deixar dados parciais no MinIO.

**Fluxo obrigatório:**

```
SIGTERM recebido
      │
      ├── queue.close()           # para de aceitar novos jobs (retorna 503 em /health/ready)
      │
      ├── aguarda jobs ativos terminarem
      │     └── timeout = SIPHON_DRAIN_TIMEOUT
      │           ├── todos terminaram?  →  exit(0)
      │           └── timeout esgotado?  →  marca jobs restantes como "failed", exit(1)
      │
      └── log: "Graceful shutdown complete. N jobs drained, M jobs aborted."
```

**Implementação no `main.py`:**

```python
from contextlib import asynccontextmanager
import signal, asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    queue.start()
    yield
    # SIGTERM handler — Kubernetes sends this before killing the pod
    await queue.drain(timeout=SIPHON_DRAIN_TIMEOUT)

app = FastAPI(lifespan=lifespan)
```

**Kubernetes `terminationGracePeriodSeconds` deve ser maior que `SIPHON_DRAIN_TIMEOUT`:**

```yaml
spec:
  terminationGracePeriodSeconds: 3660  # SIPHON_DRAIN_TIMEOUT (3600) + 60s buffer
```

**`preStop` hook** garante que o load balancer para de rotear tráfego antes do drain:

```yaml
lifecycle:
  preStop:
    exec:
      command: ["sleep", "5"]  # aguarda load balancer remover o pod do pool
```

### Segurança em runtime

**Limite de tamanho do request body** — configurado via middleware FastAPI:

```python
from starlette.middleware.limits import RequestSizeLimitMiddleware

app.add_middleware(
    RequestSizeLimitMiddleware,
    max_content_size=int(os.getenv("SIPHON_MAX_REQUEST_SIZE_MB", "1")) * 1024 * 1024,
)
```

**Desabilitar log automático de request body em erros 422** — FastAPI loga o body completo por padrão em erros de validação, expondo credenciais:

```python
import logging
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

# Sobrescrever o handler de ValidationError para não logar o body:
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Request validation failed", "errors": exc.errors()},
        # body NÃO incluído na resposta nem no log
    )
```

**API key middleware** — aplicado antes de qualquer rota:

```python
API_KEY = os.getenv("SIPHON_API_KEY")

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if API_KEY and request.url.path not in ("/health/live", "/health/ready"):
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)
```

Nota: `/health/live` e `/health/ready` ficam fora da auth para que os probes do Kubernetes funcionem sem credenciais.

---

## 9. SQL Source Implementation Notes

**Engine: ConnectorX**

ConnectorX is written in Rust and reads directly into Apache Arrow format without going through Python objects. It supports MySQL, PostgreSQL, Oracle, SQL Server, SQLite, and DuckDB via connection URL scheme.

```python
import connectorx as cx

table = cx.read_sql(
    conn="mysql://user:pass@host/db",
    query="SELECT ...",
    return_type="arrow",          # "arrow2" está deprecated em versões recentes
    partition_on="codSuporte",    # optional: for large tables
    partition_num=10,
)
```

The database type is inferred from the URL prefix (`mysql://`, `postgresql://`, `oracle+oracledb://`, `mssql://`). No additional configuration required.

**Variable resolution** happens before the query is sent to ConnectorX:

```python
def resolve(query: str) -> str:
    today = datetime.today()
    last_month = today - relativedelta(months=1)
    next_month = today + relativedelta(months=1)
    return (
        query
        .replace("@TODAY",      today.strftime("'%Y-%m-%d'"))
        .replace("@MIN_DATE",   "'1997-01-01'")
        .replace("@LAST_MONTH", last_month.strftime("'%Y-%m-01'"))
        .replace("@NEXT_MONTH", next_month.strftime("'%Y-%m-01'"))
    )
```

### Mapeamento canônico de tipos

ConnectorX e pandas mapeiam tipos de banco para Arrow de formas diferentes. A tabela abaixo define o comportamento esperado — divergências devem ser tratadas no plugin, não silenciosamente aceitas:

| Tipo no banco | Engine | Arrow esperado | Ação necessária |
|---|---|---|---|
| MySQL `TINYINT(1)` | ConnectorX | `bool` | Aceito — documentar que Silver deve tratar como bool |
| MySQL `UNSIGNED BIGINT` | ConnectorX | `uint64` | Verificar se ConnectorX usa uint64; se usar int64, overflow em valores > 2^63 |
| MySQL/PG `DECIMAL(p,s)` | ConnectorX | `decimal128(p,s)` | Correto — não usar float |
| Oracle `NUMBER` (sem precisão) | pandas+oracledb | `float64` | **Problema**: perda de precisão inteira. Usar `cursor.description` para inferir e forçar `Int64` quando scale=0 |
| Oracle `DATE` | pandas+oracledb | `datetime64[ns]` | Oracle DATE inclui hora — correto usar datetime, NÃO truncar para date |
| Oracle `CLOB` | pandas+oracledb | `object` → `string` | Converter explicitamente para `pa.string()` |
| `TIMESTAMP WITH TIME ZONE` | qualquer | `timestamp[us, tz=UTC]` | Normalizar para UTC no Arrow — ver seção de timezone abaixo |

**Regra geral:** nunca usar `float` para valores monetários ou identificadores numéricos. `DECIMAL` deve ser `decimal128`. `NUMBER(18,0)` deve ser `int64`.

### Timezone: `@TODAY` e timestamps

`datetime.today()` retorna a hora local do container, que roda em UTC. Para empresas em UTC-3 (Brasília), jobs rodando entre 21h00–23h59 UTC retornam a data do dia seguinte — extração de "hoje" vira extração de "amanhã".

**Correção obrigatória:**

```python
from zoneinfo import ZoneInfo
import os

_TZ = ZoneInfo(os.getenv("SIPHON_TIMEZONE", "America/Sao_Paulo"))

def resolve(query: str) -> str:
    today = datetime.now(tz=_TZ)
    last_month = today - relativedelta(months=1)
    next_month = today + relativedelta(months=1)
    return (
        query
        .replace("@TODAY",      today.strftime("'%Y-%m-%d'"))
        .replace("@MIN_DATE",   "'1997-01-01'")
        .replace("@LAST_MONTH", last_month.strftime("'%Y-%m-01'"))
        .replace("@NEXT_MONTH", next_month.strftime("'%Y-%m-01'"))
    )
```

Nova env var:

| Variable | Default | Description |
|---|---|---|
| `SIPHON_TIMEZONE` | `America/Sao_Paulo` | Timezone para resolução de variáveis de data |

Adicionar esta env var também na tabela do §8.

### Injeção automática de connect_timeout

Se a connection string não contiver `connect_timeout`, o `SQLSource` o injeta automaticamente antes de conectar. Evita threads presas em bancos inacessíveis que esgotariam o `ThreadPoolExecutor`.

```python
import os
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

_CONNECT_TIMEOUT = int(os.getenv("SIPHON_CONNECT_TIMEOUT", "30"))

def _inject_timeout(conn: str) -> str:
    """Injeta connect_timeout na connection string se não estiver presente."""
    parsed = urlparse(conn)
    params = parse_qs(parsed.query)
    if "connect_timeout" not in params and "connectTimeout" not in params:
        separator = "&" if parsed.query else ""
        new_query = f"{parsed.query}{separator}connect_timeout={_CONNECT_TIMEOUT}"
        return urlunparse(parsed._replace(query=new_query))
    return conn
```

Para Oracle (via `oracledb.connect`), o timeout é passado diretamente:
```python
conn = oracledb.connect(dsn=self._oracle_dsn(), tcp_connect_timeout=_CONNECT_TIMEOUT)
```

### Validação de host contra SSRF

Antes de qualquer conexão, o `SQLSource` valida o host extraído da connection string:

```python
import ipaddress
import os
from urllib.parse import urlparse

_ALLOWED_HOSTS = os.getenv("SIPHON_ALLOWED_HOSTS", "")

def _validate_host(conn: str) -> None:
    """Rejeita conexões para hosts fora da allowlist. Mitiga SSRF."""
    if not _ALLOWED_HOSTS:
        return  # modo permissivo — warning já foi emitido no startup

    host = urlparse(conn).hostname
    allowed = [h.strip() for h in _ALLOWED_HOSTS.split(",")]

    for entry in allowed:
        try:
            network = ipaddress.ip_network(entry, strict=False)
            if ipaddress.ip_address(host) in network:
                return
        except ValueError:
            if host == entry or host.endswith(f".{entry}"):
                return

    raise ValueError(f"Host '{host}' not in SIPHON_ALLOWED_HOSTS. Connection rejected.")
```

### Oracle e o limite de 500MB

ConnectorX implementa seus próprios drivers em Rust e **não usa SQLAlchemy**. A URL `oracle+oracledb://` é um formato SQLAlchemy — o ConnectorX não a entende. Para Oracle, o ConnectorX exige o Oracle Instant Client instalado no container (~200–300MB), o que por si só ameaça o NFR-02.

**Solução adotada: driver separado para Oracle**

Oracle não passa pelo ConnectorX. O `SQLSource` inspeciona o prefixo da connection string e roteia:

```python
def extract(self) -> pa.Table:
    if self.connection.startswith("oracle"):
        return self._extract_oracle()   # pandas + oracledb (thin mode)
    return self._extract_connectorx()   # todos os outros
```

Para Oracle, usa-se `pandas` + `oracledb` em modo thin (Python puro, sem Instant Client) + `pyarrow.Table.from_pandas()`:

```python
import oracledb
import pandas as pd
import pyarrow as pa

def _extract_oracle(self) -> pa.Table:
    # oracledb thin mode: sem Oracle Instant Client
    # LIMITAÇÃO: pd.read_sql carrega tudo em memória antes de converter para Arrow.
    # Para tabelas grandes, o pico de memória é ~2× o tamanho dos dados
    # (DataFrame + Arrow Table coexistem até o return).
    # Mitigação: usar chunksize para tabelas > 1M linhas (configurável via partition_num).
    conn = oracledb.connect(dsn=self._oracle_dsn())
    if self.partition_num and self.partition_num > 1:
        chunks = pd.read_sql(self.query, conn, chunksize=100_000)
        return pa.concat_tables([
            pa.Table.from_pandas(chunk, preserve_index=False)
            for chunk in chunks
        ])
    df = pd.read_sql(self.query, conn)
    return pa.Table.from_pandas(df, preserve_index=False)
```

Connection string esperada para Oracle: `oracle+oracledb://user:pass@host:1521/service`
O prefixo `oracle+oracledb://` é parseado pelo SQLSource para extrair host, porta, serviço, usuário e senha antes de passar ao `oracledb.connect()`.

**Implicação:** Oracle é ~3–5× mais lento que ConnectorX por não usar Arrow nativo. Aceitável para v1 — o gargalo real era o startup do Spark (30–60s), não o throughput do driver.

### Segurança de credenciais

A connection string contém usuário e senha em texto plano (ex: `mysql://user:SenhaProducao@host/db`). Regras obrigatórias:

1. **Logs nunca devem exibir a URI completa.** Usar uma função de mascaramento antes de qualquer log:

```python
import re

def mask_uri(uri: str) -> str:
    """mysql://user:SenhaProducao@host/db  →  mysql://***:***@host/db"""
    return re.sub(r"(://)[^:]+:[^@]+(@)", r"\1***:***\2", uri)
```

Toda referência à connection string em log deve passar por `mask_uri()`.

2. **`JobStatus` em memória não deve armazenar o request original** — apenas os campos de status, logs, rows, error. As credenciais não devem persistir no estado do job após o início da execução.

3. **Credenciais MinIO no `SiphonOperator`:** o operador deve buscar endpoint/access_key/secret_key de uma Airflow Connection do tipo `aws` (ou `generic`) com `conn_id` configurável, não de parâmetros hardcoded no DAG. Adicionar parâmetro `destination_conn_id: str` ao `SiphonOperator.__init__`.

### I/O bloqueante em contexto asyncio

`extract()` e `write()` são métodos síncronos (ConnectorX e Paramiko são blocking). Rodá-los diretamente no event loop vai serializar todos os jobs e bloquear o servidor inteiro.

O `worker.py` deve despachar cada job num `ThreadPoolExecutor`:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=SIPHON_MAX_WORKERS)

async def run_job(job: Job) -> None:
    loop = asyncio.get_event_loop()
    table = await loop.run_in_executor(_executor, source.extract)
    rows  = await loop.run_in_executor(_executor, destination.write, table)
```

O número de threads do executor deve ser igual a `SIPHON_MAX_WORKERS` para evitar contenção.

---

## 10. SFTP Source Implementation Notes

**Engine: Paramiko**

Critical constraints derived from existing problems in the current codebase:

- **One SSH connection per job**, not per file. The current implementation opens and closes a connection for every file (32 in parallel), exhausting the server's session limit.
- **Retry with exponential backoff** on connection and file download errors.
- **File move before processing** (`processing_folder`): atomic rename on the SFTP server, preventing double-processing if the job is retried.
- **File move after success** (`processed_folder`): only after the destination write confirms success.
- **Skip patterns** (`TMP_*` etc.) must be configurable, not hardcoded.
- **`FileNotFoundError`** during move operations must raise, not be silently swallowed.
- **Host key verification obrigatória**: usar `RejectPolicy` por padrão. `AutoAddPolicy` é explicitamente proibido — abre vetor de MITM para dados sensíveis. Configurar via `SIPHON_SFTP_KNOWN_HOSTS` ou `SIPHON_SFTP_HOST_KEY`.
- **Tamanho máximo de arquivo**: arquivos maiores que `SIPHON_MAX_FILE_SIZE_MB` são pulados com log de warning, nunca baixados para memória.

```python
def _single_connection(self):
    """Estabelece conexão SSH com verificação de host key."""
    ssh = paramiko.SSHClient()

    known_hosts = os.getenv("SIPHON_SFTP_KNOWN_HOSTS")
    host_key_b64 = os.getenv("SIPHON_SFTP_HOST_KEY")

    if known_hosts:
        ssh.load_host_keys(known_hosts)
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    elif host_key_b64:
        # aceita apenas a chave configurada explicitamente
        key = paramiko.RSAKey(data=base64.b64decode(host_key_b64))
        ssh.get_host_keys().add(self.host, "ssh-rsa", key)
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # sem configuração: RejectPolicy mesmo assim — falha com mensagem clara
        ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
        # startup warning já foi emitido informando que SFTP_KNOWN_HOSTS não está configurado

    ssh.connect(self.host, port=self.port, username=self.username, password=self.password)
    return ssh.open_sftp()
```

```python
class SFTPSource(Source):
    def extract(self) -> pa.Table:
        # ATENÇÃO: concat_tables com batch_size=1000 arquivos binários pode acumular
        # dezenas de GBs na RAM antes de qualquer escrita.
        # Para datasets grandes, use extract_batches() + escrita incremental.
        with self._single_connection() as sftp:
            files = self._list_and_filter(sftp)
            if self.processing_folder:
                files = self._move_to_processing(sftp, files)
            tables = []
            for f in files:
                data = self._download_with_retry(sftp, f)
                tables.append(self._parser.parse(data))
            return pa.concat_tables(tables)

    def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
        """Versão streaming: yield um pa.Table por lote de arquivos.
        Usar quando max_files total for grande (> 200 arquivos).
        O worker chama write() incrementalmente para cada batch."""
        with self._single_connection() as sftp:
            files = self._list_and_filter(sftp)
            if self.processing_folder:
                files = self._move_to_processing(sftp, files)
            for chunk in _chunked(files, chunk_size):
                tables = [self._parser.parse(self._download_with_retry(sftp, f)) for f in chunk]
                yield pa.concat_tables(tables)
```

### Tolerância a falhas parciais

Se um arquivo falha no parse (formato corrompido, truncado), o comportamento padrão é:

- **`fail_fast: bool = False` (padrão):** registra o erro no log, continua processando os demais arquivos, retorna `status: "partial_success"` com `failed_files: list[str]` no JobStatus.
- **`fail_fast: bool = True`:** aborta no primeiro erro, retorna `status: "failed"`.

Adicionar `fail_fast: bool = False` ao `SFTPSourceConfig`.

`partial_success` aciona `AirflowException` no `SiphonOperator` por padrão (conservador). Configurável via `allow_partial: bool = False` no operador.

**O `worker.py` verifica se o source implementa `extract_batches()` e usa escrita incremental quando disponível:**

```python
# All sources support extract_batches() via ABC default or override.
# Sources that override it (e.g. SFTPSource) get memory-efficient streaming.
for i, batch in enumerate(source.extract_batches()):
    destination.write(batch, is_first_chunk=(i == 0))
```

O destino `S3ParquetDestination` suporta múltiplas chamadas a `write()` — `write_to_dataset` com `existing_data_behavior="delete_matching"` na primeira chamada, `"overwrite_or_ignore"` nas subsequentes do mesmo job.

---

## 11. Destination Implementation Notes

**Engine: PyArrow (S3FileSystem built-in)**

### Validação do path de destino

```python
import os

_ALLOWED_PREFIX = os.getenv("SIPHON_ALLOWED_S3_PREFIX", "bronze/")

def _validate_path(path: str) -> None:
    """Rejeita paths fora do prefixo permitido e paths com traversal."""
    # remove scheme
    normalized = path.replace("s3a://", "").replace("s3://", "")
    if ".." in normalized:
        raise ValueError(f"Path traversal detected in destination path: {path!r}")
    # extrai só o path sem bucket se necessário, ou valida o path completo
    if not normalized.startswith(_ALLOWED_PREFIX):
        raise ValueError(
            f"Destination path {path!r} is outside allowed prefix '{_ALLOWED_PREFIX}'. "
            f"Set SIPHON_ALLOWED_S3_PREFIX to change."
        )
```

```python
import pyarrow.parquet as pq
import pyarrow.fs as pafs

import os

fs = pafs.S3FileSystem(
    endpoint_override=self.endpoint,
    access_key=self.access_key,
    secret_key=self.secret_key,
    scheme=os.getenv("SIPHON_S3_SCHEME", "https"),  # nunca hardcoded — padrão https para produção
)

pq.write_to_dataset(
    table,
    root_path=self.path.replace("s3a://", ""),
    filesystem=fs,
    compression=self.compression,
    existing_data_behavior="delete_matching",  # overwrite_or_ignore NÃO sobrescreve — re-runs do DAG deixariam dados antigos na partição
)
```

The output Parquet format is identical to what Spark's `write_bronze()` produces today. Silver Spark jobs can read it without any changes.

### Validação de compatibilidade com Spark

`write_to_dataset` produz arquivos `part-0.parquet`. Spark produz `part-00000-[uuid].snappy.parquet`. O conteúdo das colunas deve ser equivalente, mas os metadatos do arquivo (writer version, `created_by`, schema metadata Spark) serão diferentes.

**Antes de migrar qualquer DAG para produção:**
1. Executar o job Siphon para uma entidade pequena
2. Rodar o Silver job correspondente apontando para o output do Siphon
3. Comparar schema Arrow inferido pelo Spark vs schema original
4. Validar contagem de linhas e amostra de valores

Especial atenção para tipos `decimal128` vs `double` — Spark pode inferir diferente dependendo da versão do PyArrow usada para escrever.

---

## 11a. Dockerfile

Multi-stage build obrigatório para manter a imagem abaixo de 500MB.

```dockerfile
# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# instala uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/

# instala dependências em /app/.venv sem cache (reduz tamanho)
RUN uv sync --frozen --no-dev --no-cache

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# usuário não-root (NFR-13)
RUN groupadd -r siphon && useradd -r -g siphon -u 1000 siphon

WORKDIR /app

# copia apenas o virtualenv e o código — sem uv, sem cache pip
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# variáveis de ambiente
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIPHON_PORT=8000

EXPOSE ${SIPHON_PORT}

USER siphon

# read-only filesystem: /tmp é o único diretório gravável (NFR-13)
# --tmpfs /tmp é adicionado no Kubernetes securityContext

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${SIPHON_PORT}/health/live')"

CMD ["sh", "-c", "uvicorn siphon.main:app --host 0.0.0.0 --port ${SIPHON_PORT} --workers 1"]
```

**Notas:**
- `--workers 1`: obrigatório — estado in-memory não é compartilhado entre processos uvicorn
- `PYTHONUNBUFFERED=1`: garante que logs aparecem imediatamente no stdout (sem buffering)
- Multi-stage elimina o uv, cache pip, e arquivos de build do runtime

---

## 12. Integration with Airflow

### Por que não usar `POST /extract` (síncrono) para jobs longos

Manter uma conexão HTTP aberta por 5–15 minutos é frágil: proxies, load balancers e timeouts de rede podem encerrar a conexão antes do job terminar, causando falsa falha no Airflow mesmo com o job concluído com sucesso no Siphon.

### Padrão correto: submit + polling

O `SiphonOperator` usa o endpoint assíncrono e fica em polling até o job terminar. O worker do Airflow permanece vivo e acompanha o progresso em tempo real — sem depender de uma conexão HTTP aberta por minutos.

```
SiphonOperator.execute()
      │
      ├── POST /jobs  →  { job_id, status: "queued" }
      │
      └── loop:
            GET /jobs/{job_id}                →  { status, rows }
            GET /jobs/{job_id}/logs?since=N   →  { logs[], next_offset }
            │
            ├── status == "running"   →  log new lines, atualizar offset, sleep(poll_interval), repeat
            ├── status == "success"   →  log new lines, return rows
            └── status == "failed"    →  raise AirflowException(error)
```

**Implementação do operador:**

```python
class SiphonOperator(BaseOperator):
    def __init__(
        self,
        *,
        http_conn_id: str,
        source_type: str,                  # "sql" | "sftp"
        connection_id: str,                # Airflow Connection com a URI do banco
        destination_conn_id: str,          # Airflow Connection com credenciais do MinIO (tipo "aws" ou "generic")
        sql_file: str | None = None,       # caminho relativo ao DAG folder
        query: str | None = None,          # SQL inline (alternativa ao sql_file)
        destination_path: str,             # "s3a://bronze/entidade/{{ ds }}"
        poll_interval: int = 10,           # segundos entre cada GET /jobs/{id}
        **kwargs,
    ): ...

    def execute(self, context):
        uri = self._resolve_connection(self.connection_id)
        sql = self._resolve_sql(context)
        payload = self._build_payload(uri, sql, self.destination_path)

        response = self._http.post("/jobs", json=payload)
        job_id = response["job_id"]
        self.log.info("Siphon job submitted: %s", job_id)

        log_offset = 0
        while True:
            status = self._http.get(f"/jobs/{job_id}")

            # fetch only new log lines since last poll
            new_logs = self._http.get(f"/jobs/{job_id}/logs?since={log_offset}")
            for line in new_logs["logs"]:
                self.log.info("[siphon] %s", line)
            log_offset = new_logs["next_offset"]

            if status["status"] == "success":
                self.log.info("Extracted %d rows", status["rows_read"])
                return status["rows_read"]
            if status["status"] == "failed":
                raise AirflowException(f"Siphon job failed: {status['error']}")

            time.sleep(self.poll_interval)
```

**DAG structure (preserved):**

```python
@dag(dag_id="bronze_cidades", schedule="0 3,6 * * *", ...)
def bronze_cidades():
    check = check_deployment()
    btv_prod_usage = should_use_btv_prod()

    extract = SiphonOperator(
        task_id="extract",
        http_conn_id="siphon_service",
        source_type="sql",
        connection_id="source_db",
        sql_file="queries/entity.sql",
        destination_path="s3a://bronze/entity/{{ ds }}",
        outlets=[bronze_assets["entity"]],
    )

    check >> btv_prod_usage >> extract
```

O operador:
- Lê a URI de conexão do Airflow Connections (nunca hardcoded no DAG)
- Lê SQL de arquivo ou inline
- Emite os logs do Siphon em tempo real no log da task do Airflow
- Levanta `AirflowException` em falha (aciona retry automático do Airflow)
- Retorna o número de linhas extraídas via XCom

---

## 13. Escalabilidade

### v1 — Escala vertical (instância única)

Uma instância do Siphon com `SIPHON_MAX_WORKERS` configurável. Para a maioria dos cenários (200 DAGs, jobs de 2–15 min, execução distribuída ao longo do dia), aumentar os workers é suficiente.

```
SIPHON_MAX_WORKERS=20   # padrão: 10
SIPHON_MAX_QUEUE=100    # padrão: 50
```

### v2 — Escala horizontal (múltiplas instâncias)

Múltiplas instâncias do Siphon atrás de um load balancer exigem que o `GET /jobs/{id}` alcance a mesma instância que criou o job (estado in-memory).

**Solução sem dependências externas: roteamento por header**

Sticky sessions via cookie requerem que o cliente HTTP persista e reenvie o cookie — um `requests.get()` simples não faz isso. A abordagem correta é incluir o `job_id` no roteamento do load balancer, ou usar um header customizado.

Alternativa mais simples: **cada instância tem sua própria URL fixa**, e o `job_id` inclui um prefixo de instância:

```
job_id: "siphon-0/550e8400-..."   →  roteia para siphon-0
job_id: "siphon-1/7f3c1200-..."   →  roteia para siphon-1
```

O `SiphonOperator` extrai o prefixo do `job_id` e constrói a URL de polling correta:

```python
instance, uuid = job_id.split("/", 1)
poll_url = f"{self.base_url.replace('siphon', instance)}/jobs/{uuid}"
```

Ou via Kubernetes StatefulSet com `headless service` — cada pod tem DNS próprio (`siphon-0.siphon`, `siphon-1.siphon`), eliminando a necessidade de sticky sessions ou roteamento especial.

**Solução com estado compartilhado (opt-in, não obrigatório para v1):**

Adicionar suporte a Redis como backend de estado — ativado via env var `SIPHON_STATE_BACKEND=redis://...`. Quando não configurado, usa in-memory (comportamento padrão). Isso permite escala horizontal verdadeira sem sticky sessions.

| Cenário | Estratégia recomendada |
|---|---|
| ≤ 50 jobs simultâneos | 1 instância, aumentar `MAX_WORKERS` |
| > 50 jobs, SLA crítico | Sticky sessions + múltiplas instâncias |
| Alta disponibilidade | Redis backend + múltiplas instâncias |

---

## 14. Project Structure

```
siphon/
├── .github/
│   └── workflows/
│       ├── ci.yml             # ruff lint, ruff format check, pytest unit, docker build, smoke test, trivy scan, pytest integration
│       └── publish.yml        # tag v* → multi-arch build (amd64 + arm64) → push registry → update README badge
├── docker-compose.yml         # dev local: siphon + mysql + postgres + minio
├── docker-compose.test.yml    # CI: containers isolados para integration tests
├── Dockerfile                 # multi-stage: builder (uv) + runtime (non-root, read-only)
├── k8s/
│   ├── deployment.yaml        # Recreate strategy, probes, resources, securityContext, drain
│   ├── service.yaml           # ClusterIP porta 8000
│   ├── secret.yaml.template   # template de Secret (nunca commitar valores reais)
│   └── hpa.yaml               # HorizontalPodAutoscaler baseado em jobs_active (v2 — futuro)
├── pyproject.toml
├── README.md
└── src/
    └── siphon/
        ├── main.py            # FastAPI app, lifespan, routes, health endpoints
        ├── queue.py           # asyncio queue, worker pool, job state, drain logic
        ├── worker.py          # job execution: source → destination
        ├── models.py          # Pydantic: ExtractRequest, JobStatus, all configs
        ├── variables.py       # @TODAY, @MIN_DATE, @LAST_MONTH, @NEXT_MONTH resolver
        └── plugins/
            ├── sources/
            │   ├── __init__.py    # registry + autodiscovery
            │   ├── base.py        # ABC Source
            │   ├── sql.py         # SQLSource (ConnectorX + oracledb)
            │   └── sftp.py        # SFTPSource (Paramiko)
            ├── destinations/
            │   ├── __init__.py    # registry + autodiscovery
            │   ├── base.py        # ABC Destination
            │   └── s3_parquet.py  # S3ParquetDestination (PyArrow)
            └── parsers/
                ├── __init__.py    # registry + autodiscovery
                ├── base.py        # ABC Parser
                └── example_parser.py   # stub parser implementation
```

---

## 15. Tech Stack

| Component | Library | Reason |
|---|---|---|
| API framework | FastAPI | Async, fast, Pydantic-native |
| Job queue | asyncio | No external dependencies for v1 |
| SQL extraction | ConnectorX | Rust-based, reads directly to Arrow, 3–20× faster than pandas+JDBC, supports all required databases |
| Arrow/Parquet | PyArrow | Writes Parquet identical to Spark output, direct S3 support |
| S3 filesystem | PyArrow S3FileSystem (built-in) | Built into PyArrow via Arrow C++; MinIO compatible, sem boto3 ou s3fs necessários |
| SFTP | Paramiko | Standard Python SSH library |
| Data validation | Pydantic v2 | Discriminated unions, fast validation |
| CLI (optional) | Typer | For local testing and one-off runs |
| Package manager | uv | Modern, fast |
| Linter/formatter | Ruff | Already used in the host project |
| Container | Docker | Single container, no compose required |

---

## 16. What Is Explicitly Out of Scope

- **CDC / log-based streaming**: batch extraction only (full_refresh + incremental watermark)
- **Hard-delete tracking**: records deleted at source are not captured; use full_refresh for tables with hard deletes
- **Multi-source joins at extraction time**: Bronze must be source-faithful; joins belong in Silver
- **Connection pooling**: each job opens its own connection; pooling is a v2 concern
- **Silver and Gold layers**: Spark remains the engine for transformations
- **File parsing logic**: parsers exist as plugins, but the binary parsing logic itself is domain-specific and must be implemented per use case
- **mTLS entre Airflow e Siphon**: o `SiphonOperator` comunica-se com o Siphon via HTTP simples. mTLS pode ser adicionado via service mesh (Istio, Linkerd) sem mudanças no código.
- **Scanning de vulnerabilidades em tempo de execução**: o CI faz `trivy image scan` mas não há scanning de dependências em runtime. Usar `uv lock` com hashes verificados mitiga supply chain attacks em build time.
- **AI/schema-aware SQL completion**: CodeMirror uses dialect keyword completion only

---

## 16a. Kubernetes Manifests

### `deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: siphon
spec:
  replicas: 1
  strategy:
    type: Recreate          # obrigatório: estado in-memory — RollingUpdate causaria 404 no polling
  selector:
    matchLabels:
      app: siphon
  template:
    metadata:
      labels:
        app: siphon
    spec:
      terminationGracePeriodSeconds: 3660   # SIPHON_DRAIN_TIMEOUT (3600) + 60s buffer
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      containers:
        - name: siphon
          image: ghcr.io/your-org/siphon:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: siphon-secrets       # MinIO credentials, etc.
          env:
            - name: SIPHON_MAX_WORKERS
              value: "10"
            - name: SIPHON_MAX_QUEUE
              value: "50"
            - name: SIPHON_JOB_TIMEOUT
              value: "3600"
            - name: SIPHON_DRAIN_TIMEOUT
              value: "3600"
            - name: SIPHON_TIMEZONE
              value: "America/Sao_Paulo"
            - name: SIPHON_LOG_FORMAT
              value: "json"
            - name: SIPHON_S3_SCHEME
              value: "https"
            - name: SIPHON_CONNECT_TIMEOUT
              value: "30"
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "4"
              memory: "8Gi"    # 10 workers × ~800MB pico por job Oracle/SFTP
          livenessProbe:
            httpGet:
              path: /health/live
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health/ready
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
            failureThreshold: 2
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          lifecycle:
            preStop:
              exec:
                command: ["sleep", "5"]    # aguarda LB remover pod do pool antes do drain
      volumes:
        - name: tmp
          emptyDir: {}
```

### `service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: siphon
spec:
  selector:
    app: siphon
  ports:
    - port: 8000
      targetPort: 8000
  type: ClusterIP
```

### Sizing dos recursos

| Cenário | requests | limits |
|---|---|---|
| Dev/Staging | 250m CPU, 512Mi RAM | 1 CPU, 2Gi RAM |
| Produção (10 workers) | 500m CPU, 1Gi RAM | 4 CPU, 8Gi RAM |
| Produção (20 workers) | 1 CPU, 2Gi RAM | 8 CPU, 16Gi RAM |

**Memória limit** = `MAX_WORKERS × pico_por_job`. Pico por job Oracle (5M linhas, DataFrame + Arrow) ≈ 800MB. 10 workers = 8Gi limit.

### CI/CD pipeline (`ci.yml`)

```
push/PR:
  1. ruff check src/
  2. ruff format --check src/
  3. pytest tests/unit/ --tb=short
  4. docker build -t siphon:ci .
  5. docker run siphon:ci curl -f http://localhost:8000/health/live  (smoke test)
  6. docker compose -f docker-compose.test.yml up --abort-on-container-exit  (integration)
  7. trivy image --exit-code 1 --severity HIGH,CRITICAL siphon:ci

tag v*:
  1. todos os passos acima
  2. docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/org/siphon:${TAG}
  3. docker push ghcr.io/org/siphon:${TAG}
  4. docker push ghcr.io/org/siphon:latest
```

---

## 17. Definition of Done (v1)

<!-- Fases 1–9 implementadas (branch feature/phase-9-connections-pipelines-api, 256 testes, pendente merge para master). -->

- [x] `POST /jobs` + polling works end-to-end for a MySQL source → MinIO Parquet destination
- [x] `POST /jobs` + polling works end-to-end for an SFTP source (with a stub parser) → MinIO Parquet destination
- [x] Queue correctly limits concurrency and returns 429 when full
- [x] `GET /jobs/{id}` returns status and logs
- [x] `GET /health` returns active/max worker counts
- [x] `@TODAY`, `@MIN_DATE`, `@LAST_MONTH`, `@NEXT_MONTH` are resolved before SQL execution
- [x] A second source plugin can be added by creating one file in `plugins/sources/` with zero changes to core
- [x] A second destination plugin can be added by creating one file in `plugins/destinations/` with zero changes to core
- [x] Docker image builds successfully and is under 500MB
- [x] README covers: quickstart, API reference, how to write a plugin
- [x] Ruff passes with no violations
- [x] Unit tests cover: variable resolution, plugin registry, queue 429 behavior
- [x] Integration test (docker-compose) covers: MySQL → MinIO Parquet end-to-end
- [x] `pytest` passes with no failures
- [x] `GET /health/live` returns 200; `GET /health/ready` returns 503 when queue is full
- [x] SIGTERM triggers graceful drain — in-flight jobs complete before process exits
- [x] Docker image runs as non-root user (UID 1000)
- [x] `docker-compose.yml` starts full local dev environment (siphon + mysql + postgres + minio)
- [ ] `k8s/deployment.yaml` deploys successfully to a Kubernetes cluster _(Fase 11)_
- [x] `SIPHON_S3_SCHEME=https` works with a TLS MinIO endpoint
- [x] CI pipeline runs ruff + pytest + docker build + trivy scan on every PR
- [x] `SIPHON_API_KEY` configurado: rotas retornam 401 sem o header correto (exceto `/health/live` e `/health/ready`)
- [x] `SIPHON_ALLOWED_HOSTS` configurado: connection string para host fora da lista retorna 400
- [x] `SIPHON_ALLOWED_S3_PREFIX` configurado: path fora do prefixo retorna 400
- [x] Request body > 1MB retorna 413
- [x] Erro de validação (422) não loga nem retorna a connection string ou credenciais
- [x] Conexão SFTP com `AutoAddPolicy` não é possível — código usa `RejectPolicy` hardcoded
- [x] Startup sem `SIPHON_API_KEY` loga warning visível

---

## 18. UI & Management Layer (Phases 7–10)

Full design spec: `docs/superpowers/specs/2026-03-25-siphon-ui-design.md`

**Status de implementação:**
| Fase | Status | Branch/Commit |
|---|---|---|
| 7 — Hotfixes críticos | ✅ mergeado em `master` | `hotfix/phase-7` |
| 7.5 — Oracle cursor streaming | ✅ mergeado em `master` | `feature/phase-7.5-oracle-cursor-streaming` |
| 8 — PostgreSQL + Auth | ✅ mergeado em `master` (183 testes) | `feature/phase-8-postgres-auth`, 2026-03-28 |
| 9 — Connections + Pipelines API | ✅ implementado (256 testes) | `feature/phase-9-connections-pipelines-api`, 2026-03-29 |
| 10 — Frontend | ⏳ não iniciado | — |

### Overview

Siphon gains a management UI (Airbyte-style) served by the same FastAPI container. PostgreSQL becomes the persistence layer. The Airflow operator and all existing API endpoints remain unchanged.

### Architecture decision

Single FastAPI monolith. React build served via `StaticFiles`. All frontend calls go to `/api/v1/...` only — enabling future extraction to a separate container by changing `VITE_API_BASE_URL` with no code changes.

### New environment variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | required | PostgreSQL connection string for Alembic + SQLAlchemy |
| `SIPHON_ENCRYPTION_KEY` | required | Fernet key (base64-urlsafe 32 bytes) for connection credential encryption |
| `SIPHON_JWT_SECRET` | required | Secret for JWT signing |
| `SIPHON_ADMIN_EMAIL` | required on first start | Bootstrap admin user email |
| `SIPHON_ADMIN_PASSWORD` | required on first start | Bootstrap admin user password |
| `SIPHON_JOB_TTL_SECONDS` | `3600` | TTL for completed jobs in `_jobs` dict (OOM fix) |
| `SIPHON_ENABLE_SYNC_EXTRACT` | `false` | Enables `POST /extract` (disabled in production by default) |

### Auth

- Dual-auth: `SIPHON_API_KEY` (Airflow) + JWT (UI) — FastAPI dependency `get_current_principal`, not middleware
- JWT: access token 15min in React memory; refresh token 7d in `httpOnly` cookie scoped to `Path=/api/v1/auth/refresh`
- Token rotation on every refresh; reuse of revoked token cancels all user sessions
- Roles: `admin` (full CRUD) and `operator` (read + trigger only)

### Extraction modes

- `full_refresh` — current behavior (default)
- `incremental` — watermark stored as ISO-8601 UTC in `pipelines.last_watermark`; injected as type-aware SQL cast per dialect; updated only after successful `job_runs` DB write (avoids 2PC race on pod kill)

### Schema evolution

Arrow schema SHA-256 hash stored per pipeline. On divergence: job completes, `job_runs.schema_changed=true`, warning in logs, yellow badge in UI. Write is never blocked — engineer decides whether to do full refresh.

### Data quality

Per-pipeline optional: `min_rows_expected INT` and `max_rows_drop_pct INT`. Both checked after extraction, before writing. Failure marks job as `failed` and skips the S3 write.

### APScheduler safety

Uses PostgreSQL jobstore (survives restarts). Before firing any scheduled job: `SELECT pg_try_advisory_xact_lock(:id)` — if another pod holds the lock, job is skipped. Makes rolling deploys safe even if K8s strategy is changed from `Recreate`.

### Critical hotfixes (Phase 7)

1. **`_jobs` OOM**: TTL eviction — completed jobs older than `SIPHON_JOB_TTL_SECONDS` removed every 5 min
2. **SFTP stranded files**: on parse failure after `_move_to_processing()`, file is moved back to origin before being added to `failed_files`
3. **`/extract` guard**: returns 404 unless `SIPHON_ENABLE_SYNC_EXTRACT=true`

### Database schema (6 tables)

`users`, `connections` (Fernet-encrypted blob + `key_version`), `pipelines` (extraction_mode, incremental_key, last_watermark, last_schema_hash, min_rows_expected, max_rows_drop_pct), `schedules` (1:1 with pipeline, UNIQUE constraint), `job_runs` (BIGINT rows, schema_changed, schedule_id FK), `refresh_tokens` (rotation + reuse detection).

### Frontend stack

React 18 + Vite, React Router v6, TanStack Query, shadcn/ui + Tailwind, react-hook-form + zod, CodeMirror 6 (lazy-loaded, dialect per connection type), cronstrue, date-fns.

### Pipeline Wizard (4 steps)

1. Source connection
2. Query (CodeMirror) + Preview (SELECT LIMIT 100) + incremental toggle
3. Destination connection + S3 prefix + data quality config
4. Schedule (CronInput + cronstrue preview, optional) + read-only review

### Prometheus metrics

`GET /metrics` returns Prometheus text format: `siphon_jobs_total`, `siphon_job_duration_seconds`, `siphon_rows_extracted_total`, `siphon_queue_depth`, `siphon_schema_changes_total`.

### Phase 9 — módulos implementados

| Módulo | Arquivo | Responsabilidade |
|---|---|---|
| Crypto | `src/siphon/crypto.py` | Fernet encrypt/decrypt; chave via `SIPHON_ENCRYPTION_KEY` |
| Connections router | `src/siphon/connections/router.py` | CRUD, test-connection, types list |
| Pipelines router | `src/siphon/pipelines/router.py` | CRUD, trigger, schedule upsert/delete, runs paginados |
| Watermark | `src/siphon/pipelines/watermark.py` | `inject_watermark` + `_cast_for_dialect` (mysql/pg/oracle/mssql) |
| Preview router | `src/siphon/preview/router.py` | `POST /api/v1/preview` — LIMIT 100, SSRF guard |
| Runs router | `src/siphon/runs/router.py` | Lista global, logs cursor, cancel admin-only |
| Scheduler | `src/siphon/scheduler.py` | APScheduler + PostgreSQL jobstore + advisory lock |
| Metrics | `src/siphon/metrics.py` | Contadores/histogramas Prometheus |
| Worker (extensão) | `src/siphon/worker.py` | Schema hash, DQ, `_persist_job_run` UPDATE/INSERT, `_update_pipeline_metadata` |
| Migrations | `src/siphon/migrations/` | 001 (schema base), 002 (`dest_connection_id`, `triggered_by`) |

#### Dependências de test cross-module (armadilha conhecida)

`test_main.py` executa `importlib.reload(siphon.db)` e `importlib.reload(siphon.auth.deps)` para testar comportamento sem `DATABASE_URL`. Isso cria novos objetos de função. Qualquer fixture que capturar `get_db` ou `get_current_principal` antes do reload terá referência stale.

**Solução:** usar `module.get_db` e `module.get_current_principal` como chaves de override — nunca importar direto e capturar em variável de módulo. Ver `tests/test_pipelines.py` e `tests/test_connections.py` como referência.