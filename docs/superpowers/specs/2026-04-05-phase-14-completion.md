# Phase 14 Completion — Idempotência e Data Lineage

**Date:** 2026-04-05
**Branch:** `feature/phase-14-completion`

## Goal

Fechar dois gaps que ficaram pendentes da Phase 14:

1. **Idempotência** — corrigir a janela de crash entre watermark update e promote que causa gap de dados
2. **Data Lineage (mínimo)** — registrar `source_connection_id` e `destination_path` em `job_runs` para responder "de onde veio e onde foi parar"

---

## 1. Idempotência — bugfix de ordem em worker.py

### Problema

`worker.py` executa as operações de finalização nesta ordem:

```
1. _persist_job_run()          ← marca job como success no DB
2. _update_pipeline_metadata() ← avança watermark no DB
3. destination.promote()       ← move arquivos de staging → path final
```

Se o pod morrer entre os passos 2 e 3:
- Watermark avançou ✅
- Dados ainda em staging (ou deletados por `cleanup_staging` no próximo start) ❌
- Próxima run extrai do watermark novo → **gap de dados silencioso**

### Fix

Inverter a ordem: promote acontece **antes** das operações de DB.

```
1. destination.promote()       ← dados em S3 final (idempotente no S3)
2. _persist_job_run()          ← status no DB
3. _update_pipeline_metadata() ← watermark avança
```

**Garantias resultantes:**

| Modo | Crash após promote, antes do DB | Comportamento |
|---|---|---|
| `full_refresh` | Re-extrai + `delete_matching` limpa antes de escrever | Idempotente ✅ |
| `incremental` | Re-extrai mesmo intervalo → arquivos duplicados no S3 | At-most-twice ⚠️ |

O cenário at-most-twice para incremental é aceitável: é limitado (não infinito), e o risco oposto (gap de dados) é pior do que dados duplicados.

### Arquivo modificado

- `src/siphon/worker.py` — reordenar bloco `finally` em `_run_job_inner` (linhas ~480-488)

### Teste

```python
async def test_promote_before_watermark_update():
    """promote() deve ser chamado antes de _update_pipeline_metadata."""
    call_order = []
    # mock promote, _persist_job_run, _update_pipeline_metadata
    # assert promote é chamado primeiro
```

---

## 2. Data Lineage — campos em job_runs

### O que muda

`job_runs` ganha dois campos:

| Campo | Tipo SQL | Nullable | Descrição |
|---|---|---|---|
| `source_connection_id` | `UUID FK → connections.id` | YES | Connection usada na extração |
| `destination_path` | `TEXT` | YES | Path S3 resolvido (ex: `bronze/orders/`) |

Nullable porque runs legacy (`POST /jobs`) não têm esses valores.

### Fluxo de dados

```
trigger_pipeline()
  ├── src_conn.id         → Job.source_connection_id  (novo campo)
  └── dest_path (resolvido) → Job.destination_path    (novo campo)
       ↓
_persist_job_run()
  └── escreve source_connection_id + destination_path em job_runs
       ↓
GET /api/v1/runs + GET /api/v1/pipelines/{id}/runs
  └── retorna ambos os campos no response
```

### Mudanças por arquivo

| Arquivo | Mudança |
|---|---|
| `alembic/versions/007_add_lineage_to_job_runs.py` | `ADD COLUMN source_connection_id UUID REFERENCES connections(id)` e `ADD COLUMN destination_path TEXT` |
| `src/siphon/orm.py` | `source_connection_id` e `destination_path` em `JobRun` |
| `src/siphon/models.py` | `Job.source_connection_id` e `Job.destination_path` (ambos `str | None = None`) |
| `src/siphon/pipelines/router.py` | Popula os dois campos ao construir `Job` em `trigger_pipeline` |
| `src/siphon/worker.py` | `_persist_job_run` escreve os campos no UPDATE e INSERT |
| `src/siphon/runs/router.py` | `RunResponse` inclui `source_connection_id` e `destination_path` |

### API

`GET /api/v1/runs` e `GET /api/v1/pipelines/{id}/runs` retornam:

```json
{
  "id": 42,
  "job_id": "...",
  "pipeline_id": "...",
  "source_connection_id": "uuid-da-connection",
  "destination_path": "bronze/orders/",
  "status": "success",
  ...
}
```

Sem endpoint novo. Sem tabela nova.

---

## File Map

| Arquivo | Ação | Mudança |
|---|---|---|
| `alembic/versions/007_add_lineage_to_job_runs.py` | Criar | Migration: 2 colunas em `job_runs` |
| `src/siphon/orm.py` | Modificar | 2 campos em `JobRun` |
| `src/siphon/models.py` | Modificar | 2 campos em `Job` dataclass |
| `src/siphon/pipelines/router.py` | Modificar | Popula campos em `trigger_pipeline` |
| `src/siphon/worker.py` | Modificar | Reordena promote; escreve campos em `_persist_job_run` |
| `src/siphon/runs/router.py` | Modificar | Expõe campos em `RunResponse` |
| `tests/test_worker_atomicity.py` | Criar | Testa ordem promote → DB |
| `tests/test_lineage.py` | Criar | Testa source_connection_id + destination_path em job_runs |

---

## Definition of Done

- [ ] `uv run pytest` passa (368+ testes, zero falhas)
- [ ] `uv run ruff check src/` limpo
- [ ] Crash entre promote e DB commit não causa gap de dados (at-most-twice para incremental)
- [ ] `GET /api/v1/runs` retorna `source_connection_id` e `destination_path` para runs de pipelines
- [ ] Runs legacy (`POST /jobs`) retornam `null` nos dois campos
