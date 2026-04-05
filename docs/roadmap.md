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

## Próximas fases

As lacunas abaixo foram levantadas em análise de engenharia de dados (2026-04-04). O Siphon hoje é um **EL tool** (Extract-Load) bem estruturado, mas sem as garantias e conectores necessários para uso em produção em times de dados reais.

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
- **BigQuery destination** ✅ — append e replace modes, service account JSON
- **Snowflake destination** ✅ — append e replace modes, snowflake-connector-python
- **Avro parser** ✅ — fastavro

---

### Phase 14 — Observabilidade e Catálogo ✅ parcial (entregue 2026-04-05)

**Branch:** `master` (PR #18 merged) | **Testes:** 368

- **Logging JSON estruturado** ✅ — structlog, stdlib bridge, contextvars por job
- **OpenTelemetry** ✅ — TracerProvider sempre ativo; trace_id em todos os logs; OTLP via env var
- **Schema registry** ✅ — Arrow schema como JSONB em `pipelines.last_schema`; exposto em `GET /api/v1/pipelines/{id}`
- **Data lineage** ✅ — `source_connection_id` + `destination_path` em `job_runs`; exposto em `GET /api/v1/runs` _(entregue na Phase 14 Completion — ver abaixo)_
- **Column metadata** ⏳ — não iniciado (integração com OpenMetadata/Collibra é BAIXA prioridade)

### Phase 14 Completion — Idempotência + Data Lineage ✅ (2026-04-05)

**Branch:** `feature/phase-14-completion` (PR aberto) | **Testes:** 374

- **Idempotência fix** ✅ — `destination.promote()` agora antes do watermark update; previne gap de dados silencioso
- **Data lineage mínimo** ✅ — migration 007, `source_connection_id` + `destination_path` em `job_runs`
- **⚠️ Follow-up pendente:** falha em `promote()` não seta `job.status = "failed"` → watermark ainda avança; rastrear como issue

---

### Phase 15 — Transformações (estimativa: 4 semanas)

**Motivação:** Hoje o Siphon é EL. Times de dados precisam de pelo menos transformações básicas sem depender de dbt para tudo.

- **Column transformations no pipeline** — rename, cast, drop, filter (além da watermark), calculated fields simples
- **Configuração via YAML/JSON** no pipeline, executada via PyArrow compute antes do write
- **Flattening de JSON** — colunas com tipo JSON/struct podem ser expandidas em colunas flat
- **Exemplo:**
  ```json
  "transformations": [
    { "type": "rename", "from": "cust_id", "to": "customer_id" },
    { "type": "drop", "columns": ["internal_debug_flag"] },
    { "type": "cast", "column": "created_at", "to": "timestamp" },
    { "type": "mask", "column": "email", "method": "sha256" }
  ]
  ```

---

### Phase 16 — Escalabilidade (estimativa: 6 semanas)

**Motivação:** ThreadPoolExecutor in-memory não escala horizontalmente; limite prático de ~10 jobs paralelos.

- **Migração para Celery + Redis** — workers distribuídos; multiple pods sem coordenação manual; retry automático via Celery
- **Connection pooling** — pool de conexões SQL reutilizável entre jobs do mesmo source; reduz latência de cold start
- **Job prioritization** — filas de prioridade (high/normal/low) por pipeline; jobs pequenos não ficam atrás de jobs grandes
- **Streaming de arquivos SFTP** — substituir download completo em memória por streaming chunk-by-chunk; resolve OOM em arquivos grandes
- **Dynamic worker scaling** — escalar número de workers com base no tamanho da queue

---

## Decisões de design que guiam o roadmap

- **Bronze layer only** — Siphon não é um substituto para dbt ou Spark para Silver/Gold; foco em Extract-Load confiável
- **Plugin architecture** — novos sources e destinations não exigem mudanças no core; apenas registrar com `@register("tipo")`
- **Sem dependência de Airflow** — Siphon tem scheduler próprio; integração com Airflow é via API key no sentido Airflow → Siphon, não o contrário
- **Self-hosted first** — deployável em Docker Compose ou Kubernetes; sem vendor lock-in em serviços gerenciados
