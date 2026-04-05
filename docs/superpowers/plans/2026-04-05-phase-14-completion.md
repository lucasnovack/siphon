# Phase 14 Completion — Idempotência e Data Lineage

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir a janela de crash que causa gap de dados (idempotência) e registrar source_connection_id + destination_path em job_runs (data lineage mínimo).

**Architecture:** Dois changes independentes em cima do master (368 testes passando). Idempotência = reordenar 3 linhas em `worker.py`. Data lineage = migration 007 + ORM + modelos + worker + pipeline trigger + runs API.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, Alembic, PyArrow, pytest-asyncio, structlog.

---

## File Map

| Arquivo | Ação | O que muda |
|---|---|---|
| `alembic/versions/007_add_lineage_to_job_runs.py` | Criar | `ADD COLUMN source_connection_id UUID` + `ADD COLUMN destination_path TEXT` em `job_runs` |
| `src/siphon/orm.py` | Modificar | 2 novos campos em `JobRun` |
| `src/siphon/models.py` | Modificar | 2 novos campos em `Job` dataclass |
| `src/siphon/worker.py` | Modificar | Reordenar promote; escrever campos em `_persist_job_run` |
| `src/siphon/pipelines/router.py` | Modificar | Popular `Job.source_connection_id` e `Job.destination_path` em `trigger_pipeline` |
| `src/siphon/runs/router.py` | Modificar | Expor os dois campos em `_run_to_dict` |
| `tests/test_worker_atomicity.py` | Criar | Verifica ordem: promote antes de DB writes |
| `tests/test_lineage.py` | Criar | Verifica captura e exposição dos campos de lineage |

---

## Task 1: Migration 007 + ORM

**Files:**
- Create: `alembic/versions/007_add_lineage_to_job_runs.py`
- Modify: `src/siphon/orm.py`

### Contexto

`job_runs` hoje não registra qual connection foi usada nem para onde os dados foram escritos. A migration adiciona 2 colunas nullable (para compatibilidade com runs legados). O ORM reflete as colunas.

O padrão de migration do projeto: ver `alembic/versions/006_add_schema_registry.py`. O `down_revision` deve ser `"006"`.

- [ ] **Step 1: Criar migration**

Crie o arquivo `alembic/versions/007_add_lineage_to_job_runs.py` com o conteúdo exato:

```python
"""add lineage columns to job_runs

Revision ID: 007
Revises: 006
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_runs",
        sa.Column("source_connection_id", UUID(as_uuid=True), sa.ForeignKey("connections.id"), nullable=True),
    )
    op.add_column(
        "job_runs",
        sa.Column("destination_path", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_runs", "destination_path")
    op.drop_column("job_runs", "source_connection_id")
```

- [ ] **Step 2: Atualizar ORM**

Em `src/siphon/orm.py`, adicione os dois campos à classe `JobRun` após `triggered_by` (linha ~115):

```python
source_connection_id: Mapped[uuid.UUID | None] = mapped_column(
    UUID(as_uuid=True), ForeignKey("connections.id"), nullable=True
)
destination_path: Mapped[str | None] = mapped_column(Text, nullable=True)
```

O import `Text` já existe no arquivo. O import `ForeignKey` também. Nenhum import novo necessário.

- [ ] **Step 3: Verificar que os testes existentes continuam passando**

```bash
uv run pytest tests/test_orm.py -v
```

Expected: todos os testes do arquivo passam (não há testes de job_runs com campos específicos que quebrariam).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/007_add_lineage_to_job_runs.py src/siphon/orm.py
git commit -m "feat(phase-14-completion): migration 007 + ORM lineage fields on job_runs"
```

---

## Task 2: Job dataclass + fix de atomicidade em worker.py

**Files:**
- Modify: `src/siphon/models.py`
- Modify: `src/siphon/worker.py`
- Create: `tests/test_worker_atomicity.py`

### Contexto

O `Job` dataclass em `models.py` precisa de 2 novos campos para carregar os valores de lineage do `trigger_pipeline` até o `_persist_job_run`.

O bug de atomicidade está em `worker.py` no bloco `finally` de `_run_job_inner` (linhas ~480-488). A ordem atual é:
1. `_persist_job_run` (DB)
2. `_update_pipeline_metadata` (watermark no DB)
3. `destination.promote()` (S3)

A ordem correta é:
1. `destination.promote()` (S3)
2. `_persist_job_run` (DB)
3. `_update_pipeline_metadata` (watermark no DB)

Se o pod morrer entre 1 e 2 com a ordem correta: dados em S3 final, watermark não avança → próxima run re-extrai (at-most-twice para incremental; idempotente para full_refresh). Isso é melhor que o cenário atual onde o watermark avança mas os dados nunca chegam ao path final.

- [ ] **Step 1: Escrever o teste de atomicidade (deve falhar)**

Crie o arquivo `tests/test_worker_atomicity.py`:

```python
# tests/test_worker_atomicity.py
"""Verifica que destination.promote() é chamado ANTES das operações de DB."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, call, patch

import pyarrow as pa
import pytest

from siphon.models import Job


_PIPELINE_UUID = "11111111-1111-1111-1111-111111111111"


def _make_batch() -> pa.RecordBatch:
    schema = pa.schema([pa.field("id", pa.int64())])
    return pa.record_batch([pa.array([1, 2, 3], type=pa.int64())], schema=schema)


@pytest.mark.asyncio
async def test_promote_called_before_db_writes():
    """promote() deve ser chamado antes de _persist_job_run e _update_pipeline_metadata."""
    from siphon.worker import run_job

    call_order = []

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([_make_batch()])

    mock_dest = MagicMock()
    mock_dest.write.return_value = 3
    mock_dest.promote.side_effect = lambda: call_order.append("promote")

    mock_pipeline = MagicMock()
    mock_pipeline.last_schema_hash = None
    mock_pipeline.last_schema = None
    mock_pipeline.last_watermark = None

    mock_run = MagicMock()
    mock_run.id = 1

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pipeline

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    async def fake_persist(job, db_factory):
        call_order.append("persist_job_run")

    async def fake_update_metadata(job, db_factory):
        call_order.append("update_pipeline_metadata")

    job = Job(job_id="test-atomicity", pipeline_id=_PIPELINE_UUID)

    with patch("siphon.worker._persist_job_run", side_effect=fake_persist), \
         patch("siphon.worker._update_pipeline_metadata", side_effect=fake_update_metadata):
        with ThreadPoolExecutor(max_workers=1) as executor:
            await run_job(mock_source, mock_dest, job, executor, timeout=30, db_factory=mock_factory)

    assert call_order[0] == "promote", f"promote should be first, got: {call_order}"
    assert "persist_job_run" in call_order
    assert "update_pipeline_metadata" in call_order
    promote_idx = call_order.index("promote")
    persist_idx = call_order.index("persist_job_run")
    assert promote_idx < persist_idx, f"promote ({promote_idx}) must come before persist ({persist_idx})"
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

```bash
uv run pytest tests/test_worker_atomicity.py::test_promote_called_before_db_writes -v
```

Expected: FAIL — `promote should be first, got: ['persist_job_run', 'update_pipeline_metadata', 'promote']`

- [ ] **Step 3: Adicionar campos ao Job dataclass**

Em `src/siphon/models.py`, adicione os dois campos ao `Job` dataclass após `_actual_schema` (linha ~194):

```python
source_connection_id: str | None = None   # UUID string; populated by trigger_pipeline
destination_path: str | None = None        # resolved S3/BQ/Snowflake path
```

- [ ] **Step 4: Corrigir a ordem no worker.py**

Em `src/siphon/worker.py`, substitua o bloco `finally` (linhas ~480-491):

**Antes:**
```python
        if db_factory is not None:
            await _persist_job_run(job, db_factory)
            await _update_pipeline_metadata(job, db_factory)
        # Promote staging to final path after DB commit (idempotent writes)
        if job.status in ("success", "partial_success") and hasattr(destination, "promote"):
            try:
                await loop.run_in_executor(executor, destination.promote)
            except Exception as exc:
                logger.error("staging_promote_failed", error=str(exc))
```

**Depois:**
```python
        # Promote staging BEFORE DB writes so that if the pod dies after promote
        # but before the watermark update, the next run re-extracts (at-most-twice
        # for incremental; idempotent for full_refresh). The previous order
        # (promote after DB) risked advancing the watermark while data sat in
        # staging and got cleaned up on restart — a silent data gap.
        if job.status in ("success", "partial_success") and hasattr(destination, "promote"):
            try:
                await loop.run_in_executor(executor, destination.promote)
            except Exception as exc:
                logger.error("staging_promote_failed", error=str(exc))
        if db_factory is not None:
            await _persist_job_run(job, db_factory)
            await _update_pipeline_metadata(job, db_factory)
```

- [ ] **Step 5: Rodar o teste para confirmar que passa**

```bash
uv run pytest tests/test_worker_atomicity.py -v
```

Expected: PASS

- [ ] **Step 6: Rodar suite completa para confirmar nenhuma regressão**

```bash
uv run pytest --tb=short -q
```

Expected: 368+ passed

- [ ] **Step 7: Commit**

```bash
git add src/siphon/models.py src/siphon/worker.py tests/test_worker_atomicity.py
git commit -m "fix(phase-14-completion): promote before DB writes; add lineage fields to Job"
```

---

## Task 3: Capturar lineage em trigger_pipeline e persistir em job_runs

**Files:**
- Modify: `src/siphon/pipelines/router.py`
- Modify: `src/siphon/worker.py`
- Create: `tests/test_lineage.py`

### Contexto

`trigger_pipeline` em `pipelines/router.py` já tem `src_conn.id` (UUID da connection de origem) e `dest_path` (string do path S3 resolvido) em mãos. Precisa populá-los no `Job`.

`_persist_job_run` em `worker.py` precisa escrever esses valores em `job_runs` — tanto no UPDATE (run existente) quanto no INSERT (fallback legacy).

- [ ] **Step 1: Escrever os testes de lineage (devem falhar)**

Crie o arquivo `tests/test_lineage.py`:

```python
# tests/test_lineage.py
"""Verifica que source_connection_id e destination_path são capturados e persistidos."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pyarrow as pa
import pytest

from siphon.models import Job


_PIPELINE_UUID = "11111111-1111-1111-1111-111111111111"
_CONN_UUID = "22222222-2222-2222-2222-222222222222"


def _make_batch() -> pa.RecordBatch:
    schema = pa.schema([pa.field("id", pa.int64())])
    return pa.record_batch([pa.array([1, 2, 3], type=pa.int64())], schema=schema)


@pytest.mark.asyncio
async def test_lineage_fields_persisted_to_job_run():
    """source_connection_id e destination_path são escritos em job_runs."""
    from siphon.worker import run_job

    mock_source = MagicMock()
    mock_source.extract_batches.return_value = iter([_make_batch()])

    mock_dest = MagicMock()
    mock_dest.write.return_value = 3

    persisted_run = MagicMock()
    persisted_run.id = None  # INSERT path (no run_id)

    persist_calls = []

    mock_pipeline = MagicMock()
    mock_pipeline.last_schema_hash = None
    mock_pipeline.last_schema = None
    mock_pipeline.last_watermark = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_pipeline

    # Capture the JobRun object that gets added to the session
    added_objects = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock(side_effect=lambda obj: added_objects.append(obj))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_factory = MagicMock(return_value=mock_session)

    job = Job(
        job_id="test-lineage",
        pipeline_id=_PIPELINE_UUID,
        source_connection_id=_CONN_UUID,
        destination_path="bronze/orders/",
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        await run_job(mock_source, mock_dest, job, executor, timeout=30, db_factory=mock_factory)

    assert job.status == "success"
    # Find the JobRun object that was added (INSERT path since no run_id)
    from siphon.orm import JobRun
    job_run_objects = [o for o in added_objects if isinstance(o, JobRun)]
    assert len(job_run_objects) == 1
    run_obj = job_run_objects[0]
    import uuid
    assert run_obj.source_connection_id == uuid.UUID(_CONN_UUID)
    assert run_obj.destination_path == "bronze/orders/"


def test_job_dataclass_has_lineage_fields():
    """Job dataclass tem os campos de lineage com default None."""
    job = Job(job_id="test")
    assert job.source_connection_id is None
    assert job.destination_path is None

    job2 = Job(
        job_id="test2",
        source_connection_id=_CONN_UUID,
        destination_path="bronze/table/",
    )
    assert job2.source_connection_id == _CONN_UUID
    assert job2.destination_path == "bronze/table/"
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

```bash
uv run pytest tests/test_lineage.py -v
```

Expected: FAIL — `JobRun` não tem os campos ainda no INSERT.

- [ ] **Step 3: Atualizar _persist_job_run no worker.py**

Em `src/siphon/worker.py`, na função `_persist_job_run`:

**No bloco UPDATE** (após `run.started_at = job.started_at`, antes de `await session.commit()`):
```python
                    run.source_connection_id = (
                        uuid.UUID(job.source_connection_id)
                        if job.source_connection_id else None
                    )
                    run.destination_path = job.destination_path
```

O import `import uuid` já existe na função (linha ~337). Adicione o mesmo no escopo local de `_persist_job_run` caso não exista — verificar que `import uuid` está no topo do bloco `try`.

**No bloco INSERT** (no construtor `JobRun(...)`), adicione os campos:
```python
            run = JobRun(
                job_id=job.job_id,
                status=job.status,
                rows_read=job.rows_read,
                rows_written=job.rows_written,
                duration_ms=duration_ms,
                error=job.error,
                schema_changed=schema_changed,
                started_at=job.started_at,
                finished_at=job.finished_at,
                source_connection_id=(
                    uuid.UUID(job.source_connection_id)
                    if job.source_connection_id else None
                ),
                destination_path=job.destination_path,
                created_at=datetime.now(tz=UTC),
            )
```

Certifique-se de que `import uuid` está no topo do bloco `try` em `_persist_job_run`. Se não estiver, adicione-o.

- [ ] **Step 4: Popular os campos em trigger_pipeline**

Em `src/siphon/pipelines/router.py`, no construtor `Job(...)` dentro de `trigger_pipeline` (por volta da linha 445), adicione:

```python
    job = Job(
        job_id=str(uuid.uuid4()),
        pipeline_id=str(pipeline_id),
        source_connection_id=str(src_conn.id),   # ← novo
        destination_path=dest_path,               # ← novo
        pipeline_schema_hash=p.last_schema_hash,
        pipeline_pii=p.pii_columns or None,
        pipeline_expected_schema=p.expected_schema or None,
        pipeline_dq={
            "min_rows_expected": p.min_rows_expected,
            "max_rows_drop_pct": p.max_rows_drop_pct,
            "prev_rows": None,
        } if has_dq else None,
        is_backfill=bool(body.date_from and body.date_to),
        pipeline_alert=(
            {"webhook_url": p.webhook_url, "alert_on": p.alert_on or ["failed"]}
            if p.webhook_url else None
        ),
    )
```

- [ ] **Step 5: Rodar os testes de lineage**

```bash
uv run pytest tests/test_lineage.py -v
```

Expected: PASS

- [ ] **Step 6: Rodar suite completa**

```bash
uv run pytest --tb=short -q
```

Expected: 370+ passed

- [ ] **Step 7: Commit**

```bash
git add src/siphon/worker.py src/siphon/pipelines/router.py tests/test_lineage.py
git commit -m "feat(phase-14-completion): capture + persist source_connection_id and destination_path"
```

---

## Task 4: Expor lineage na runs API

**Files:**
- Modify: `src/siphon/runs/router.py`

### Contexto

`_run_to_dict` em `runs/router.py` serializa `JobRun` para dict. Adicionar os dois novos campos faz com que apareçam em todos os endpoints que usam essa função: `GET /api/v1/runs` e `GET /api/v1/runs/{id}`.

Os runs de pipelines (`trigger_pipeline`) terão os valores preenchidos. Runs legados (`POST /jobs`) retornarão `null` nos dois campos.

- [ ] **Step 1: Escrever teste de API de lineage (deve falhar)**

Adicione ao arquivo `tests/test_lineage.py`:

```python
def test_run_to_dict_includes_lineage_fields():
    """_run_to_dict inclui source_connection_id e destination_path."""
    import uuid
    from siphon.runs.router import _run_to_dict
    from siphon.orm import JobRun
    from datetime import datetime, UTC

    conn_id = uuid.uuid4()
    run = JobRun(
        job_id="test-run-dict",
        status="success",
        source_connection_id=conn_id,
        destination_path="bronze/orders/",
        triggered_by="manual",
        schema_changed=False,
        created_at=datetime.now(tz=UTC),
    )
    result = _run_to_dict(run)
    assert result["source_connection_id"] == str(conn_id)
    assert result["destination_path"] == "bronze/orders/"


def test_run_to_dict_null_lineage_for_legacy_runs():
    """Runs legados (POST /jobs) retornam null nos campos de lineage."""
    from siphon.runs.router import _run_to_dict
    from siphon.orm import JobRun
    from datetime import datetime, UTC

    run = JobRun(
        job_id="legacy-run",
        status="success",
        triggered_by="api",
        schema_changed=False,
        created_at=datetime.now(tz=UTC),
    )
    result = _run_to_dict(run)
    assert result["source_connection_id"] is None
    assert result["destination_path"] is None
```

- [ ] **Step 2: Rodar para confirmar falha**

```bash
uv run pytest tests/test_lineage.py::test_run_to_dict_includes_lineage_fields tests/test_lineage.py::test_run_to_dict_null_lineage_for_legacy_runs -v
```

Expected: FAIL — KeyError `source_connection_id`

- [ ] **Step 3: Atualizar _run_to_dict**

Em `src/siphon/runs/router.py`, substitua a função `_run_to_dict`:

```python
def _run_to_dict(r: JobRun) -> dict:
    return {
        "id": r.job_id,
        "job_id": r.job_id,
        "pipeline_id": str(r.pipeline_id) if r.pipeline_id else None,
        "source_connection_id": str(r.source_connection_id) if r.source_connection_id else None,
        "destination_path": r.destination_path,
        "status": r.status,
        "triggered_by": r.triggered_by,
        "rows_read": r.rows_read,
        "rows_written": r.rows_written,
        "duration_ms": r.duration_ms,
        "schema_changed": r.schema_changed,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat(),
    }
```

- [ ] **Step 4: Rodar todos os testes de lineage**

```bash
uv run pytest tests/test_lineage.py -v
```

Expected: todos passam

- [ ] **Step 5: Rodar suite completa**

```bash
uv run pytest --tb=short -q
```

Expected: 372+ passed, zero failures

- [ ] **Step 6: Ruff check**

```bash
uv run ruff check src/
```

Expected: sem violations

- [ ] **Step 7: Commit**

```bash
git add src/siphon/runs/router.py tests/test_lineage.py
git commit -m "feat(phase-14-completion): expose source_connection_id and destination_path in runs API"
```
