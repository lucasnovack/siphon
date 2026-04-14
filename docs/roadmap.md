# Siphon Roadmap

Histórico de fases entregues e plano de evolução para tornar o Siphon production-ready como ferramenta de dados.

---

## Fases entregues

### Phase 1 — Skeleton ✅

Scaffolding completo: estrutura de pastas, modelos Pydantic, ABCs de plugins, registry com autodiscovery, resolver de variáveis de data.

### Phase 2 — API + Queue ✅
HTTP service com job queue funcional: `POST /jobs`, `POST /extract`, `GET /jobs/{id}`, health endpoints, middleware de segurança.

### Phase 3 — SQL Source ✅
`SQLSource` cobrindo MySQL, PostgreSQL, MSSQL, SQLite via ConnectorX e Oracle via oracledb thin mode.

### Phase 4 — SFTP Source + S3 Parquet Destination ✅
`SFTPSource` com retry e processamento por lote. `S3ParquetDestination` com compressão Snappy e path validation.

### Phase 5 — Incremental Extraction ✅
Watermark injection com CTE por dialeto (MySQL, PostgreSQL, Oracle, MSSQL). Atualização automática de `last_watermark` após sucesso.

### Phase 6 — Data Quality Guards ✅
Checks de `min_rows_expected` e `max_rows_drop_pct`. Schema hash para detecção de schema changes. Alertas em job logs.

### Phase 7 — Hotfixes ✅
Correção de OOM em `_jobs` dict, SFTP stranded files em processing folder, e `POST /extract` sempre habilitado.

### Phase 7.5 — Oracle Cursor Streaming ✅
Substituição do path pandas por cursor streaming com `oracledb`. Memória peak agora é O(chunk\_size) independente do tamanho da tabela.

### Phase 8 — PostgreSQL + Auth ✅
Schema PostgreSQL com 6 tabelas via Alembic. Dual-auth (API key + JWT) com CRUD de usuários, bootstrap de admin e persistência de `job_runs` no worker.

### Phase 9 — Connections + Pipelines API ✅
API de gestão completa (`/api/v1/connections`, `/api/v1/pipelines`, `/api/v1/runs`, `/api/v1/preview`). Scheduling com APScheduler + advisory lock. Métricas Prometheus. Criptografia de credenciais com Fernet.

### Phase 10 — Frontend MVP ✅
React SPA com Vite + TypeScript. Páginas de login, connections, pipelines, runs. Preview de queries. Autenticação JWT com refresh token em cookie httpOnly.

### Phase 10.5 — Security Hardening ✅
SQL injection via `incremental_key` (CRITICAL). Auth em `/health` e `/metrics`. Rate limiting em endpoints sensíveis. Narrowing de `except Exception` em auth. Log CRITICAL para JWT secret padrão. URL-decode em path traversal. Headers de segurança HTTP. Masking de credenciais em logs.

---

## Fases de produção

As fases abaixo completam o Siphon como plataforma production-ready, entregues após a análise de engenharia de dados de 2026-04-04.

---

### Phase 11 — Confiabilidade e Parsers ✅ (entregue 2026-04-04)

**Branch:** `master` | **Testes:** 352

- **Retry em SQL sources** ✅ — exponential backoff com jitter
- **Idempotência / deduplicação** ✅ — staging path (`_staging/{job_id}`) + promote após DB; fix de ordem (promote antes do watermark) entregue na Phase 14 Completion
- **CSV parser** ✅
- **JSON / JSONL parser** ✅
- **PII masking básico** ✅ — sha256 / redact por coluna

---

### Phase 12 — Backfill, Particionamento e Alertas ✅ (entregue 2026-04-04)

**Branch:** `master`

- **Backfill API** ✅
- **Particionamento Hive-style** ✅
- **Alerting webhook** ✅
- **SLA de freshness** ✅

---

### Phase 13 — Novos Conectores ✅ (entregue 2026-04-05)

**Branch:** `master` (PR #18 merged) | **Testes:** 368

- **HTTP/REST source** ✅ — auth Bearer/OAuth2/API key, paginação cursor/page/offset, rate limiting
- **Avro parser** ✅ — fastavro
- BigQuery e Snowflake foram implementados nesta fase e **removidos na Phase 15** (foco S3/Parquet only)

---

### Phase 14 — Observabilidade e Catálogo ✅ parcial (entregue 2026-04-05)

**Branch:** `master` (PR #18 merged) | **Testes:** 368

- **Logging JSON estruturado** ✅ — structlog, stdlib bridge, contextvars por job
- **OpenTelemetry** ✅ — TracerProvider sempre ativo; trace_id em todos os logs; OTLP via env var
- **Schema registry** ✅ — Arrow schema como JSONB em `pipelines.last_schema`; exposto em `GET /api/v1/pipelines/{id}`
- **Data lineage** ✅ — `source_connection_id` + `destination_path` em `job_runs`; exposto em `GET /api/v1/runs` _(entregue na Phase 14 Completion — ver abaixo)_
- **Column metadata** ⏳ — não iniciado (integração com OpenMetadata/Collibra é BAIXA prioridade)

### Phase 14 Completion — Idempotência + Data Lineage ✅ (2026-04-05)

**Branch:** `feature/phase-14-completion` (mergeado) | **Testes:** 374

- **Idempotência fix** ✅ — `destination.promote()` agora antes do watermark update; previne gap de dados silencioso
- **Data lineage mínimo** ✅ — migration 007, `source_connection_id` + `destination_path` em `job_runs`

---

### Phase 15 — Cleanup + Performance ✅ (entregue 2026-04-06)

**Branch:** `master`

- **Remoção de BigQuery e Snowflake** ✅ — Siphon é S3/Parquet only; dependências e testes removidos
- **`max_concurrent_jobs` em Connection** ✅ — limita jobs simultâneos por fonte (migration 008); worker verifica antes de iniciar
- **`priority` em Pipeline** ✅ — enum `low/normal/high` (migration 009); substitui `asyncio.Queue` por `PriorityQueue`
- **Frontend atualizado** ✅ — campo `priority` no PipelineWizard e `max_concurrent_jobs` no ConnectionForm

---

### Phase 16 — Celery + Redis (Escala horizontal) ✅ (entregue 2026-04-07)

**Branch:** `master`

- **Celery + Redis** ✅ — `celery_app.py`, filas `high/normal/low`, broker Redis, backend Redis
- **`tasks.py`** ✅ — `run_pipeline_task` Celery task chamando `run_job()` existente
- **Estado de jobs em PostgreSQL** ✅ — `job_runs` no DB; `GET /jobs/{id}` lê do DB; cancel via `celery revoke`
- **`queue.py` como wrapper** ✅ — `enqueue()` → `apply_async(queue=priority)`
- **`docker-compose.yml`** ✅ — `redis:7-alpine` + serviço `siphon-worker`
- **Graceful drain** ✅ — `task_acks_late=True`, `worker_prefetch_multiplier=1`

---

### Phase 17 — GDPR Compliance ✅ (entregue 2026-04-08)

**Branch:** `master`

- **Soft delete** ✅ — `deleted_at TIMESTAMPTZ` em `connections`, `pipelines`, `schedules`, `users` (migration 010); todos os `GET` filtram `WHERE deleted_at IS NULL`
- **Cascade** ✅ — soft-delete de connection → soft-delete pipelines + remoção de schedules do Celery
- **Purge S3 API** ✅ — `DELETE /api/v1/pipelines/{id}/data` com params `?before=date&partition=val` (admin-only); síncrono (<1000 arquivos) ou Celery task background (≥1000, retorna 202)
- **`gdpr_events`** ✅ — migration 011; registra cada purge com arquivos/bytes deletados
- **Endpoints de auditoria** ✅ — `GET /api/v1/gdpr/events` e `GET /api/v1/gdpr/events/{id}` (admin-only)

---

## Decisões de design que guiam o roadmap

- **Bronze layer only** — Siphon não é um substituto para dbt ou Spark para Silver/Gold; foco em Extract-Load confiável
- **Plugin architecture** — novos sources e destinations não exigem mudanças no core; apenas registrar com `@register("tipo")`
- **Sem dependência de Airflow** — Siphon tem scheduler próprio; integração com Airflow é via API key no sentido Airflow → Siphon, não o contrário
- **Self-hosted first** — deployável em Docker Compose ou Kubernetes; sem vendor lock-in em serviços gerenciados
