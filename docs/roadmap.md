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

### Phase 11 — Confiabilidade e Parsers (estimativa: 3–4 semanas)

**Motivação:** Gaps que causam perda de dados ou impossibilitam uso real.

- **Retry em SQL sources** — exponential backoff com jitter; apenas SFTP tem retry hoje (`sql.py:64` chama `cx.read_sql()` sem nenhuma proteção)
- **Idempotência / deduplicação** — write S3 e update de watermark não são atômicos; crash entre os dois gera duplicação de dados; implementar staging path ou 2-PC leve
- **CSV parser** — interface `Parser` existe mas sem implementação; SFTP é inutilizável em produção sem isso
- **JSON / JSONL parser** — segundo parser mais comum em integrações SFTP/FTP
- **PII masking básico** — campos marcados como `sensitive: true` na config passam por hash/redact antes de gravar em S3; necessário para LGPD/GDPR

---

### Phase 12 — Backfill, Particionamento e Alertas (estimativa: 3 semanas)

**Motivação:** Operação real de pipeline exige reprocessamento e observabilidade mínima.

- **Backfill API** — `POST /api/v1/pipelines/{id}/trigger` com `backfill_from` e `backfill_to`; reprocessa intervalo sem mover watermark global; evita duplicação fora do intervalo
- **Particionamento Hive-style** — `bronze/orders/_date=2024-04-03/part-0.parquet` em vez de `bronze/orders/part-0.parquet`; reduz drasticamente custo de scan em Athena/BigQuery
- **Alerting webhook** — configuração de URL de webhook por pipeline; dispara em `failed`, `schema_changed`, `dq_failed`; payload JSON padronizado compatível com Slack e PagerDuty
- **SLA de freshness** — alerta se pipeline não completou dentro de janela configurada (ex: deve rodar até 06:00)

---

### Phase 13 — Novos Conectores (estimativa: 4–5 semanas)

**Motivação:** SQL + SFTP cobrem <50% dos casos de uso de um time de dados moderno.

- **HTTP/REST source** — extração paginada de APIs (Salesforce, Stripe, Zendesk, custom); suporte a auth Bearer, OAuth2 client_credentials, API key; configuração de rate limiting e cursor de paginação
- **BigQuery destination** — escrita direta via `google-cloud-bigquery`; append e replace modes; sem necessidade de S3 como intermediário para GCP shops
- **Snowflake destination** — escrita via `snowflake-connector-python` com COPY INTO; alternativa ao S3 para stacks Snowflake-first
- **Avro parser** — terceiro formato mais comum em integrações SFTP enterprise (bancos, seguradoras)

---

### Phase 14 — Observabilidade e Catálogo (estimativa: 4 semanas)

**Motivação:** Sem observabilidade real é impossível debugar problemas em produção ou responder "de onde veio esse dado?".

- **Logging JSON estruturado** — substituir `logging.info("texto")` por JSON com `job_id`, `pipeline_id`, `trace_id`, `duration_ms`; compatível com Datadog/Loki/CloudWatch
- **OpenTelemetry** — traces distribuídos cobrindo extract → write → DB update; exportador configurável (OTLP, Jaeger, Zipkin)
- **Schema registry** — armazenar schema Arrow completo (não só SHA-256) por versão; endpoint `GET /api/v1/pipelines/{id}/schema/history`; diff legível entre versões
- **Data lineage** — registrar `source_connection → pipeline → destination_path` por run; base para responder "onde esse dado foi parar?" e "quem usa essa tabela?"
- **Column metadata** — descrição, tipo, nullable, flag PII por coluna; integração básica com OpenMetadata ou Collibra

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
