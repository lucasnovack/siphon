# Siphon — Implementation Roadmap

Spec completa em `claude.md`. Implementar fase a fase, na ordem abaixo.
Cada fase termina com testes passando e código funcional antes de avançar.

---

## Fase 1 — Esqueleto do projeto ✅

- [x] Inicializar projeto com `uv init`, configurar `pyproject.toml` (deps, ruff, pytest)
- [x] Criar estrutura de pastas conforme §14 do `claude.md`
- [x] `models.py` — todos os modelos Pydantic
- [x] ABCs: `Source`, `Destination`, `Parser`
- [x] Registry + autodiscovery para sources, destinations, parsers
- [x] `variables.py` — resolver `@TODAY`, `@MIN_DATE`, `@LAST_MONTH`, `@NEXT_MONTH`
- [x] Testes unitários: variable resolution, registry

---

## Fase 2 — API e queue ✅

- [x] `queue.py` — asyncio queue, ThreadPoolExecutor, drain logic (SIGTERM)
- [x] `worker.py` — loop de execução: source.extract_batches() → destination.write()
- [x] `main.py` — FastAPI app, rotas: `/jobs`, `/extract`, `/health`
- [x] Middleware: request size limit, API key auth, 422 sem log de body

---

## Fase 3 — SQL Source ✅

- [x] `SQLSource` com ConnectorX (MySQL, PostgreSQL, MSSQL)
- [x] `_inject_timeout()`, `_validate_host()` (SSRF), `mask_uri()`
- [x] Oracle: `_extract_oracle()` com oracledb thin mode + cursor.fetchmany()
- [x] Campos de particionamento: `partition_on`, `partition_num`, `partition_range`

---

## Fase 4 — S3 Destination ✅

- [x] `S3ParquetDestination` com PyArrow S3FileSystem
- [x] `_validate_path()` — traversal check + prefix check
- [x] Staging atômico: write temp → rename
- [x] Validação `rows_read == rows_written`

---

## Fase 5 — SFTP Source ✅

- [x] `SFTPSource` com Paramiko, RejectPolicy
- [x] `_move_to_processing()` / `_move_to_processed()` / `_move_back_to_origin()`
- [x] `_download_with_retry()` — exponential backoff
- [x] `skip_patterns`, `SIPHON_MAX_FILE_SIZE_MB`, `fail_fast`, `partial_success`

---

## Fase 6 — Docker e CI ✅

- [x] `Dockerfile` — multi-stage, non-root UID 1000
- [x] `docker-compose.yml` — siphon + mysql + postgres + minio + sftp
- [x] `.github/workflows/ci.yml` — ruff, pytest unit, docker build, trivy, integration
- [x] `.github/workflows/publish.yml` — tag `v*` → GHCR

---

## Fase 7 — Hotfixes críticos ✅

- [x] TTL eviction de jobs terminados (`SIPHON_JOB_TTL_SECONDS`)
- [x] SFTP `_move_back_to_origin()` em falha de parse
- [x] `/extract` guard: 404 sem `SIPHON_ENABLE_SYNC_EXTRACT=true`

---

## Fase 7.5 — Oracle cursor streaming ✅

- [x] `_extract_oracle()` com `cursor.fetchmany()` nativo oracledb
- [x] `_oracle_output_type_handler()` — LOB, NUMBER, DATE
- [x] `_oracle_rows_to_arrow()` — conversão row-list → pa.Table

---

## Fase 8 — PostgreSQL + Auth ✅

- [x] Alembic + migrations (6 tabelas: users, connections, pipelines, schedules, job_runs, refresh_tokens)
- [x] `db.py` — SQLAlchemy async engine, session factory
- [x] Auth router: login, refresh, logout, me — JWT + httpOnly cookie
- [x] Token rotation + reuse detection
- [x] Rate limiting no login (slowapi)
- [x] Users router — CRUD admin-only
- [x] Worker persiste resultado em `job_runs`

---

## Fase 9 — Connections + Pipelines API ✅

- [x] `crypto.py` — Fernet encrypt/decrypt
- [x] Connections router: CRUD, test, types
- [x] Pipelines router: CRUD, schedule upsert, `GET /:id/runs`
- [x] `POST /pipelines/:id/trigger` — monta Job, cria job_run queued, enfileira
- [x] Preview router — `POST /preview` com LIMIT 100 + SSRF
- [x] Runs router — histórico global, logs cursor-based, cancel
- [x] `GET /metrics` — Prometheus
- [x] Scheduler: APScheduler + PostgreSQL jobstore + advisory lock multi-pod
- [x] Watermark: CTE `_siphon_base` + WHERE type-aware por dialeto
- [x] Schema evolution: hash SHA-256, `schema_changed=True`, escrita nunca bloqueada
- [x] Data quality: `min_rows_expected`, `max_rows_drop_pct`

---

## Fase 10 — Frontend ✅

- [x] Vite + React 18 + shadcn/ui + Tailwind + TanStack Query
- [x] Auth context, axios interceptor com refresh mutex
- [x] Páginas: Login, Dashboard, Connections, Pipelines, Runs, Settings/Users, Settings/System
- [x] PipelineWizard 4 steps, RunDetailPage + LogViewer polling, QueryEditor CodeMirror
- [x] FastAPI serve `frontend/dist/` (SPA fallback)
- [x] Dockerfile: stage `frontend-builder` node:22-slim

---

## Fase 10.5 — Security hardening ✅

- [x] 19 vulnerabilidades corrigidas (Trivy scan)

---

## Fase 11 — Retry, Parsers, PII, Staging ✅

- [x] Retry automático em SQL sources com backoff
- [x] CSV parser, JSON parser (com jq-style path)
- [x] PII masking configurável por coluna (hash / redact / partial)
- [x] Staging S3 atômico generalizado

---

## Fase 12 — Backfill, Hive, Webhooks, SLA ✅

- [x] Backfill API — rerun histórico com watermark override
- [x] Hive partitioning em Parquet (`_date=2024-04-03/`)
- [x] Webhook alerts: job failure, SLA breach
- [x] SLA configurável por pipeline (max duration)

---

## Fase 13 — HTTP/REST Source, Avro, BigQuery, Snowflake ✅

- [x] `HTTPRestSource` — pagination, auth headers, JSON path extraction
- [x] Avro parser
- [x] BigQuery destination *(removido na fase 15)*
- [x] Snowflake destination *(removido na fase 15)*

---

## Fase 14 — Observabilidade + Schema Registry ✅

- [x] structlog — JSON em prod, ConsoleRenderer em TTY, stdlib bridge
- [x] OTEL tracing — `trace_id`/`span_id` em cada log line, OTLP export opcional
- [x] Schema registry — `last_schema JSONB` + `expected_schema JSONB` em `pipelines`
- [x] DQ schema validation — missing col = error, type mismatch = error, extra col = warning
- [x] `PipelineResponse` retorna `last_schema`

---

## Fase 14-completion — Idempotência + Lineage ✅

- [x] Fix idempotência: trigger cria row "queued", worker faz UPDATE (sem duplicata)
- [x] Data lineage: `source_connection_id` + `destination_path` em `job_runs`
- [x] Expostos em `GET /runs` e `GET /runs/{id}`
- [x] 374 testes passando

---

## Fase 15 — Cleanup + Performance ✅

**Objetivo:** remover destinos não-Parquet, adicionar concurrency limits por conexão e priorização de jobs.

- [x] Remover `bigquery_dest.py`, `snowflake_dest.py`, modelos e testes associados
- [x] `max_concurrent_jobs` em `Connection` — limita jobs simultâneos por fonte (migration 008)
- [x] Worker verifica concorrência antes de iniciar; requeueia com backoff se no limite
- [x] `priority` enum (`low/normal/high`) em `pipelines` (migration 009)
- [x] Substituir `asyncio.Queue` por `asyncio.PriorityQueue` em `queue.py`
- [x] Frontend: campo `priority` no PipelineWizard e PipelineEditPage; `max_concurrent_jobs` no ConnectionForm

---

## Fase 16 — Celery + Redis (Escala horizontal) ✅

**Objetivo:** desacoplar API e workers; múltiplos pods de worker consumindo a mesma fila.

- [x] `celery_app.py` — Celery configurado com Redis broker/backend, filas `high/normal/low`
- [x] `tasks.py` — `@celery_app.task run_pipeline_task(job_dict)` chamando `run_job()` existente
- [x] Estado de jobs migra de `_jobs` in-memory para `job_runs` no PostgreSQL
- [x] `GET /jobs/{id}` lê do DB; cancel via `celery revoke(terminate=True)`
- [x] `queue.py` vira wrapper fino: `enqueue()` → `apply_async(queue=priority)`
- [x] Concorrência por conexão lê de `job_runs WHERE status='running'` (sem dict in-memory)
- [x] `docker-compose.yml`: adicionar `redis:7-alpine` + `siphon-worker`
- [x] Graceful drain: `task_acks_late=True`, `worker_prefetch_multiplier=1`

---

## Fase 17 — GDPR Compliance ✅

**Objetivo:** soft delete em todas as entidades + API de purge de dados no S3.

- [x] `deleted_at TIMESTAMPTZ` nullable em `connections`, `pipelines`, `schedules`, `users` (migration 010)
- [x] Todos os `GET` filtram `WHERE deleted_at IS NULL`; `DELETE` seta `deleted_at = now()`
- [x] Cascade: soft-delete de connection → soft-delete pipelines + remove schedules Celery
- [x] `DELETE /api/v1/pipelines/{id}/data` — purge S3 com params `?before=date&partition=val` (admin-only)
- [x] Purge síncrono (<1000 arquivos) ou background Celery task (≥1000, retorna 202)
- [x] Tabela `gdpr_events` + migration 011: registra cada purge com arquivos/bytes deletados
- [x] `GET /api/v1/gdpr/events` e `GET /api/v1/gdpr/events/{id}` (admin-only)

---

## Definition of Done global

Antes de considerar v1 completo, todos os itens do §17 do `claude.md` devem estar checados.
