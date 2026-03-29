# Siphon — Implementation Roadmap

Spec completa em `claude.md`. Implementar fase a fase, na ordem abaixo.
Cada fase termina com testes passando e código funcional antes de avançar.

---

## Fase 1 — Esqueleto do projeto ✅
**Objetivo:** repositório configurado, estrutura de pastas, modelos e ABCs prontos. Zero lógica de negócio ainda.

- [x] Inicializar projeto com `uv init`, configurar `pyproject.toml` (deps, ruff, pytest)
- [x] Criar estrutura de pastas conforme §14 do `claude.md`
- [x] `models.py` — todos os modelos Pydantic: `SQLSourceConfig`, `SFTPSourceConfig`, `S3ParquetDestinationConfig`, `ExtractRequest`, `JobStatus`, `LogsResponse`, `Job` (dataclass interno sem credenciais)
- [x] `plugins/sources/base.py` — ABC `Source` com `extract()` e `extract_batches()`
- [x] `plugins/destinations/base.py` — ABC `Destination` com `write(table, is_first_chunk)`
- [x] `plugins/parsers/base.py` — ABC `Parser` com `parse(data)`
- [x] Registry + autodiscovery (`__init__.py`) para sources, destinations, parsers
- [x] `variables.py` — resolver `@TODAY`, `@MIN_DATE`, `@LAST_MONTH`, `@NEXT_MONTH` com timezone
- [x] Testes unitários: variable resolution, registry register/get, autodiscovery

---

## Fase 2 — API e queue ✅
**Objetivo:** serviço HTTP rodando com queue funcional, sem plugins reais ainda (stubs).

- [x] `queue.py` — asyncio queue, ThreadPoolExecutor, job state dict, drain logic (SIGTERM)
- [x] `worker.py` — loop de execução: source.extract_batches() → destination.write(batch, is_first_chunk)
- [x] `main.py` — FastAPI app com lifespan, rotas: `POST /jobs`, `POST /extract`, `GET /jobs/{id}`, `GET /jobs/{id}/logs`, `GET /health`, `GET /health/live`, `GET /health/ready`
- [x] Middleware de segurança: request size limit (413), API key auth (401), 422 sem log de body
- [x] Testes unitários: queue 429 quando cheia, graceful drain, health ready retorna 503 quando cheia

---

## Fase 3 — SQL Source ✅
**Objetivo:** extração SQL funcionando para MySQL e PostgreSQL. Oracle depois.

- [x] `plugins/sources/sql.py` — `SQLSource` com ConnectorX
- [x] `_inject_timeout()` — injeção automática de connect_timeout
- [x] `_validate_host()` — validação SSRF contra `SIPHON_ALLOWED_HOSTS`
- [x] `mask_uri()` em `models.py` + `__repr__` mascarado em `SQLSourceConfig`
- [x] Roteamento Oracle → `_extract_oracle()` com pandas + oracledb thin mode + chunksize
- [x] Campos de particionamento: `partition_on`, `partition_num`, `partition_range`
- [x] Testes unitários: variable resolution aplicada antes da query, mask_uri, host validation
- [x] Teste de integração (docker-compose): MySQL → Arrow Table

---

## Fase 4 — S3 Destination ✅
**Objetivo:** escrita Parquet no MinIO funcionando, validação de path, TLS configurável.

- [x] `plugins/destinations/s3_parquet.py` — `S3ParquetDestination` com PyArrow S3FileSystem
- [x] `_validate_path()` — traversal check + prefix check contra `SIPHON_ALLOWED_S3_PREFIX`
- [x] `SIPHON_S3_SCHEME` — nunca hardcoded
- [x] Suporte a múltiplos `write()` no mesmo job: `delete_matching` no primeiro, `overwrite_or_ignore` nos seguintes
- [x] Validação `rows_read == rows_written` antes de marcar `success`
- [x] Testes unitários: path validation, scheme config
- [x] Teste de integração: MySQL → MinIO Parquet end-to-end (POST /jobs + polling)

---

## Fase 5 — SFTP Source ✅
**Objetivo:** extração SFTP com as garantias de segurança e resiliência da spec.

- [x] `plugins/sources/sftp.py` — `SFTPSource` com Paramiko
- [x] `_single_connection()` com `RejectPolicy` + known_hosts/host_key config
- [x] File listing com `skip_patterns` (fnmatch)
- [x] `_move_to_processing()` e `_move_to_processed()` — rename atômico no servidor
- [x] `_download_with_retry()` — exponential backoff
- [x] `extract_batches()` com `chunk_size` para streaming de memória
- [x] Tolerância a falha parcial: `fail_fast`, `partial_success`, `failed_files`
- [x] Limite de tamanho de arquivo: `SIPHON_MAX_FILE_SIZE_MB`
- [x] `plugins/parsers/example_parser.py` — stub (bytes → pa.Table com coluna `raw`)
- [x] Testes unitários: skip_patterns, file size limit, partial_success behavior
- [x] Teste de integração: SFTP mock → stub parser → MinIO

---

## Fase 6 — Docker e CI ✅
**Objetivo:** imagem Docker < 500MB, CI completo rodando no GitHub Actions.

- [x] `Dockerfile` — multi-stage build (builder uv + runtime non-root UID 1000, read-only fs)
- [x] Verificar tamanho da imagem: `docker image inspect siphon:latest --format='{{.Size}}'`
- [x] `docker-compose.yml` — siphon + mysql + postgres + minio + sftp (dev local)
- [x] `docker-compose.test.yml` — containers isolados para CI
- [x] `.github/workflows/ci.yml` — ruff, pytest unit, docker build, smoke test `/health/live`, pytest integration, trivy scan
- [x] `.github/workflows/publish.yml` — tag `v*` → multi-arch build → push GHCR

---

## Fase 7 — Hotfixes críticos ✅
**Objetivo:** corrigir bugs identificados em revisão de arquitetura antes de avançar.

- [x] `queue.py` — TTL eviction: jobs terminados há mais de `SIPHON_JOB_TTL_SECONDS` (default 3600) são removidos de `_jobs`. Background task a cada 5 min.
- [x] `plugins/sources/sftp.py` — `_move_back_to_origin()`: em caso de falha de parse após `_move_to_processing()`, mover arquivo de volta à origem antes de adicionar a `failed_files`
- [x] `main.py` — `/extract` retorna 404 a menos que `SIPHON_ENABLE_SYNC_EXTRACT=true`
- [x] Testes unitários: TTL eviction, SFTP move-back, `/extract` guard
- [x] PR mergeado: `hotfix/phase-7` → `main` (129 unit tests passing, 0 ruff violations)

---

## Fase 7.5 — Oracle cursor streaming ✅
**Objetivo:** Oracle usa cursor nativo com fetchmany() em vez de pandas, sem quebrar tipagem.

- [x] `_extract_oracle()` — substituir pandas `read_sql` por `cursor.fetchmany(chunk_size)` nativo oracledb
- [x] `_oracle_output_type_handler()` — mapeamento de tipos Oracle → Python (LOB, NUMBER, DATE)
- [x] `_oracle_rows_to_arrow()` — conversão row-list → pa.Table preservando tipos
- [x] Testes unitários: LOB handler, tipo-fidelidade NUMBER/DATE, streaming em chunks
- [x] PR mergeado: branch `feature/phase-7.5-oracle-cursor-streaming` → `master`

---

## Fase 8 — PostgreSQL + Auth ✅
**Objetivo:** persistência e autenticação JWT prontas. Zero lógica de negócio nova ainda.

- [x] Alembic setup + migrations para todas as 6 tabelas (`users`, `connections`, `pipelines`, `schedules`, `job_runs`, `refresh_tokens`)
- [x] `db.py` — SQLAlchemy async engine, session factory, `get_db` dependency
- [x] `auth/` router — `POST /api/v1/auth/login`, `/refresh`, `/logout`, `GET /api/v1/auth/me`
- [x] `get_current_principal` dependency — dual-auth: API key → JWT → 401
- [x] JWT: access token (15min, memória), refresh token (7d, httpOnly cookie `Path=/api/v1/auth/refresh`)
- [x] Token rotation + reuse detection (revogação de todas as sessões)
- [x] Rate limiting no login (slowapi, 10 req/min por IP)
- [x] `users/` router — CRUD admin-only
- [x] Startup: criar usuário admin se nenhum existir (`SIPHON_ADMIN_EMAIL` + `SIPHON_ADMIN_PASSWORD`)
- [x] `worker.py` — persistir resultado em `job_runs` ao finalizar job
- [x] Testes unitários: login/refresh/logout, token rotation, dual-auth, admin CRUD
- [x] PR mergeado: `feature/phase-8-postgres-auth` → `master` (183 testes passando, 2026-03-28)

---

## Fase 9 — Connections + Pipelines API ✅
**Objetivo:** toda a lógica de negócio da UI exposta via API. Frontend ainda não existe.
**Branch:** `feature/phase-9-connections-pipelines-api` (256 testes passando, 2026-03-29)

### Infraestrutura base
- [x] `crypto.py` — Fernet encrypt/decrypt com `SIPHON_ENCRYPTION_KEY`
- [x] Migration 002 — `dest_connection_id` em `pipelines`, `triggered_by` em `job_runs`
- [x] `metrics.py` — contadores/histogramas Prometheus (`jobs_total`, `job_duration_seconds`, `rows_extracted_total`, `queue_depth`, `schema_changes_total`)

### Routers
- [x] `connections/` router — CRUD, Fernet encryption/decryption, `POST /test`, `GET /types`
- [x] `pipelines/` router — CRUD (409 em nome duplicado, writes admin-only), `GET /:id/runs` paginado, schedule upsert/delete via APScheduler
- [x] `POST /api/v1/pipelines/:id/trigger` — monta Job a partir de connections, cria `job_runs` com status `queued`, enfileira job; worker UPDATa a linha existente (sem duplicata)
- [x] `preview/` router — `POST /api/v1/preview` com `LIMIT 100` via subquery, validação SSRF via `_validate_host()`
- [x] `runs/` router — histórico global paginado newest-first, logs com cursor `?since=N`, `POST /:id/cancel` (admin-only)
- [x] `GET /metrics` — endpoint Prometheus com `queue_depth` atualizado em tempo real
- [x] Todos os routers registrados em `main.py`; scheduler iniciado/parado no lifespan

### Worker (`worker.py`)
- [x] `_compute_schema_hash(schema)` — SHA-256 de `[(name, type)]` em JSON ordenado
- [x] Schema evolution — comparação com `pipeline.last_schema_hash`; `job_runs.schema_changed=True` + log de warning; escrita nunca bloqueada
- [x] `_check_data_quality(dq, rows_read)` — verifica `min_rows_expected` e `max_rows_drop_pct`; erro antes de qualquer escrita
- [x] `_sync_extract_and_write` — bufferiza batches quando DQ ativo; streaming direto caso contrário; sempre seta `job.schema_hash`
- [x] `_persist_job_run` — UPDATE se `job.run_id` set (linha já existe), INSERT caso contrário
- [x] `_update_pipeline_metadata` — atualiza `last_watermark` (modo incremental) e `last_schema_hash` após sucesso
- [x] Instrumentação Prometheus no bloco `finally` de `run_job`

### Watermark (`pipelines/watermark.py`)
- [x] `inject_watermark(query, key, watermark, dialect)` — wrap em CTE `_siphon_base`, adiciona `WHERE key > cast_expr`; compatível com queries que já têm CTE
- [x] `_cast_for_dialect` — mysql → `DATETIME`, postgresql/postgres → `TIMESTAMPTZ`, oracle → `TIMESTAMP WITH TIME ZONE`, mssql → `DATETIMEOFFSET`, outros → `TIMESTAMPTZ`; escapa aspas simples

### Scheduler (`scheduler.py`)
- [x] `start_scheduler()` / `stop_scheduler()` — lifecycle APScheduler com `AsyncIOScheduler` + jobstore PostgreSQL (ou memória quando sem `DATABASE_URL`)
- [x] `sync_schedule(pipeline_id, cron, is_active)` — add/reschedule/remove job no scheduler; no-op se `_scheduler is None`
- [x] `remove_schedule(pipeline_id)` — remove job; no-op se `_scheduler is None`
- [x] `_parse_cron(cron)` — valida e converte string de 5 campos em kwargs APScheduler
- [x] `_uuid_to_lock_key(str)` — `UUID.int & 0x7FFFFFFFFFFFFFFF` para advisory lock PostgreSQL
- [x] `_fire_with_advisory_lock` — adquire `pg_try_advisory_xact_lock`; ignora disparo se lock não obtido (proteção multi-pod)
- [x] `_async_trigger_pipeline` — carrega Pipeline + Connections do DB, constrói Job, cria `job_runs`, enfileira

### Job dataclass (`models.py`)
- [x] Novos campos: `run_id`, `pipeline_id`, `pipeline_dq`, `pipeline_schema_hash`, `schema_hash`

### Testes (55 novos, 256 total)
- [x] `test_watermark.py` — 14 testes: casts por dialeto, escape de aspas, CTE wrapping, estrutura SQL
- [x] `test_worker_phase9.py` — 15 testes: schema hash determinismo, DQ pass/fail, `_sync_extract_and_write`, `_persist_job_run` UPDATE/INSERT, schema change warning
- [x] `test_preview.py` — 7 testes: 404/400 connection, 400 non-SQL, query + rows, `_apply_limit` unit tests
- [x] `test_runs.py` — 8 testes: list 200, logs 404/cursor/evicted, cancel 403/409/202
- [x] `test_scheduler.py` — 8 testes: `_parse_cron` válido/inválido, `_uuid_to_lock_key` determinismo/range, noop sem scheduler

### Pendente (pós-Fase 9)
- [ ] Structured logging em job entries (`{"ts","job_id","pipeline_id","level","msg"}`)
- [ ] Testes de integração: pipeline incremental MySQL → MinIO com watermark, schema change detectado

---

## Fase 10 — Frontend ✅
**Objetivo:** UI completa servida pelo FastAPI.
**Branch:** `feature/phase-10-frontend` (build limpo, 0 erros TS, 2026-03-29)

### Build e configuração
- [x] `frontend/` — scaffold Vite + React 18 + shadcn/ui + Tailwind + react-hook-form + zod
- [x] `package.json` — deps completas: TanStack Query, Radix UI, axios, cronstrue, date-fns, CodeMirror 6, lucide-react
- [x] `vite.config.ts` — proxy `/api → localhost:8000`, alias `@/`
- [x] `tsconfig.json` — strict mode, noUnusedLocals, noUnusedParameters
- [x] `tailwind.config.js` — CSS vars shadcn/ui, tailwindcss-animate

### Fundação
- [x] `src/lib/api.ts` — axios client, access token em memória, refresh mutex interceptor, todos os typed API helpers (auth, connections, pipelines, runs, preview, users)
- [x] `src/lib/queryKeys.ts` — query keys centralizados
- [x] `src/contexts/AuthContext.tsx` — AuthProvider, useAuth, login/logout, bootstrap via `/auth/refresh` no mount
- [x] `src/main.tsx` — entry point (ReactDOM.createRoot, QueryClientProvider, AuthProvider, Toaster)
- [x] `src/App.tsx` — React Router v6 com todas as rotas (connections, pipelines, runs, settings)

### Componentes UI (shadcn/ui manual)
- [x] button, input, label, card, badge (variants: default/secondary/destructive/outline/warning/success/running), select, dialog, alert-dialog, toast/toaster

### Componentes compartilhados
- [x] `PageHeader`, `EmptyState`, `ApiErrorMessage`, `ConfirmDialog`
- [x] `SchemaDriftBadge`, `StatusBadge`
- [x] `CronInput` — live preview via cronstrue
- [x] `ConnectionSelect` — dropdown + botão inline "create new"
- [x] `QueryEditor` — CodeMirror 6 lazy-loaded (SQL syntax highlight)

### Layout
- [x] `AppLayout` — sidebar nav (Dashboard, Connections, Pipelines, Runs, Settings)
- [x] `RequireAuth` — proteção de rotas, redirect para `/login`

### Páginas
- [x] `LoginPage` — form email/senha, redirect pós-login
- [x] `DashboardPage` — cards de stats + recent runs
- [x] `/connections` — lista, `ConnectionForm` (campos dinâmicos por tipo, test-connection inline)
- [x] `/pipelines` — lista, `PipelineWizard` 4 steps (Source → Query+Preview → Dest+DQ → Schedule+Review), `PipelineDetailPage` (config, schedule CRUD, recent runs), `PipelineEditPage`
- [x] `/runs` — histórico global com filtro de status, `RunDetailPage` + `LogViewer` (polling cursor-based, cap 2000 linhas)
- [x] `/settings/users` — CRUD admin-only de usuários
- [x] `/settings/system` — info de runtime + links /docs /redoc

### Backend
- [x] `src/siphon/main.py` — monta `StaticFiles` em `/` servindo `frontend/dist/` (SPA fallback para index.html); fallback 503 amigável se dist não existe; controlado via `SIPHON_FRONTEND_DIR`

### Dockerfile
- [x] Stage 0 `frontend-builder` — node:22-slim, pnpm install, pnpm build
- [x] Stage runtime copia `frontend/dist` → `/app/frontend/dist`

### Pendente (fase 11+)
- [ ] CI: `lint-frontend`, `test-frontend` (Vitest), Playwright E2E smoke
- [ ] `test_integration_ui.py` — fluxo completo via API

---

## Fase 11 — Kubernetes
**Objetivo:** manifests prontos para deploy em qualquer cluster.

- [ ] `k8s/deployment.yaml` — Recreate strategy, probes, resources, securityContext, terminationGracePeriodSeconds, preStop hook
- [ ] `k8s/service.yaml` — ClusterIP porta 8000
- [ ] `k8s/secret.yaml.template` — template sem valores reais
- [ ] Testar deploy num cluster local (kind ou minikube)
- [ ] Verificar graceful drain: `kubectl delete pod siphon-xxx` com job em execução

---

## Fase 12 — SiphonOperator (Airflow)
**Objetivo:** operador Airflow pronto para substituir SparkKubernetesOperator em produção.

- [ ] `airflow/operators/siphon.py` — `SiphonOperator` com polling + log_offset
- [ ] Suporte a `pipeline_id` (referencia pipeline salvo) além de config inline
- [ ] Leitura de connection URI do Airflow Connections
- [ ] `destination_conn_id` — credenciais MinIO do Airflow Connections
- [ ] Leitura de SQL de arquivo ou inline
- [ ] `AirflowException` em falha, XCom com `rows_read` em sucesso
- [ ] `allow_partial: bool` — controla se `partial_success` é aceito ou levanta exception
- [ ] Teste com DAG real num Airflow local (docker-compose)

---

## Fase 13 — Migração (pós-implementação)
**Objetivo:** substituir Spark em produção com segurança, DAG por DAG.

- [ ] Definir ordem de migração (começar pelas menores/menos críticas)
- [ ] Executar Siphon e Spark em paralelo para 1 entidade — comparar output (schema, row count, sample)
- [ ] Validar tipos problemáticos: DECIMAL, TINYINT(1), Oracle NUMBER, DATETIME
- [ ] Documentar rollback procedure
- [ ] Migrar 5 DAGs piloto → monitorar por 1 semana → migrar restante em lotes

---

## Definition of Done global

Antes de considerar v1 completo, todos os itens do §17 do `claude.md` devem estar checados.
