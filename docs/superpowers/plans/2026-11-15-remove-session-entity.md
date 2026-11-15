# Remove Session Entity (Fold Into Runs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the Session entity; every transcript behavior lives on Runs.

**Architecture:** One-way DB migration folds session columns into runs and drops the table; backend call sites switch to run-keyed helpers; session API/routes and Sessions UI are deleted; transcript dir moves `sessions/` → `runs/` with fallback reads.

**Tech Stack:** Python stdlib sqlite3, FastAPI, Typer, React + TanStack Query, pytest.

**Spec:** `docs/superpowers/specs/2026-11-15-remove-session-entity-design.md`

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- User content in Rich output is `rich.markup.escape`d; PR titles may contain `[`.
- Parity checklist: CLI flag → web arg passthrough → `web/API_CONTRACT.md` entry → frontend control → both test files (`tests/test_cli.py`, `tests/test_webapp.py`).
- `CHANGELOG.md`: one line per change, ≤150 chars, non-technical wording, ID at end `[104]`; append-only.
- No new implementation patterns; follow existing module seams (`store_sqlite.py`, `cli_harness.py`, `web_runs*.py`).

## Review Focus

- A pre-v16 `state.db` with fix-continue chains must migrate with `continued_from_run_id` intact and resume still working — the one-way migration has no rollback.
- A `file_path` pointing at the old `sessions/` dir must still resume after the rename (fallback read).
- `review --fix-comments --use-review-session` with no prior review run must behave exactly as today (fresh transcript, no crash).
- Concurrent web preview + CLI child adopting the same transcript must not create duplicate run rows.
- Deleted-worktree runs stay hidden from list/detail unless `include_deleted_worktrees` (existing `_NOT_DELETED` pass-through rule).

---

### Task 1: Migration v16 — fold sessions into runs, drop table, move dir

**Files:**
- Modify: `src/workagent/store_sqlite.py`
- Modify: `src/workagent/store.py` (facade re-exports, if any session names)
- Test: `tests/test_store_sqlite.py`

**Interfaces:**
- Consumes: existing `SCHEMA`, `init_db`, migration chain (`_migrate_*`).
- Produces: `migrate_sessions_into_runs(conn)`; new `runs` columns (`worktree_ref`, `harness_name`, `initiator_command`, `prompt`, `file_path`, `session_type`, `metadata`, `state`, `continued_from_run_id`); transcript dir constant `RUNS_DIR = config_dir / "runs"` (replacing `sessions/` usage for transcripts).

- [ ] **Step 1: Write the failing test**

```python
def test_migrate_v16_folds_sessions_into_runs(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    # seed minimal v15 schema with one session + preview/execution runs sharing it
    ...
    sq.init_db(db)  # runs migration
    assert "sessions" not in sq.table_names(db)
    rows = sq.list_runs(db)
    assert rows[0]["file_path"].endswith(".jsonl")
    assert rows[0]["session_type"] == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_sqlite.py::test_migrate_v16_folds_sessions_into_runs -v`
Expected: FAIL (no v16 migration / columns missing)

- [ ] **Step 3: Write minimal implementation**

Implement `migrate_sessions_into_runs`: `ALTER TABLE runs ADD COLUMN` for each new col; `UPDATE runs SET ... FROM sessions`; set `continued_from_run_id` for chained runs; `DROP TABLE sessions`; move `sessions/` dir → `runs/` with `file_path` prefix rewrite.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_sqlite.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store.py tests/test_store_sqlite.py
git commit -m "[104] Fold sessions table into runs via v16 migration"
```

### Task 2: Run-keyed transcript helpers replace store_sessions.py

**Files:**
- Delete: `src/workagent/store_sessions.py`
- Modify: `src/workagent/store_sqlite.py` (new helpers)
- Test: `tests/test_store_sqlite.py`

**Interfaces:**
- Consumes: Task 1 columns.
- Produces: `insert_run(..., worktree_ref=None, file_path="", prompt="", session_type=None, metadata=None, state="finished", continued_from_run_id=None)`, `latest_review_run(worktree_ref)`, `run_by_file_path(file_path)`, `set_run_file_path(run_id, file_path)`.

- [ ] **Step 1: Write the failing test**

```python
def test_latest_review_run_resolves_from_runs(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    rid = sq.insert_run(db, "review", ["k"], 0, worktree_ref="k", session_type="review", file_path="/tmp/x.jsonl")
    latest = sq.latest_review_run(db, "k")
    assert latest["id"] == rid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_sqlite.py::test_latest_review_run_resolves_from_runs -v`
Expected: FAIL (helper missing)

- [ ] **Step 3: Write minimal implementation**

Move `session_file_path`, `branch_path`, `gen_session_id`, `derive_session_type`, `MAX_RUN_OUTPUT_LINES` into `store_sqlite.py` (transcript-path helpers are entity-neutral); implement run-keyed helpers; update all `store_sessions` import sites to `store_sqlite`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_sqlite.py tests/test_run_store.py tests/test_save_all_sessions.py -v`
Expected: PASS (update `test_save_all_sessions.py` to runs if it pins sessions)

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py tests/test_store_sqlite.py
git commit -m "[104] Replace session helpers with run-keyed transcript helpers"
```

### Task 3: Harness launch + preview paths write runs only

**Files:**
- Modify: `src/workagent/cli_harness.py`
- Modify: `src/workagent/cli_review.py` (reuse lookup)
- Modify: `src/workagent/cli_flow_steps.py` (flow tagging → runs)
- Modify: `src/workagent/cli_cleanup_gc.py` (transcript GC by file_path)
- Test: `tests/test_cli.py` (or targeted harness/review tests)

**Interfaces:**
- Consumes: Task 2 helpers.
- Produces: `record_preview_run(...)` (replaces `record_preview_session`); launch adopts preview row by `file_path` stem; `reuse_run_id` (replaces `reuse_session_id`).

- [ ] **Step 1: Write the failing test**

```python
def test_preview_then_launch_share_one_transcript():
    # preview run with state='preview' + file_path; launch adopts same row/file
    ...
    assert adopted["file_path"] == preview["file_path"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v -k "preview or harness"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Replace `insert_session`/`finish_session`/`session_by_file_path` calls with run-keyed equivalents; keep `--session-file` flags untouched; keep `derive_session_type` rule for `session_type` column.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_harness.py src/workagent/cli_review.py src/workagent/cli_flow_steps.py src/workagent/cli_cleanup_gc.py tests/test_cli.py
git commit -m "[104] Write harness launches as runs-only rows"
```

### Task 4: Web runs registry + review-session from runs

**Files:**
- Modify: `src/workagent/web_runs.py` (precreate/adopt/mirror by run id)
- Modify: `src/workagent/web_runs_routes.py` (drop `/api/sessions*` resume; terminal-worktree row → runs)
- Modify: `src/workagent/webapp.py` (`/api/sessions*` deleted; `/api/review-session` from runs)
- Test: `tests/test_webapp.py`, `tests/test_webapp_runs.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `GET /api/review-session` → `{ref, run_id, file_path}` from latest review run; persisted resume stays `POST /api/runs/db-<id>/resume`.

- [ ] **Step 1: Write the failing test**

```python
def test_review_session_resolves_from_runs(client):
    r = client.get("/api/review-session", params={"ref": key})
    assert r.status_code == 200 and r.json()["file_path"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp_runs.py -v -k review_session`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

Delete session endpoints; repoint lookups (`latest_review_run`, `run_by_file_path`); update `SPECS` mirror test expectations.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_webapp.py tests/test_webapp_runs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_runs.py src/workagent/web_runs_routes.py src/workagent/webapp.py tests/test_webapp.py tests/test_webapp_runs.py
git commit -m "[104] Serve review-session and resume from runs"
```

### Task 5: Frontend — delete Sessions surface, Runs own transcripts

**Files:**
- Delete: `web/src/pages/Sessions.tsx`
- Modify: `web/src/App.tsx`, `web/src/lib/api.ts`, `web/src/lib/queries.ts`, `web/src/components/*` (RunActions/resume), `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `web/src/**/*.test.ts*` (runs/launch suites)

**Interfaces:**
- Consumes: Task 4 API.
- Produces: no `/sessions` routes/nav/query keys; Runs table/detail show `session_type` kind label + resume/copy; in-app `/sessions*` links → `/runs*`.

- [ ] **Step 1: Write the failing test**

```ts
it("has no sessions routes or query keys", () => {
  expect(appRoutes).not.toContain("/sessions");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- runs`
Expected: FAIL (Sessions routes present)

- [ ] **Step 3: Write minimal implementation**

Delete page + routes + nav + query keys + `SessionRow` types; repoint resume/copy to run endpoints; update contract docs.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src web/API_CONTRACT.md web/FEATURE_INVENTORY.md
git commit -m "[104] Remove Sessions UI; Runs own transcripts"
```

### Task 6: Docs, changelog, final verification

**Files:**
- Modify: `CHANGELOG.md` (append one line), `README.md`/`AGENTS.md` if they name sessions
- Test: full suite

- [ ] **Step 1: Append changelog line**

`Simplified run history: transcripts now live on runs, Sessions view removed [104]` (≤150 chars, non-technical)

- [ ] **Step 2: Run full verification**

Run: `uv run pytest -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "[104] Document Session removal"
```
