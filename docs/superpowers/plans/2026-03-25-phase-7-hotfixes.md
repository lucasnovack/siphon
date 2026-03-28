# Phase 7 — Hotfixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three critical bugs before implementing the UI phases: `_jobs` dict OOM, SFTP stranded files in processing folder, and `POST /extract` always enabled in production.

**Architecture:** Three independent, surgical fixes — each touches exactly one file in production code and one test file. No new abstractions. No refactoring beyond the fix itself.

**Tech Stack:** Python 3.12, pytest-asyncio, unittest.mock — same stack as the rest of the test suite.

---

## File Map

| File | Change |
|---|---|
| `src/siphon/queue.py` | Add `job_ttl` param, `_evict_expired()` method, `_evict_loop()` coroutine, start loop in `start()` |
| `src/siphon/plugins/sources/sftp.py` | Add `_origin_map` tracking in `_move_to_processing()`, add `_move_back_to_origin()` method, call it in `extract_batches()` on error |
| `src/siphon/main.py` | Add `ENABLE_SYNC_EXTRACT` flag, guard `POST /extract` with 404 when disabled |
| `tests/test_queue.py` | Add two TTL eviction tests |
| `tests/test_sftp_source.py` | Add one stranded-file regression test |
| `tests/test_main.py` | Update two `/extract` tests to set env var; add one 404 test |

---

## Task 1: `_jobs` TTL eviction — prevent OOM

The `_jobs` dict accumulates completed jobs forever. A long-running deployment will OOM.

**Files:**
- Modify: `src/siphon/queue.py`
- Test: `tests/test_queue.py`

- [ ] **Step 1.1: Write the failing tests**

Add to the bottom of `tests/test_queue.py`:

```python
async def test_evict_expired_removes_old_completed_jobs():
    """Completed jobs older than TTL are removed from _jobs."""
    from datetime import UTC, datetime, timedelta

    q = JobQueue(max_workers=1, max_queue=0, job_timeout=5, job_ttl=60)
    q.start()

    # Manually plant a completed job with a finished_at in the past
    old_job = Job(job_id="j-old")
    old_job.status = "success"
    old_job.finished_at = datetime.now(tz=UTC) - timedelta(seconds=120)
    q._jobs["j-old"] = old_job

    q._evict_expired()

    assert q.get_job("j-old") is None


async def test_evict_expired_keeps_running_jobs():
    """Running jobs are never evicted regardless of age."""
    from datetime import UTC, datetime, timedelta

    q = JobQueue(max_workers=1, max_queue=0, job_timeout=5, job_ttl=60)
    q.start()

    running_job = Job(job_id="j-running")
    running_job.status = "running"
    running_job.finished_at = None
    q._jobs["j-running"] = running_job

    q._evict_expired()

    assert q.get_job("j-running") is running_job
```

- [ ] **Step 1.2: Run tests to confirm they fail**

```bash
cd /home/lucasnvk/projects/siphon
uv run pytest tests/test_queue.py::test_evict_expired_removes_old_completed_jobs tests/test_queue.py::test_evict_expired_keeps_running_jobs -v
```

Expected: `FAILED` — `AttributeError: 'JobQueue' object has no attribute '_evict_expired'`

- [ ] **Step 1.3: Implement the fix in `src/siphon/queue.py`**

Add `from datetime import UTC, datetime` to the imports block at the top:

```python
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
```

Update `__init__` to accept `job_ttl`:

```python
def __init__(
    self,
    max_workers: int = int(os.getenv("SIPHON_MAX_WORKERS", "10")),
    max_queue: int = int(os.getenv("SIPHON_MAX_QUEUE", "50")),
    job_timeout: int = int(os.getenv("SIPHON_JOB_TIMEOUT", "3600")),
    job_ttl: int = int(os.getenv("SIPHON_JOB_TTL_SECONDS", "3600")),
) -> None:
    self._max_workers = max_workers
    self._max_queue = max_queue
    self._job_timeout = job_timeout
    self._job_ttl = job_ttl
    self._executor: ThreadPoolExecutor | None = None
    self._jobs: dict[str, Job] = {}
    self._active: int = 0
    self._queued: int = 0
    self._total: int = 0
    self._draining: bool = False
```

Update `start()` to launch the eviction loop:

```python
def start(self) -> None:
    """Start the thread pool. Call once at service startup."""
    self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
    asyncio.ensure_future(self._evict_loop())
    logger.info(
        "JobQueue started: max_workers=%d, max_queue=%d, job_ttl=%ds",
        self._max_workers,
        self._max_queue,
        self._job_ttl,
    )
```

Add these two methods anywhere after `get_job()`:

```python
def _evict_expired(self) -> None:
    """Remove terminal jobs whose finished_at is older than job_ttl. Called by _evict_loop."""
    now = datetime.now(tz=UTC)
    terminal = ("success", "failed", "partial_success")
    to_remove = [
        jid
        for jid, job in list(self._jobs.items())
        if job.status in terminal
        and job.finished_at is not None
        and (now - job.finished_at).total_seconds() > self._job_ttl
    ]
    for jid in to_remove:
        del self._jobs[jid]
    if to_remove:
        logger.debug("Evicted %d expired job(s) from memory", len(to_remove))

async def _evict_loop(self) -> None:
    """Background coroutine: evict expired jobs every 5 minutes until draining."""
    while not self._draining:
        await asyncio.sleep(300)
        self._evict_expired()
```

- [ ] **Step 1.4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_queue.py::test_evict_expired_removes_old_completed_jobs tests/test_queue.py::test_evict_expired_keeps_running_jobs -v
```

Expected: both `PASSED`

- [ ] **Step 1.5: Run the full queue test suite to confirm no regressions**

```bash
uv run pytest tests/test_queue.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 1.6: Commit**

```bash
git add src/siphon/queue.py tests/test_queue.py
git commit -m "fix: evict completed jobs from _jobs dict after TTL to prevent OOM

Adds _evict_expired() and a background _evict_loop() coroutine that
removes terminal jobs (success/failed/partial_success) older than
SIPHON_JOB_TTL_SECONDS (default: 3600) every 5 minutes."
```

---

## Task 2: SFTP stranded files — move failed files back to origin

When `processing_folder` is set and a file fails to parse, the file is stuck in `/processing/` forever — it will never be retried and never show up in `failed_files` on the server after recovery.

**Files:**
- Modify: `src/siphon/plugins/sources/sftp.py`
- Test: `tests/test_sftp_source.py`

- [ ] **Step 2.1: Write the failing test**

Add the following class to `tests/test_sftp_source.py` after `TestMoveHelpers`:

```python
class TestMoveBackToOrigin:
    def test_failed_file_moved_back_when_processing_folder_set(self):
        """When processing_folder is set and a file fails, it must be moved back to origin."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [
            _make_sftp_entry("good.bin"),
            _make_sftp_entry("bad.bin"),
        ]

        good_fh = MagicMock()
        good_fh.__enter__ = MagicMock(return_value=good_fh)
        good_fh.__exit__ = MagicMock(return_value=False)
        good_fh.read.return_value = b"ok"

        call_count = {"n": 0}

        def open_side_effect(path, mode):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return good_fh
            raise IOError("parse failure")

        sftp.open.side_effect = open_side_effect

        src = _make_source(
            fail_fast=False,
            processing_folder="/processing",
        )
        _mock_sftp_source(src, sftp)

        list(src.extract_batches())

        # bad.bin was in /processing/bad.bin after the initial move.
        # On failure it must be renamed back to /data/bad.bin.
        rename_calls = [str(c) for c in sftp.rename.call_args_list]
        move_back = any(
            "processing/bad.bin" in c and "/data/bad.bin" in c
            for c in rename_calls
        )
        assert move_back, (
            f"Expected rename from /processing/bad.bin to /data/bad.bin. "
            f"Actual rename calls: {rename_calls}"
        )

        # bad.bin must still appear in failed_files
        assert any("bad.bin" in f for f in src.failed_files)

    def test_no_move_back_when_no_processing_folder(self):
        """Without processing_folder, no rename-back should happen on failure."""
        sftp = MagicMock()
        sftp.listdir_attr.return_value = [_make_sftp_entry("bad.bin")]
        sftp.open.side_effect = IOError("boom")

        src = _make_source(fail_fast=False)  # no processing_folder
        _mock_sftp_source(src, sftp)

        list(src.extract_batches())

        # rename should never have been called (no processing_folder)
        sftp.rename.assert_not_called()
        assert any("bad.bin" in f for f in src.failed_files)
```

- [ ] **Step 2.2: Run the test to confirm it fails**

```bash
uv run pytest tests/test_sftp_source.py::TestMoveBackToOrigin -v
```

Expected: `FAILED` — rename-back call is not happening, assertion fails.

- [ ] **Step 2.3: Implement the fix in `src/siphon/plugins/sources/sftp.py`**

**1.** Update `_move_to_processing` to populate `_origin_map`:

Replace the existing `_move_to_processing` method with:

```python
def _move_to_processing(self, sftp, files: list[str]) -> list[str]:
    self._origin_map: dict[str, str] = {}
    new_paths = []
    for path in files:
        filename = os.path.basename(path)
        new_path = f"{self.processing_folder.rstrip('/')}/{filename}"
        sftp.rename(path, new_path)
        new_paths.append(new_path)
        self._origin_map[new_path] = path
    return new_paths
```

**2.** Add `_move_back_to_origin` method after `_move_to_processed`:

```python
def _move_back_to_origin(self, sftp, path: str) -> None:
    """Move a failed file from processing_folder back to its origin path."""
    origin = self._origin_map.get(path, path)
    try:
        sftp.rename(path, origin)
        logger.info("Moved failed file back to origin: %s → %s", path, origin)
    except Exception as exc:
        logger.error(
            "Could not move %s back to origin %s: %s — file may need manual recovery",
            path,
            origin,
            exc,
        )
```

**3.** Update the exception handler in `extract_batches` to call `_move_back_to_origin` when `processing_folder` is set:

Replace the `except` block inside the `for f in chunk` loop:

```python
                    except Exception as exc:
                        if self.fail_fast:
                            raise
                        logger.warning("Failed to process %s: %s", f, exc)
                        if self.processing_folder:
                            self._move_back_to_origin(sftp, f)
                        self.failed_files.append(f)
```

The full updated `extract_batches` method for reference:

```python
def extract_batches(self, chunk_size: int = 100) -> Iterator[pa.Table]:
    self.failed_files = []
    with self._single_connection() as sftp:
        files = self._list_and_filter(sftp)
        logger.info("Found %d files to process across %d paths", len(files), len(self.paths))
        if self.processing_folder:
            files = self._move_to_processing(sftp, files)
        for chunk in _chunked(files, chunk_size):
            tables = []
            for f in chunk:
                try:
                    data = self._download_with_retry(sftp, f)
                    table = self._parser.parse(data)
                    tables.append(table)
                    if self.processed_folder:
                        self._move_to_processed(sftp, f)
                    logger.info("Processed %s (%d rows)", f, table.num_rows)
                except Exception as exc:
                    if self.fail_fast:
                        raise
                    logger.warning("Failed to process %s: %s", f, exc)
                    if self.processing_folder:
                        self._move_back_to_origin(sftp, f)
                    self.failed_files.append(f)
            if tables:
                yield pa.concat_tables(tables)
```

- [ ] **Step 2.4: Run the new tests to confirm they pass**

```bash
uv run pytest tests/test_sftp_source.py::TestMoveBackToOrigin -v
```

Expected: both tests `PASSED`

- [ ] **Step 2.5: Run the full SFTP test suite to confirm no regressions**

```bash
uv run pytest tests/test_sftp_source.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 2.6: Commit**

```bash
git add src/siphon/plugins/sources/sftp.py tests/test_sftp_source.py
git commit -m "fix: move SFTP files back to origin on parse failure to prevent stranding

When processing_folder is set and a file fails to download or parse,
_move_to_processing() now tracks origin paths in _origin_map.
_move_back_to_origin() uses that map to rename the file back,
so failed files are retryable on the next run instead of stuck forever."
```

---

## Task 3: `POST /extract` production guard

`POST /extract` is documented as "local dev only" but is always live. An Airflow operator that hits this endpoint accidentally holds an HTTP connection open for up to one hour.

**Files:**
- Modify: `src/siphon/main.py`
- Test: `tests/test_main.py`

- [ ] **Step 3.1: Update existing `/extract` tests and add a guard test**

In `tests/test_main.py`, update the two existing `/extract` tests so they set the env var that enables the endpoint. Then add one new test that verifies the default 404.

Replace the two existing `/extract` tests:

```python
# ── POST /extract ─────────────────────────────────────────────────────────────


def test_post_extract_returns_200_with_status(client, monkeypatch):
    monkeypatch.setenv("SIPHON_ENABLE_SYNC_EXTRACT", "true")
    main_module.ENABLE_SYNC_EXTRACT = True
    try:
        response = client.post("/extract", json=VALID_REQUEST)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("success", "failed", "running", "queued")
        assert "job_id" in body
    finally:
        main_module.ENABLE_SYNC_EXTRACT = False


def test_post_extract_429_when_queue_full(client, monkeypatch):
    monkeypatch.setenv("SIPHON_ENABLE_SYNC_EXTRACT", "true")
    main_module.ENABLE_SYNC_EXTRACT = True
    try:
        q = main_module.queue
        q._active = q._max_workers
        q._queued = q._max_queue
        response = client.post("/extract", json=VALID_REQUEST)
        assert response.status_code == 429
    finally:
        main_module.ENABLE_SYNC_EXTRACT = False


def test_post_extract_disabled_by_default(client):
    """POST /extract returns 404 unless SIPHON_ENABLE_SYNC_EXTRACT=true."""
    main_module.ENABLE_SYNC_EXTRACT = False
    response = client.post("/extract", json=VALID_REQUEST)
    assert response.status_code == 404
```

- [ ] **Step 3.2: Run the tests to confirm they fail**

```bash
uv run pytest tests/test_main.py::test_post_extract_returns_200_with_status tests/test_main.py::test_post_extract_429_when_queue_full tests/test_main.py::test_post_extract_disabled_by_default -v
```

Expected: `test_post_extract_disabled_by_default` FAILS (returns 200, not 404). The other two may pass or fail depending on module state.

- [ ] **Step 3.3: Implement the guard in `src/siphon/main.py`**

Add `ENABLE_SYNC_EXTRACT` as a module-level variable after the existing `API_KEY` and `DRAIN_TIMEOUT` declarations (around line 21):

```python
API_KEY: str | None = os.getenv("SIPHON_API_KEY")
DRAIN_TIMEOUT = int(os.getenv("SIPHON_DRAIN_TIMEOUT", "3600"))
ENABLE_SYNC_EXTRACT: bool = os.getenv("SIPHON_ENABLE_SYNC_EXTRACT", "false").lower() == "true"
```

Update the `extract_sync` route to check the flag at the top of the function:

```python
@app.post("/extract")
async def extract_sync(req: ExtractRequest) -> dict:
    """Synchronous extraction — blocks until job completes. For local dev and debug only.

    Disabled by default. Set SIPHON_ENABLE_SYNC_EXTRACT=true to enable.
    Do NOT use from Airflow in production — HTTP connections open for minutes are fragile.
    Use POST /jobs + polling instead.
    """
    if not ENABLE_SYNC_EXTRACT:
        raise HTTPException(status_code=404, detail="Not Found")

    import asyncio

    job, source, destination = _make_job_and_plugins(req)
    await queue.submit(job, source, destination)

    # Poll until terminal state
    while job.status in ("queued", "running"):
        await asyncio.sleep(0.05)

    duration_ms = None
    if job.started_at and job.finished_at:
        duration_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

    return {
        "job_id": job.job_id,
        "status": job.status,
        "rows_read": job.rows_read,
        "rows_written": job.rows_written,
        "duration_ms": duration_ms,
        "error": job.error,
        "logs": job.logs,
    }
```

- [ ] **Step 3.4: Run the tests to confirm they pass**

```bash
uv run pytest tests/test_main.py::test_post_extract_returns_200_with_status tests/test_main.py::test_post_extract_429_when_queue_full tests/test_main.py::test_post_extract_disabled_by_default -v
```

Expected: all three `PASSED`

- [ ] **Step 3.5: Run the full main test suite to confirm no regressions**

```bash
uv run pytest tests/test_main.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 3.6: Commit**

```bash
git add src/siphon/main.py tests/test_main.py
git commit -m "fix: disable POST /extract by default in production

Returns 404 unless SIPHON_ENABLE_SYNC_EXTRACT=true.
Prevents accidental use from Airflow operators that would hold HTTP
connections open for minutes, causing timeout failures."
```

---

## Task 4: Final verification and push

- [ ] **Step 4.1: Run the complete test suite**

```bash
uv run pytest --ignore=tests/test_integration_sql.py \
              --ignore=tests/test_integration_s3.py \
              --ignore=tests/test_integration_sftp.py \
              -v
```

Expected: all unit tests `PASSED`. Note the count — it should have increased by 5 new tests (2 eviction + 2 SFTP move-back + 1 extract guard) compared to the baseline.

- [ ] **Step 4.2: Run ruff**

```bash
uv run ruff check .
uv run ruff format --check .
```

Expected: no violations.

- [ ] **Step 4.3: Push**

```bash
git push origin master
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement (§12 of design doc) | Covered by |
|---|---|
| `_jobs` OOM: TTL eviction, completed jobs removed every 5 min | Task 1 |
| SFTP stranded files: `_move_back_to_origin()` on parse error | Task 2 |
| `/extract` guard: 404 unless `SIPHON_ENABLE_SYNC_EXTRACT=true` | Task 3 |

**Placeholder scan:** None found. All steps contain exact code.

**Type consistency:**
- `_evict_expired()` accesses `job.finished_at` (typed as `datetime | None` in `Job` dataclass ✓) and `job.status` (str ✓)
- `_origin_map` is `dict[str, str]`, populated in `_move_to_processing` before `extract_batches` loop runs ✓
- `ENABLE_SYNC_EXTRACT` is `bool`, module-level, patched in tests via `main_module.ENABLE_SYNC_EXTRACT = True/False` ✓
