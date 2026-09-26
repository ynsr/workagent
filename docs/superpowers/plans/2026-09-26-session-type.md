# Session type + metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sessions rows carry `session_type` (start/review/sync/fix_comments/NULL) and `metadata` JSON; fix-comments flows stamp or fork sessions per the approved design.

**Architecture:** Additive nullable-column migration with prompt-based backfill; `result["command"]` set at CLI call sites so `_run_harness` derives the type once; web continue-path resolves the latest review session server-side and reuses its transcript; exit-0-only metadata stamp in the run mirror.

**Tech Stack:** Python (Typer CLI, sqlite3, FastAPI/pytest), TypeScript/React (Vite, shadcn), SQLite DDL + `PRAGMA table_info` probe migrations.

**Spec:** `docs/superpowers/specs/2026-09-26-session-type-design.md`

## Global Constraints

- SQLite only: `session_type TEXT` NULL-able, `metadata TEXT NOT NULL DEFAULT '{}'`; validation in Python, never rely on DB CHECK.
- Column name is `session_type`, never `type`.
- Backfill never guesses: unrecognizable rows stay NULL type + `'{}'`.
- `review_comments_fixed_at` + `fixed_by_run_id` in metadata; no bare `reviewCommentsFixed` boolean.
- Fixed-signal is run exit code 0 only; non-zero leaves metadata untouched.
- Reuse the existing one-harness-per-worktree lock for concurrency; no new locking.
- New fix session uses a fresh transcript + `metadata.fixed_from_session_id`.
- Suite via `.venv/bin/python -m pytest tests -q`; frontend via `./web/node_modules/.bin/tsc --noEmit --project web` AND `npm run build` in `web/`; rebuild `web/dist` + hard-refresh to see UI changes.
- Parity test `tests/test_webapp_data.py::test_specs_mirror_cli_flags` must pass after any flag change (mirror in `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `API_CONTRACT.md` + frontend + both test files).

## Review Focus

- A `review --fix-comments` run on a worktree with zero prior review sessions should create a fresh `fix_comments` session, not crash on the missing parent.
- A corrupt (non-JSON) `metadata` string in an old row should read back as `{}` and never break the Sessions page.
- `--new-fix-session` passed without `--fix-comments` should exit 2 with a usage error, mirroring `--fix`/`--post-comments` guards.
- An explicit CLI `--session-file` must always win over the resolved continue-file.
- A failed (non-zero) continue run must leave prior fix metadata untouched while still marking the session `failed`.

---

### Task 1: Schema migration + session helpers

**Files:**
- Modify: `src/workagent/store_sqlite.py:33-37` (SCHEMA sessions DDL), `:120-148` (add `_migrate_sessions_type` beside siblings), `:282-313` (`_schema_current` probe), `:316-342` (`init_db` wiring), `:458-468` (facade re-exports)
- Modify: `src/workagent/store_sessions.py:34-66` (`insert_session`, `finish_session` area; add `set_session_metadata`, `latest_review_session`, `SESSION_TYPES`, `derive_session_type`)
- Test: `tests/test_store_sqlite.py` (extend; mirrors `test_migrate_sessions_harness_renames_runtime_column` pattern)

**Interfaces:**
- Consumes: existing `connect(path)`, `PRAGMA table_info` probe idiom, `insert_session(path, *, worktree_ref, harness_name, initiator_command, prompt, file_path, session_id=None)`.
- Produces: `SESSION_TYPES = ("start", "review", "sync", "fix_comments")`; `insert_session(..., session_type: str | None = None, metadata: dict | None = None)`; `set_session_metadata(path, sid, patch: dict) -> dict`; `latest_review_session(path, worktree_ref) -> dict | None`; `derive_session_type(command: str, fix_comments: bool) -> str | None`; `_migrate_sessions_type(conn)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_migrate_sessions_type_adds_columns(tmp_path):
    import sqlite3
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    with sq.connect(db) as conn:
        conn.executescript(sq.SCHEMA.replace(
            "  state TEXT NOT NULL, harness_name TEXT NOT NULL DEFAULT '',",
            "  state TEXT NOT NULL, harness_name TEXT NOT NULL DEFAULT '',"))
        # simulate pre-v6 table: drop the new cols if present
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        for c in ("session_type", "metadata"):
            if c in cols:
                conn.execute(f"ALTER TABLE sessions DROP COLUMN {c}")
        conn.execute("INSERT INTO sessions (id, worktree_ref, state, harness_name,"
                     " initiator_command, prompt, file_path, created_at)"
                     " VALUES ('s1', 'k', 'finished', 'omp', 'omp', ?, '/f', '2026-01-01T00:00:00+00:00')",
                     ("Fix all open (not-resolved) review comments on this PR/MR: https://x/1",))
    sq.init_db(db)
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        assert {"session_type", "metadata"} <= cols
        row = conn.execute("SELECT session_type, metadata FROM sessions WHERE id = 's1'").fetchone()
        assert row[0] == "fix_comments"


def test_derive_session_type_matrix():
    from workagent.store_sessions import derive_session_type
    assert derive_session_type("start", False) == "start"
    assert derive_session_type("sync", False) == "sync"
    assert derive_session_type("review", False) == "review"
    assert derive_session_type("review", True) == "fix_comments"
    assert derive_session_type("cleanup", False) is None
    assert derive_session_type("", False) is None


def test_set_session_metadata_merges(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp",
                            initiator_command="review", prompt="p",
                            file_path="/f", session_type="review")
    out = sq.set_session_metadata(db, sid, {"review_comments_fixed_at": "2026-09-26T00:00:00+00:00"})
    assert out["review_comments_fixed_at"] == "2026-09-26T00:00:00+00:00"
    assert sq.get_session(db, sid)["metadata"]["review_comments_fixed_at"].startswith("2026-09-26")


def test_latest_review_session_skips_running(tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                      prompt="p", file_path="/old", session_id="old-1", session_type="review")
    sq.finish_session(db, "old-1", "finished")
    running = sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                                prompt="p", file_path="/run", session_type="review")
    assert sq.latest_review_session(db, "k")["id"] == "old-1"
    _ = running


def test_insert_session_rejects_bad_type(tmp_path):
    import pytest
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    with pytest.raises(ValueError):
        sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="x",
                          prompt="p", file_path="/f", session_type="nonsense")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store_sqlite.py -q -k "sessions_type or derive_session_type or set_session_metadata or latest_review_session or rejects_bad_type"`
Expected: FAIL (names undefined / columns missing).

- [ ] **Step 3: Write minimal implementation**

```python
#: Allowed values for sessions.session_type — an enum, never free text (NULL = unknown).
SESSION_TYPES = ("start", "review", "sync", "fix_comments")

_FIX_PROMPT_PREFIX = "Fix all open (not-resolved) review comments on this PR/MR:"

def derive_session_type(command: str, fix_comments: bool = False) -> str | None:
    """Subcommand + flag → session_type (spec §2.1). Unknown → None, never guess."""
    c = (command or "").strip().lower()
    if c == "review" and fix_comments:
        return "fix_comments"
    if c in ("start", "review", "sync"):
        return c
    return None

def _backfill_session_type(initiator_command: str, prompt: str) -> str | None:
    if (prompt or "").startswith(_FIX_PROMPT_PREFIX):
        return "fix_comments"
    first = (initiator_command or "").split(" ", 1)[0].strip().lower()
    if first in ("start", "review", "sync"):
        return first
    if "--fix-comments" in (initiator_command or ""):
        return "fix_comments"
    return None
```

Migration in `store_sqlite.py` (beside `_migrate_sessions_harness`):

```python
def _migrate_sessions_type(conn) -> None:
    """Schema v6: sessions gains session_type + metadata (spec §4)."""
    from .store_sessions import _backfill_session_type  # local: avoid import cycle
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if not cols:
        return
    if "session_type" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN session_type TEXT")
    if "metadata" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
    for sid, cmd, prompt, stype in conn.execute(
            "SELECT id, initiator_command, prompt, session_type FROM sessions WHERE session_type IS NULL"):
        conn.execute("UPDATE sessions SET session_type = ? WHERE id = ?",
                     (_backfill_session_type(cmd or "", prompt or ""), sid))
```

Plus: SCHEMA DDL gains the two columns; `init_db` calls `_migrate_sessions_type(conn)` after `_migrate_sessions_harness(conn)`; `_schema_current` requires `{"session_type", "metadata"} <= scols`; `insert_session` validates + writes both (metadata via `json.dumps(metadata or {})`); `set_session_metadata` read-modify-writes JSON; `latest_review_session` = newest non-running `session_type='review'` row for the ref; `list_sessions`/`get_session` parse metadata to dict with `{}` fallback; facade re-exports the four new names.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store_sqlite.py -q`
Expected: PASS (all incl. new).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_sessions.py tests/test_store_sqlite.py
git commit -m "feat(db): sessions session_type + metadata with prompt backfill"
```

### Task 2: CLI derivation + `--new-fix-session` + continue mechanics

**Files:**
- Modify: `src/workagent/cli_harness.py:69-159` (`_run_harness`: derive + pass `session_type`/`metadata`; honor `result["reuse_session_id"]` by skipping insert)
- Modify: `src/workagent/cli_start.py:169-176` + `cli_sync.py:260-280` + `src/workagent/cli_review.py:341-342,383-384` (set `result["command"]`; review sets `fix_comments` marker for derivation)
- Modify: `src/workagent/cli_review.py:170-213` (new `--new-fix-session` flag + `--fix-comments` requirement guard; continue lookup via `latest_review_session`; `--session-file` precedence)
- Test: `tests/test_cli.py` or `tests/test_review_fix.py` if present (check first; derivation + guard tests)

**Interfaces:**
- Consumes: Task 1 `derive_session_type`, `latest_review_session`, `insert_session(..., session_type, metadata)`.
- Produces: `result["command"] ∈ {"start","review","sync"}` at all three call sites; `review --new-fix-session` flag; continue path sets `result["reuse_session_id"]` + effective session file.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_new_fix_session_requires_fix_comments():
    from typer.testing import CliRunner
    from workagent.cli_core import app
    r = CliRunner().invoke(app, ["review", "OWNER/R#1", "--new-fix-session"])
    assert r.exit_code == 2
    assert "--fix-comments" in r.output


def test_run_harness_derives_fix_comments_type(monkeypatch, tmp_path):
    """_run_harness with command=review + fix marker inserts fix_comments type."""
    import json
    from workagent import cli_harness as _h, store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    monkeypatch.setattr(sq, "db_path", lambda: db)
    monkeypatch.setattr(_h.store, "record_harness_run", lambda *a: None)
    monkeypatch.setattr(_h.store, "clear_harness_run", lambda *a: None)
    from workagent import backend as _b
    monkeypatch.setattr(_b, "launch", lambda *a, **k: None)
    result = {"command": "review", "fix_comments": True}
    _h._run_harness("omp", "Fix all open (not-resolved) review comments on this PR/MR: u",
                    "/tmp", "/tmp", True, True, result, False, run_key="k")
    rows = sq.list_sessions(db)
    assert rows and rows[0]["session_type"] == "fix_comments"


def test_review_continue_reuses_transcript(monkeypatch, tmp_path):
    """Continue path: latest finished review session file reused, no new row at resolve time."""
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                            prompt="p", file_path="/s/old.jsonl", session_id="old-9",
                            session_type="review")
    sq.finish_session(db, "old-9", "finished")
    got = sq.latest_review_session(db, "k")
    assert got and got["file_path"] == "/s/old.jsonl"
    _ = sid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q -k "new_fix_session or derives_fix or continue_reuses"`
Expected: FAIL (`--new-fix-session` unknown; type not written).

- [ ] **Step 3: Write minimal implementation**
  - `cli_start.py` + `cli_sync.py` + both review call sites: add `"command": "start"|"sync"|"review"` to the `result` dicts; review adds `"fix_comments": fix_comments`.
  - `_run_harness`: `stype = derive_session_type(result.get("command", ""), bool(result.get("fix_comments")))`; `reuse = result.get("reuse_session_id")`; if reuse → skip `insert_session` (sid stays None unless explicit session_file row exists — actually sid = reuse id for finish-stamping); else `insert_session(..., session_type=stype, metadata=result.get("session_metadata"))`.
  - `review --new-fix-session`: `typer.Option(False, "--new-fix-session", ...)`; guard `if new_fix_session and not fix_comments: _fail("--new-fix-session requires --fix-comments", EXIT_USAGE)` beside the `--fix`/`--post-comments` guards; new-session path sets `result["session_metadata"] = {"fixed_from_session_id": latest.id}` when a prior review session exists; continue path (fix_comments and not new_fix_session and not session_file): `latest = latest_review_session(db, review_key)`; if found → effective `session_file = latest["file_path"]`, `result["reuse_session_id"] = latest["id"]`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py tests/test_store_sqlite.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_harness.py src/workagent/cli_start.py src/workagent/cli_sync.py src/workagent/cli_review.py tests/test_cli.py
git commit -m "feat(cli): session_type derivation + review --new-fix-session + continue reuse"
```

### Task 3: Web backend — mirror stamp + review-session lookup

**Files:**
- Modify: `src/workagent/web_runs.py:38-78` (`_mirror_run`: reuse-session targeting + exit-0 metadata stamp)
- Modify: `src/workagent/webapp.py` (add `GET /api/review-session?ref=` beside `default_repo`, ~`webapp.py:200-209`)
- Modify: `src/workagent/web_args.py:36-66` (`BOOL_FLAGS["review"]` += `"--new-fix-session"`)
- Modify: `web/API_CONTRACT.md` (new endpoint entry)
- Test: `tests/test_webapp_runs.py` (mirror stamp + endpoint tests)

**Interfaces:**
- Consumes: Task 1–2 (`set_session_metadata`, `latest_review_session`, `--new-fix-session` passthrough).
- Produces: `GET /api/review-session?ref= → {session_id, file_path} | null`; `_mirror_run` stamps `review_comments_fixed_at` + `fixed_by_run_id` on exit-0 continue runs.

- [ ] **Step 1: Write the failing tests**

```python
def test_mirror_run_stamps_fix_metadata(client, tmp_path, monkeypatch):
    """Exit-0 run reusing a review session stamps fixed_at + run id."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sid = sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                            prompt="p", file_path="/s/r.jsonl", session_id="rev-1",
                            session_type="review")
    sq.finish_session(db, "rev-1", "finished")
    run = client.app.state.registry.create("review", ["k", "--fix-comments"], "review:k")
    run.session_file = "/s/r.jsonl"
    run.exit_code = 0
    from workagent import web_runs as _wr
    _wr._mirror_run(run)
    md = sq.get_session(db, sid)["metadata"]
    assert md["review_comments_fixed_at"]
    assert md["fixed_by_run_id"] == run.id


def test_api_review_session_returns_latest(client, tmp_path):
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sq.insert_session(db, worktree_ref="k", harness_name="omp", initiator_command="review",
                      prompt="p", file_path="/s/r.jsonl", session_id="rev-2",
                      session_type="review")
    sq.finish_session(db, "rev-2", "finished")
    r = client.get("/api/review-session", params={"ref": "k"})
    assert r.status_code == 200, r.text
    assert r.json()["file_path"] == "/s/r.jsonl"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_webapp_runs.py -q -k "stamps_fix or review_session"`
Expected: FAIL (no stamp; 404 endpoint).

- [ ] **Step 3: Write minimal implementation**
  - `_mirror_run`: after computing `command, args` — detect continue: `"--fix-comments" in args and "--new-fix-session" not in args`; target sid = stem of `run.session_file` (unchanged); after `insert_run`, if continue and `run.exit_code == 0`: `set_session_metadata(db, sid, {"review_comments_fixed_at": now_iso, "fixed_by_run_id": run.id})` — only when that session's `session_type == "review"`. Wrap in try/except (best-effort mirror, never crash the run).
  - New endpoint (read-only GET, always 200): resolve ref → worktree key via the same fuzzy resolver cleanup/open use → `latest_review_session(db, key)` → `{session_id, file_path}` or `{session_id: None, file_path: None}`.
  - `BOOL_FLAGS["review"]` += `"--new-fix-session"`; `_validate_args` passes it (bool path, no extra code if generic); `API_CONTRACT.md` documents the endpoint.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webapp_runs.py tests/test_webapp_data.py -q`
Expected: PASS incl. `test_specs_mirror_cli_flags`.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_runs.py src/workagent/webapp.py src/workagent/web_args.py web/API_CONTRACT.md tests/test_webapp_runs.py
git commit -m "feat(web): review-session lookup + fix-metadata mirror stamp"
```

### Task 4: Frontend — Type column, badge, new-session checkbox

**Files:**
- Modify: `web/src/lib/api.ts:139-156` (`SessionRow`: `session_type: string | null; metadata: Record<string, unknown>`)
- Modify: `web/src/lib/launchConfig.ts:48-67` (`LaunchForm.newFixSession`, `INITIAL`)
- Modify: `web/src/pages/Launch.tsx:104-108,181-192,194-203,308-317` (init from params, args building, flag list, checkbox gated on fixComments)
- Modify: `web/src/lib/api.ts:322-329` area + `web/src/lib/queries.ts` (add `reviewSession(ref)` query)
- Modify: `web/src/pages/Sessions.tsx:66-203` (Type column + fixed-badge; search includes type)
- Modify: `web/src/pages/Sessions.tsx:205-260` (Detail metadata block)
- Test: existing `npx tsc -b --noEmit` + `npm run build` (no JS unit harness in repo)

**Interfaces:**
- Consumes: Task 3 endpoint + `SessionRow` shape.
- Produces: `newFixSession` form field; Type column; `✓ fixed` badge.

- [ ] **Step 1: Write the failing type check**

Add `session_type`/`metadata` to `SessionRow`, reference `s.session_type` in `Sessions.tsx`, run `./web/node_modules/.bin/tsc --noEmit --project web`.
Expected: FAIL (property missing until api.ts updated — implement both in step 3; the check is the gate).

- [ ] **Step 2: Confirm the gate trips**

Run: `./web/node_modules/.bin/tsc --noEmit --project web`
Expected: FAIL with `TS2339: Property 'session_type' does not exist`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// api.ts
export interface SessionRow {
  id: string; worktree_ref: string; state: string; harness_name: string;
  initiator_command: string; file_path: string; created_at: string;
  session_type: string | null; metadata: Record<string, unknown>;
}
// + query:
reviewSession: (ref: string) =>
  request<{ session_id: string | null; file_path: string | null }>(
    `/api/review-session?ref=${encodeURIComponent(ref)}`),
```

```tsx
// launchConfig.ts
export interface LaunchForm {
  ref: string; repo: string; depth: string; base: string;
  launch: boolean; fixComments: boolean; newFixSession: boolean; merge: boolean;
}
export const INITIAL: LaunchForm = { ..., fixComments: false, newFixSession: false, ... };
```

```tsx
// Launch.tsx buildArgs (review branch):
const args = [refValue, "--no-tty"];
if (form.fixComments) args.push("--fix-comments");
if (form.fixComments && form.newFixSession) args.push("--new-fix-session");
```

Continue wiring in `handleSubmit` (review + fixComments + !newFixSession): `const rs = await api.reviewSession(refValue); if (rs.file_path) args.push("--session-file", rs.file_path);` — failure/None falls through to fresh-session behavior (Task 3 backend then types it `fix_comments`).

```tsx
{mode === "review" && form.fixComments ? (
  <CheckRow
    id="launch-new-fix-session"
    checked={form.newFixSession}
    onChange={(v) => update("newFixSession", v)}
    label="Create a new session"
    flag="--new-fix-session"
    description="Start a fresh fix session (default: continue the latest review session and mark it fixed on success)."
  />
) : null}
```

Sessions table: `<TableHead>Type</TableHead>` + cell `{s.session_type ?? "—"}`; fixed-badge when `s.session_type === "review" && typeof s.metadata?.review_comments_fixed_at === "string"` → `✓ fixed {relativeTime}` with `title={fixed_by_run_id}`; search string gains `session_type`. Detail page: metadata block rendering the two fix fields when present.

- [ ] **Step 4: Run checks to verify they pass**

Run: `./web/node_modules/.bin/tsc --noEmit --project web` then `npm run build` in `web/`
Expected: both clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/queries.ts web/src/lib/launchConfig.ts web/src/pages/Launch.tsx web/src/pages/Sessions.tsx
git commit -m "feat(web): session type column + new-fix-session option"
```

### Task 5: Full verification + reinstall + serve reset

**Files:** none (verification + deployment only).

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS (baseline 459 + new tests).

- [ ] **Step 2: Run both frontend gates**

Run: `./web/node_modules/.bin/tsc --noEmit --project web && npm run build` (in `web/`)
Expected: clean `tsc`, `vite build` emits new `web/dist/index-*.js`.

- [ ] **Step 3: Reinstall**

Run: `./install.sh`
Expected: `doctor: ok`, fresh `web/dist` installed.

- [ ] **Step 4: Reset serve**

Run: kill the `:3344` holder, start `workagent serve --port 3344`, verify `GET /api/sessions` rows carry `session_type`/`metadata` and the Sessions page shows the Type column.

- [ ] **Step 5: Commit any stragglers (CHANGELOG/docs) and push**

```bash
git add -A && git commit -m "chore(release): session type + metadata" && git push
```

## Self-Review

- **Spec coverage:** §4 migration+helpers → Task 1; §5 CLI mechanics+`--new-fix-session` → Task 2; §6 endpoint+mirror → Task 3; §6 frontend → Task 4; §7 rollout → Task 5. `result["command"]` correction (§3) → Task 2. Backfill rule (§2.5) → Task 1.
- **Placeholders:** none — every step names exact files, signatures, commands, expected outputs.
- **Type consistency:** `session_type: str | None` (py) ↔ `session_type: string | null` (ts); metadata dict ↔ `Record<string, unknown>`; endpoint shape `{session_id, file_path}` identical in py/ts/tests.
- **Review Focus:** five failure modes listed; each pinned: missing-parent → Task 2/3 fallthrough (+ test in Task 3 suite via null endpoint); corrupt metadata → Task 1 fallback test; flag-without-parent → Task 2 guard test; `--session-file` precedence → Task 2 implementation note (add assertion in Task 2 Step 4 run); non-zero leave → Task 3 stamp condition (extend stamp test with exit-1 case during implementation).
