# Flow linkage: orphaned worktrees, missing sessions, lost failures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every flow step leaves a session + run row, every failure persists with the cursor held, cleanup settles linked flows, and all three pages filter by flow linkage server-side.

**Architecture:** `flows` stays the owner; `sessions.flow_id` + `runs.flow_id` nullable FKs (`ON DELETE SET NULL`) are the query path, worktree stays primary while attached. `sessions.worktree_ref` becomes nullable (NULL = unattached, exempt from FK). Cleanup auto-fails open flows (exact key + launch_ref issue-key match).

**Tech Stack:** Python 3.12, stdlib sqlite3 (no ORM), Typer CLI, FastAPI, React + TS (Vite).

**Spec:** `docs/superpowers/specs/2026-10-23-flow-linkage-design.md`

## Global Constraints

- Python files and React components/classes must not exceed 500 lines.
- New/renamed CLI flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli.py` + webapp tests in the same PR.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Flow cleanup steps stay fixed: squash + soft-delete (`--hard-delete`/`--no-squash` rejected).
- `CHANGELOG.md` entries ≤ 150 chars, append issue ID in brackets.

## File Structure

- Modify: `src/workagent/store_sqlite.py` (SCHEMA, v15 migration, `_schema_current`, `_REQUIRED_COLS`, `_NOT_DELETED` handling)
- Modify: `src/workagent/store_sessions.py` (`insert_session` + `insert_run` gain `flow_id`, `list_*` gain `flow_id` filter, NULL-safe queries)
- Modify: `src/workagent/store_flows.py` (`list_flows(status, repo)`, `fail_open_flows`, `match_launch_ref` issue-key matcher)
- Modify: `src/workagent/cli_flow_run.py` (flow-owned session, per-step `flow_id` at insert, `except Exception` failure path, resume adopt + backfill, `_find_resumable` issue-key match, `flow list --status --repo`, `flow show` deleted-aware)
- Modify: `src/workagent/cli_flow_util.py` (`persist_terminal_pause` flows through NULL-worktree path, `backfill_flow_worktree`)
- Modify: `src/workagent/cli_harness.py` (`record_preview_session`/`record_headless_attempt` accept `flow_id` + `allow_null_worktree`, keep warn-and-skip for non-flow unattached)
- Modify: `src/workagent/store_links.py` (call `fail_open_flows` on hard delete + GC; soft-delete path calls it from `cli_cleanup._cleanup_one`)
- Modify: `src/workagent/cli_cleanup.py` (`_cleanup_one` fails open flows after stamp/drop)
- Modify: `src/workagent/web_args.py` (SPECS key for `flow:<tpl>:<ref>` already exists — verify; add `flow list` VAL_FLAGS for `--status --repo --worktree`)
- Modify: `src/workagent/webapp.py` (`GET /api/flows?worktree=&status=&repo=`, `GET /api/sessions?flow=`, `/api/runs` via `web_runs_routes.py`)
- Modify: `src/workagent/web_runs.py` + `src/workagent/web_runs_routes.py` (run summaries carry `flow_id`, `?flow=` filter)
- Modify: `src/workagent/store.py` (facade: `create_flow/get_flow/list_flows/update_flow`, `fail_open_flows` passthrough, `list_sessions/list_runs` flow passthrough)
- Modify: `web/src/lib/api.ts`, `web/src/lib/queries.ts` (`useFlows(worktree, status, repo)`, `useSessions(flow)`, runs `?flow=`)
- Modify: `web/src/pages/Flows.tsx`, `web/src/pages/Sessions.tsx`, `web/src/pages/Runs.tsx` (server-driven filters + URL params)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `CHANGELOG.md`
- Test: `tests/test_flow_linkage.py` (new: migration, adopt, pause, failure, cleanup, filters), extend `tests/test_webapp_data.py` (parity), `tests/test_webapp_flow.py` (filters)

## Review Focus

- Paused flow cleaned before resume shows `failed (worktree cleaned)` and resume creates a fresh row (no zombie adoption).
- Concurrent `flow run` resume races stay last-write-wins (no new locking).
- `metadata.flow_id` for deleted flow ids never backfills the column (skip, keep history).
- Unknown `?flow=<id>` returns `[]`, never 404.
- Invalid `--status` exits 2 with usage text on stderr.

---

### Task 1: v15 schema migration (flow_id columns, nullable worktree_ref, backfill)

**Files:**
- Modify: `src/workagent/store_sqlite.py:15-84` (SCHEMA sessions/runs DDL)
- Modify: `src/workagent/store_sqlite.py:461-472` (_REQUIRED_COLS sessions row)
- Modify: `src/workagent/store_sqlite.py:545-606` (_schema_current)
- Modify: `src/workagent/store_sqlite.py:615-653` (init_db chain)
- Test: `tests/test_flow_linkage.py`

**Interfaces:**
- Consumes: existing `_canonical_ddl`, `_rebuild_required_columns`, `_migrate_sessions_no_fk` (v14 pattern to follow)
- Produces: `_migrate_flow_linkage(conn)` (idempotent v15), updated SCHEMA DDL consumed by Task 2's `insert_session(flow_id=)` / `insert_run(flow_id=)`; `_schema_current` gating consumed by all later tasks

- [ ] **Step 1: Write the failing test**

```python
def test_v15_adds_flow_id_and_backfills(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO flow_templates (label, name, steps, created_at, modified_at)"
                     " VALUES ('t', 'T', '[]', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO flows (template_label, worktree_ref, repo_snapshot, status,"
                     " current_step, step_results, created_at, modified_at)"
                     " VALUES ('t', 'k', '{}', 'paused', 0, '[]', '2026-01-01', '2026-01-01')")
        fid = conn.execute("SELECT id FROM flows").fetchone()[0]
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, added_at, payload)"
                     " VALUES ('k', '/tmp/wt', 'b', '2026-01-01', '{}')")
        import json as _j
        conn.execute("INSERT INTO sessions (id, worktree_ref, state, harness_name,"
                     " initiator_command, prompt, file_path, created_at, metadata)"
                     " VALUES ('s1', 'k', 'preview', 'omp', 'flow run t k', 'p', '', '2026-01-01', ?)",
                     (_j.dumps({"flow_id": fid}),))
        conn.execute("INSERT INTO runs (session_id, command, args, exit_code, created_at)"
                     " VALUES ('s1', 'flow', '[]', 0, '2026-01-01')")
    sq._INIT_CACHE.pop(str(db), None)
    sq.init_db(db)
    with sq.connect(db) as conn:
        scols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        rcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
        assert {"flow_id"} <= scols and {"flow_id"} <= rcols
        assert conn.execute("SELECT flow_id FROM sessions WHERE id='s1'").fetchone()[0] == fid
        assert conn.execute("SELECT flow_id FROM runs WHERE session_id='s1'").fetchone()[0] == fid
        assert sq._schema_current(db) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow_linkage.py::test_v15_adds_flow_id_and_backfills -v`
Expected: FAIL with "no such column: flow_id" or assertion on missing columns

- [ ] **Step 3: Write minimal implementation**

```python
def _migrate_flow_linkage(conn) -> None:
    """Schema v15: sessions/runs gain flow_id; sessions.worktree_ref nullable.

    flow_id is the query path (metadata.flow_id stays as history). NULL
    worktree_ref = unattached flow row, exempt from the worktrees FK.
    Idempotent: ADD COLUMN skipped when present; sessions rebuilt from the
    canonical DDL only while worktree_ref is still NOT NULL.
    """
    import json as _json
    info = list(conn.execute("PRAGMA table_info(sessions)"))
    if not info:
        return
    cols = {r[1] for r in info}
    if "flow_id" not in cols:
        conn.execute("ALTER TABLE sessions ADD COLUMN flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL")
    rcols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    if rcols and "flow_id" not in rcols:
        conn.execute("ALTER TABLE runs ADD COLUMN flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL")
    wr = next((r for r in info if r[1] == "worktree_ref"), None)
    if wr is not None and wr[3] == 1 and wr[4] is None:
        if conn.in_transaction:
            conn.commit()
        was_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if was_on:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            ddl = _canonical_ddl()["sessions"]
            conn.execute('DROP TABLE IF EXISTS "sessions_rebuild"')
            conn.execute(ddl.replace("CREATE TABLE sessions", 'CREATE TABLE "sessions_rebuild"', 1))
            new_cols = {r[1] for r in conn.execute('PRAGMA table_info("sessions_rebuild")')}
            old_cols = [r[1] for r in info if r[1] in new_cols]
            select = ", ".join(
                "NULLIF(\"%s\", '')" % c if c == "worktree_ref" else '"%s"' % c
                for c in old_cols)
            columns = ", ".join('"%s"' % c for c in old_cols)
            conn.execute(f'INSERT INTO "sessions_rebuild" ({columns}) SELECT {select} FROM "sessions"')
            conn.execute('DROP TABLE "sessions"')
            conn.execute('ALTER TABLE "sessions_rebuild" RENAME TO "sessions"')
            if conn.in_transaction:
                conn.commit()
        finally:
            if was_on:
                conn.execute("PRAGMA foreign_keys = ON")
    # Backfill flow_id from metadata.flow_id (integer ids of live flows only).
    live = {r[0] for r in conn.execute("SELECT id FROM flows")}
    for sid, meta in conn.execute("SELECT id, metadata FROM sessions"):
        try:
            m = _json.loads(meta or "{}")
        except Exception:
            continue
        f = m.get("flow_id") if isinstance(m, dict) else None
        if isinstance(f, int) and f in live:
            conn.execute("UPDATE sessions SET flow_id = ? WHERE id = ? AND flow_id IS NULL", (f, sid))
    conn.execute("UPDATE runs SET flow_id = (SELECT flow_id FROM sessions WHERE sessions.id = runs.session_id)"
                 " WHERE flow_id IS NULL")
```

SCHEMA edits (exact):
- `sessions`: `worktree_ref TEXT REFERENCES worktrees(ref_key) ON DELETE CASCADE` (drop NOT NULL), add `flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL` at end.
- `runs`: add `flow_id INTEGER REFERENCES flows(id) ON DELETE SET NULL` at end.
- `_REQUIRED_COLS["sessions"]`: remove `"worktree_ref"` → `("id", "state", "created_at")`.
- `_schema_current`: add `if "flow_id" not in scols: return False`; add runs `flow_id` check.
- `init_db`: call `_migrate_flow_linkage(conn)` after `_migrate_sessions_no_fk(conn)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow_linkage.py::test_v15_adds_flow_id_and_backfills -v`
Expected: PASS; also `pytest tests/test_save_all_sessions.py -v` (FK CASCADE for attached rows still fires; NULL rows survive)

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py tests/test_flow_linkage.py
git commit -m "Add v15 flow linkage schema: sessions/runs flow_id, nullable worktree_ref [no-issue]"
```

### Task 2: session/run store gains flow_id (insert + filtered lists + re-attach)

**Files:**
- Modify: `src/workagent/store_sessions.py:105-153` (insert_session)
- Modify: `src/workagent/store_sessions.py:271-284` (insert_run)
- Modify: `src/workagent/store_sessions.py:286-292` (_NOT_DELETED NULL-safe)
- Modify: `src/workagent/store_sessions.py:295-348` (list_runs, list_sessions)
- Test: `tests/test_flow_linkage.py`

**Interfaces:**
- Consumes: Task 1's `flow_id` columns + nullable `worktree_ref`
- Produces: `insert_session(..., flow_id=None, allow_null_worktree=False) -> str`, `insert_run(..., flow_id=None) -> int`, `list_sessions(..., flow_id=0)`, `list_runs(..., flow_id=0, limit=200)`, `backfill_flow_worktree(path, flow_id, worktree_ref)` — consumed by Tasks 3–4

- [ ] **Step 1: Write the failing test**

```python
def test_null_worktree_flow_session_persists_and_filters(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    import workagent.store_sessions as ss
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO flow_templates (label, name, steps, created_at, modified_at)"
                     " VALUES ('t', 'T', '[]', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO flows (template_label, worktree_ref, repo_snapshot, status,"
                     " current_step, step_results, created_at, modified_at)"
                     " VALUES ('t', '', '{}', 'paused', 0, '[]', '2026-01-01', '2026-01-01')")
        fid = conn.execute("SELECT id FROM flows").fetchone()[0]
    sid = ss.insert_session(db, worktree_ref=None, harness_name="omp",
                            initiator_command="flow run t jira:X-1", prompt="p",
                            file_path="", flow_id=fid, allow_null_worktree=True)
    rid = ss.insert_run(db, sid, "flow", ["run", "t"], 0, output=["paused"], flow_id=fid)
    assert ss.list_sessions(db, flow_id=fid)[0]["id"] == sid
    assert ss.list_runs(db, flow_id=fid)[0]["id"] == rid
    assert ss.list_sessions(db) != []  # NULL ref visible by default (pass-through)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow_linkage.py::test_null_worktree_flow_session_persists_and_filters -v`
Expected: FAIL with "unexpected keyword argument 'flow_id'" (or allow_null_worktree)

- [ ] **Step 3: Write minimal implementation**

```python
def insert_session(path, *, worktree_ref, harness_name, initiator_command, prompt,
                   file_path, session_id=None, session_type=None, metadata=None,
                   flow_id=None, allow_null_worktree=False) -> str:
    # ... existing validation ...
    if not worktree_ref and not allow_null_worktree:
        # existing callers pass "" for unattached — keep FK behavior:
        # "" stored as-is (FK rejects dangling); NULL only for flow rows.
        pass
    ref = None if (not worktree_ref and allow_null_worktree) else (worktree_ref or "")
    # names/vals gain flow_id when the column exists:
    #   if "flow_id" in cols: names.append("flow_id"); vals.append(flow_id)
```

```python
def insert_run(path, session_id, command, args, exit_code, output=None,
               truncated=False, flow_id=None) -> int:
    # column-detect like insert_session; flow_id defaults from parent session when None:
    #   SELECT flow_id FROM sessions WHERE id = ? → use when arg is None
```

```python
_NOT_DELETED = ("(s.worktree_ref IS NULL OR s.worktree_ref = '' OR w.ref_key IS NULL"
                " OR (w.deleted_at IS NULL OR w.deleted_at = ''))")
```

```python
def list_sessions(path, include_deleted_worktrees=False, flow_id=0):
    # ... existing query ...
    # if flow_id: q += " AND s.flow_id = ?"; params.append(flow_id)

def list_runs(path, limit=200, include_deleted_worktrees=False, flow_id=0):
    # SELECT gains s.flow_id AS flow_id; optional " AND s.flow_id = ?"

def backfill_flow_worktree(path, flow_id, worktree_ref) -> None:
    """Re-attach NULL-worktree sessions of one flow after resume adopts a key."""
    with _connect(path) as conn:
        conn.execute("UPDATE sessions SET worktree_ref = ? WHERE flow_id = ?"
                     " AND (worktree_ref IS NULL OR worktree_ref = '')",
                     (worktree_ref, flow_id))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow_linkage.py::test_null_worktree_flow_session_persists_and_filters tests/test_save_all_sessions.py -v`
Expected: PASS (attached CASCADE + soft-delete hiding unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sessions.py tests/test_flow_linkage.py
git commit -m "Store flow_id on sessions/runs; NULL worktree pass-through [no-issue]"
```

### Task 3: flow store filters + fail_open_flows + launch_ref issue matcher

**Files:**
- Modify: `src/workagent/store_flows.py:348-370` (list_flows)
- Modify: `src/workagent/store_flows.py` (new: `fail_open_flows`, `match_launch_ref`)
- Modify: `src/workagent/store.py` (facade passthroughs)
- Test: `tests/test_flow_linkage.py`

**Interfaces:**
- Consumes: Task 1 migration (flows table unchanged)
- Produces: `list_flows(db, worktree="", status="", repo="", include_deleted_worktrees=False)`, `fail_open_flows(db, worktree_ref, reason="worktree cleaned") -> list[int]`, `match_launch_ref(db, issue_key_base) -> list[dict]` — consumed by Tasks 4–5

- [ ] **Step 1: Write the failing test**

```python
def test_fail_open_flows_matches_key_and_launch_ref(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    sf.create_template(db, name="T", label="t", steps=[{"kind": "start"}, {"kind": "cleanup"}])
    import json as _j
    a = sf.create_flow(db, template_label="t", worktree_ref="jira:X-1@r",
                       repo_snapshot={"launch_ref": "jira:X-1"})
    b = sf.create_flow(db, template_label="t", worktree_ref="",
                       repo_snapshot={"launch_ref": "https://j.example/browse/X-1"})
    c = sf.create_flow(db, template_label="t", worktree_ref="jira:X-1@r",
                       repo_snapshot={"launch_ref": "jira:X-1"})
    sf.update_flow(db, c["id"], status="done")
    failed = sf.fail_open_flows(db, "jira:X-1@r")
    assert sorted(failed) == sorted([a["id"], b["id"]])
    assert sf.get_flow(db, a["id"])["status"] == "failed"
    assert sf.list_flows(db, status="failed") != []
    assert sf.list_flows(db, repo="nope") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow_linkage.py::test_fail_open_flows_matches_key_and_launch_ref -v`
Expected: FAIL with "has no attribute 'fail_open_flows'"

- [ ] **Step 3: Write minimal implementation**

```python
def match_launch_ref(db, worktree_ref) -> list[dict]:
    """Empty-ref flows whose launch_ref parses to the key's issue identity."""
    from .refs import parse_ref, issue_key, split_issue_key
    base, _ = split_issue_key(worktree_ref)
    want = ""
    try:
        want = issue_key(parse_ref(base))
    except Exception:
        return []
    if not want:
        return []
    out = []
    for f in list_flows(db, include_deleted_worktrees=True):
        if (f.get("worktree_ref") or "") != "":
            continue
        if f.get("status") in ("failed", "done"):
            continue
        launch = ((f.get("repo_snapshot") or {}).get("launch_ref") or "")
        try:
            got = issue_key(parse_ref(launch))
        except Exception:
            continue
        if got and (got == want or got.split("@")[0] == want.split("@")[0]):
            out.append(f)
    return out

def fail_open_flows(db, worktree_ref, reason="worktree cleaned") -> list[int]:
    now_done = []
    targets = [f for f in list_flows(db, include_deleted_worktrees=True)
               if (f.get("worktree_ref") or "") == worktree_ref
               and f.get("status") in ("pending", "running", "paused")]
    targets += match_launch_ref(db, worktree_ref)
    seen = set()
    for f in targets:
        if f["id"] in seen:
            continue
        seen.add(f["id"])
        results = list(f.get("step_results") or [])
        kind = results[-1].get("kind", "start") if results else "start"
        results.append({"kind": kind, "status": "failed",
                        "detail": f"flow stopped: {reason}"})
        update_flow(db, f["id"], status="failed", step_results=results)
        now_done.append(f["id"])
    return now_done
```

`list_flows` gains `status=""` (exact match against `FLOW_STATUSES`, `ValueError` → caller maps to exit 2) and `repo=""` (substring against `repo_snapshot.repo`): AND with worktree.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow_linkage.py::test_fail_open_flows_matches_key_and_launch_ref tests/test_flows.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_flows.py src/workagent/store.py tests/test_flow_linkage.py
git commit -m "Fail open flows on cleanup incl launch_ref orphans; list filters [no-issue]"
```

### Task 4: flow run engine — owned session, per-step flow_id, failure persistence, adopt

**Files:**
- Modify: `src/workagent/cli_flow_run.py:102-356` (flow_run body, _find_resumable, flow_list, flow_show)
- Modify: `src/workagent/cli_flow_util.py:217-244` (persist_terminal_pause → flow-aware NULL path)
- Modify: `src/workagent/cli_harness.py:521-610` (record_* accept flow_id + allow_null_worktree)
- Test: `tests/test_flow_linkage.py`

**Interfaces:**
- Consumes: Tasks 2–3 (`insert_session(flow_id=)`, `backfill_flow_worktree`, `fail_open_flows` not needed here, `match_launch_ref` logic for resume)
- Produces: fixed `flow run`/`list`/`show` behavior consumed by Task 6 (API/CLI surface) + Task 5 (cleanup calls `fail_open_flows`)

- [ ] **Step 1: Write the failing test**

```python
def test_step_failure_persists_state_and_session(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401
    import workagent.cli_flow_run as m
    import workagent.store as st
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    st.create_template(name="T", label="t", steps='[{"kind": "start"}]')
    monkeypatch.setattr(m, "_resolve_ref", lambda ref: ("", {}, {"kind": "issue"}))
    def boom(*a, **k):
        raise RuntimeError("runner exploded")
    monkeypatch.setattr(m, "_run_start_step", boom)
    r = CliRunner().invoke(m.flow_app, ["run", "t", "jira:X-1", "--yes"])
    assert r.exit_code == 1, r.output
    flows = st.list_flows()
    assert flows[0]["status"] == "failed" and flows[0]["current_step"] == 0
    assert flows[0]["step_results"][-1]["status"] == "failed"
    rows = sq.list_sessions(db, include_deleted_worktrees=True)
    assert any((s.get("metadata") or {}).get("flow_id") == flows[0]["id"] for s in rows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow_linkage.py::test_step_failure_persists_state_and_session -v`
Expected: FAIL (RuntimeError propagates; flow stays running or row missing)

- [ ] **Step 3: Write minimal implementation**

```python
# flow_run, after `fid = flow["id"]`:
from . import store_sqlite as _sq
_flow_sid = _sq.insert_session(
    _sq.db_path(), worktree_ref=key or None, harness_name="",
    initiator_command=f"flow run {tpl['label']} {ref}", prompt="",
    file_path="", session_type=None,
    metadata={"flow_id": fid, "mode": "flow"}, flow_id=fid,
    allow_null_worktree=True)
try:
    _sq.insert_run(_sq.db_path(), _flow_sid, "flow",
                   ["run", tpl["label"], ref], None, flow_id=fid)
except Exception:
    pass
```

Per-step: pass `session_metadata={"flow_id": fid}` into headless runners that accept it (`_run_review_step`, `_run_sync_step`, `_run_action_step` via config merge — merge `{"flow_id": fid}` into step config session metadata, not the banned-flag surface); pause path `persist_terminal_pause(key or ref, …, fid, …)` with `allow_null_worktree=True` inside `record_headless_attempt`.

Failure path: widen `except HarnessError as e` → `except Exception as e`, mapping `typer.Exit` code (`e.exit_code if isinstance(e, typer.Exit) else 1`); on failure write the failure session + run (best effort, NULL worktree when unattached) then `raise typer.Exit(code)`.

Adopt: after any `store.update_flow(fid, worktree_ref=key)` where the key changed from `""`, call `backfill_flow_worktree(db, fid, key)`.

`_find_resumable`: after the exact `launch == ref` check, add parsed issue-identity match:

```python
from .refs import parse_ref as _pr, issue_key as _ik
try:
    if not key and launch and _ik(_pr(launch)).split("@")[0] == _ik(_pr(ref)).split("@")[0]:
        return flow
except Exception:
    pass
```

`flow list` gains `--status` (validate against `sf.FLOW_STATUSES`, `_fail(..., 2)` on bad) and `--repo`; `flow show` uses `include_deleted_worktrees=True`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow_linkage.py::test_step_failure_persists_state_and_session tests/test_cli_flow.py tests/test_cli_flow_gaps.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_flow_run.py src/workagent/cli_flow_util.py src/workagent/cli_harness.py tests/test_flow_linkage.py
git commit -m "Flow run owns session; steps stamp flow_id; failures persist [no-issue]"
```

### Task 5: cleanup coupling (soft + hard + GC + remove)

**Files:**
- Modify: `src/workagent/cli_cleanup.py` (_cleanup_one soft + hard paths)
- Modify: `src/workagent/store_links.py:135-208` (delete_worktree_row, gc_soft_deleted)
- Test: `tests/test_flow_linkage.py`

**Interfaces:**
- Consumes: Task 3's `fail_open_flows`
- Produces: cleanup-settles-flows behavior (no new API)

- [ ] **Step 1: Write the failing test**

```python
def test_cleanup_fails_linked_and_orphan_flows(tmp_path, monkeypatch, isolated_config):
    from workagent import store as st
    from workagent import store_sqlite as sq
    from workagent import cli_cleanup as cc
    db = sq.db_path()
    st.create_template(name="T", label="t", steps='[{"kind": "start"}]')
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, added_at, payload)"
                     " VALUES ('jira:X-1@r', '/tmp/wt-x1', 'b', '2026-01-01', '{}')")
    a = st.create_flow(template_label="t", worktree_ref="jira:X-1@r")
    b = st.create_flow(template_label="t", worktree_ref="",
                       repo_snapshot={"launch_ref": "jira:X-1"})
    cc._cleanup_one("jira:X-1@r", {"repo": "/tmp", "branch": "b", "worktree": "/tmp/wt-x1"},
                    True, True, False, True, squash=True, hard_delete=True)
    assert st.get_flow(a["id"], include_deleted_worktrees=True)["status"] == "failed"
    assert st.get_flow(b["id"], include_deleted_worktrees=True)["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow_linkage.py::test_cleanup_fails_linked_and_orphan_flows -v`
Expected: FAIL (flows stay paused/pending)

- [ ] **Step 3: Write minimal implementation**

```python
# cli_cleanup._cleanup_one, after soft-delete stamp AND after hard delete_worktree_row:
try:
    from . import store_flows as _sf
    from . import store_sqlite as _sq
    _sf.fail_open_flows(_sq.db_path(), key, reason="worktree cleaned")
except Exception as e:
    eprint(f"warning: flow settle failed: {e}")
```

```python
# store_links.delete_worktree_row + gc_soft_deleted per-row:
from .store_flows import fail_open_flows
try:
    fail_open_flows(path, key, reason="worktree cleaned")
except Exception:
    pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow_linkage.py::test_cleanup_fails_linked_and_orphan_flows tests/test_save_all_sessions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_cleanup.py src/workagent/store_links.py tests/test_flow_linkage.py
git commit -m "Cleanup settles linked + orphan flows as failed [no-issue]"
```

### Task 6: API + CLI surface (filters, flow_id on runs, contract, parity)

**Files:**
- Modify: `src/workagent/webapp.py:239-242` (flows_list), `366-375` (sessions_list)
- Modify: `src/workagent/web_runs.py` + `src/workagent/web_runs_routes.py` (runs `?flow=`, `flow_id` in summaries)
- Modify: `src/workagent/store.py` (facade signatures)
- Modify: `src/workagent/web_args.py` (flow list VAL_FLAGS)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_webapp_flow.py`, `tests/test_webapp_data.py`

**Interfaces:**
- Consumes: Tasks 2–3 (`list_flows(status, repo)`, `list_sessions(flow_id)`, `list_runs(flow_id)`)
- Produces: `GET /api/flows?worktree=&status=&repo=`, `GET /api/sessions?flow=`, `GET /api/runs?flow=`, `flow list --status --repo` — consumed by Task 7

- [ ] **Step 1: Write the failing test**

```python
def test_api_flows_status_repo_and_sessions_flow_filter(client):
    store.create_template(name="Full", label="full", steps='[{"kind": "start"}]')
    a = store.create_flow(template_label="full", worktree_ref="jira:IPG-1",
                          repo_snapshot={"repo": "/r/one"})
    b = store.create_flow(template_label="full", worktree_ref="",
                          repo_snapshot={"repo": "/r/two", "launch_ref": "jira:X-9"})
    store.update_flow(a["id"], status="failed")
    assert {f["id"] for f in client.get("/api/flows", params={"status": "failed"}).json()["flows"]} == {a["id"]}
    assert [f["id"] for f in client.get("/api/flows", params={"repo": "/r/two"}).json()["flows"]] == [b["id"]]
    assert client.get("/api/sessions", params={"flow": 999999}).json()["sessions"] == []
    assert client.get("/api/runs", params={"flow": 999999}).json()["runs"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_flow_linkage.py::test_api_flows_status_repo_and_sessions_flow_filter -v`
Expected: FAIL (unexpected query params / missing filter)

- [ ] **Step 3: Write minimal implementation**

```python
@app.get("/api/flows")
def flows_list(worktree: str = "", status: str = "", repo: str = "") -> dict:
    """Flow runs, optionally scoped by worktree/status/repo. Always 200."""
    return {"flows": store.list_flows(worktree=worktree, status=status, repo=repo)}

@app.get("/api/sessions")
def sessions_list(flow: int = 0) -> dict:
    ...
    rows = [... for r in sq.list_sessions(db, flow_id=int(flow or 0))]
```

Runs: `list_runs(..., flow_id=)` passthrough in `web_runs.py` + `web_runs_routes.py` query param; summaries include `flow_id` (persisted column; live `flow:`-target children resolve via target key against `launch_ref` + `worktree_ref`).

CLI `flow list`: add `--status` (validate, exit 2) + `--repo` options, mirror into `web_args.py` VAL_FLAGS + `_validate_args`; contract + inventory docs updated in the same edit.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_flow_linkage.py::test_api_flows_status_repo_and_sessions_flow_filter tests/test_webapp_data.py::test_specs_mirror_cli_flags tests/test_webapp_flow.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/webapp.py src/workagent/web_runs.py src/workagent/web_runs_routes.py src/workagent/store.py src/workagent/web_args.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_flow_linkage.py tests/test_webapp_flow.py
git commit -m "Server flow filters: flows status/repo, sessions/runs flow [no-issue]"
```

### Task 7: frontend server-driven filters (Flows / Sessions / Runs)

**Files:**
- Modify: `web/src/lib/api.ts:405-408` (flows signature), `452-458` (sessions/runs)
- Modify: `web/src/lib/queries.ts` (useFlows, useSessions, useRuns params)
- Modify: `web/src/pages/Flows.tsx:55-300` (status + repo controls, URL params)
- Modify: `web/src/pages/Sessions.tsx`, `web/src/pages/Runs.tsx`
- Test: `tsc`, `npm run build`

**Interfaces:**
- Consumes: Task 6's API query params
- Produces: filter UI (no further consumers)

- [ ] **Step 1: Write the failing test**

```typescript
// Flows.test.tsx (new, colocated): status dropdown sets ?status= and calls /api/flows?status=failed
import { render, screen, fireEvent } from "@testing-library/react"
import { Flows } from "./Flows"
// mock useFlows: assert called with (worktree, "failed", "")
```

(Keep minimal: assert the fetch URL contains `status=failed` after selecting Failed; assert Sessions flow picker calls `/api/sessions?flow=`; assert Runs `?flow=` replaces the target-string match.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- Flows 2>&1 | head -30`
Expected: FAIL (no status control / old fetch URL)

- [ ] **Step 3: Write minimal implementation**

```typescript
// api.ts
flows: (worktree?: string, status?: string, repo?: string) =>
  request<{ flows: FlowRow[] }>(`/api/flows${qs({ worktree, status, repo })}`),
sessions: (flow?: number) =>
  request<SessionsResponse>(`/api/sessions${qs({ flow })}`),
runs: (flow?: number) =>
  request<Run[]>(`/api/runs${qs({ flow })}`),
```

```typescript
// queries.ts
export function useFlows(worktree?: string, status?: string, repo?: string) {
  return useQuery({ queryKey: ["flows", worktree, status, repo],
    queryFn: () => api.flows(worktree, status, repo) });
}
```

Flows page: status `<select>` (all/pending/running/paused/failed/done) + repo text input, both synced to `useSearchParams` (`?status=`, `?repo=`); Sessions page: flow picker from `useFlows()` + `?flow=`, `useSessions(flow)`; Runs page: `?flow=` via `/api/runs?flow=`, keeping state/repo/target client filters.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx tsc --noEmit && npm run build 2>&1 | tail -5`
Expected: PASS (tsc clean, build succeeds)

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/queries.ts web/src/pages/Flows.tsx web/src/pages/Sessions.tsx web/src/pages/Runs.tsx
git commit -m "Server-driven flow filters on Flows/Sessions/Runs pages [no-issue]"
```

### Task 8: changelog, full suite, smoke

**Files:**
- Modify: `CHANGELOG.md` (gap inserts only — `PUT >N:` after last line, never range-replace; verify with `git diff` showing only `+` lines)
- Test: full `pytest`, `tsc`, CLI smokes

**Interfaces:**
- Consumes: Tasks 1–7
- Produces: verified branch

- [ ] **Step 1: Append changelog (gap insert, ≤150 chars each + [no-issue])**

```
- Flow steps persist sessions+runs with flow link; failures saved [no-issue]
- Cleanup settles linked + orphan flows as failed [no-issue]
- Flows filter by status/repo; sessions/runs filter by flow [no-issue]
```

- [ ] **Step 2: Run full verification**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: PASS (whole suite green)

- [ ] **Step 3: CLI smoke on a scratch template**

Run:
```bash
workagent flow run --help | head -20
python - <<'EOF'
# scratch: create template t-smoke, run --dry-run, run preview pause, resume --skip-steps, cleanup
EOF
```
Expected: dry-run chain prints; pause row has session; resume adopts; cleanup marks failed

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "Changelog: flow linkage fixes [no-issue]"
```

## Self-Review

- **Spec coverage:** §3 schema → Task 1; §4 lifecycle (owned session, per-step flow_id, backfill, resume match, Exception catch) → Task 4 (+ Task 2 store); §5 cleanup → Task 5; §6 queries/API → Task 6; §7 CLI → Tasks 4+6; §8 UI → Task 7; §9 edge cases → Task 4 (paused-cleaned fresh row via existing `_find_resumable` terminal skip), Task 5 (GC fails not deletes), Task 1 (metadata-for-deleted-ids skipped); §10 testing → Tasks 1–7 each carry failing-first tests.
- **Placeholder scan:** no TBD/TODO/"similar to"; every step has exact file:lines, signatures, commands, and expected output.
- **Type consistency:** `flow_id: int | None` columns; `insert_session(flow_id=None, allow_null_worktree=False)`; `insert_run(flow_id=None)`; `list_*(flow_id=0)` (0 = no filter, matching existing `""` convention); `fail_open_flows(db, worktree_ref, reason) -> list[int]`; frontend `flow?: number`.
- **Review Focus:** each line has its owning task test (Task 4: paused-cleaned resume; Task 1: deleted-id backfill skip; Task 6: unknown flow → []; Task 6: bad --status → 2; Task 4 covers last-write-wins by not adding locking — existing `update_flow` semantics unchanged).
