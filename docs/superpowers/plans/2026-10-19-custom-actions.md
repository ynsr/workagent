# Custom Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship user-defined `custom_actions` (CRUD + per-worktree harness execution) across CLI, serve, and web UI.

**Architecture:** `store_custom_actions.py` owns the table + CRUD (facade re-export via `store.py`, mirroring `store_sessions.py`); new `cli_action.py` owns the `action` subcommand and execution (reuses `_run_harness`, `resolve_any`, `release_gates` shell choke point); `web_args.py` + `webapp.py` mirror flags and add read endpoints; `Actions.tsx` + Dashboard dropdown are the frontend.

**Tech Stack:** Python (Typer, stdlib sqlite3), FastAPI serve, React + TS + TanStack Query + shadcn/popover.

**Spec:** `docs/superpowers/specs/2026-10-19-custom-actions-design.md`

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- Python files and React components/classes must not exceed 500 lines.
- CLI owns the logic; `webapp.py` `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS` mirror it — `tests/test_webapp.py::test_specs_mirror_cli_flags` fails on drift.
- New/renamed flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR.
- Timestamp columns render relative time + exact-date tooltip, uniform.
- No component may block rendering on data loading (stale-while-revalidate, overlay indicators, no layout shift).
- CHANGELOG.md entries <= 150 chars + `[issue]` suffix; gap-insert only (`PUT >N:`), verify with `git diff` (additions only).
- Session types: `SESSION_TYPES` enum extension is additive (`custom_action`); never free text.

## Review Focus

- Label collision (`Foo Bar` vs `Foo-Bar` normalize identically) → UNIQUE error must name the holder, not a bare sqlite error.
- Blank prompt + blank pre_execution → exit 2 at add AND at run (a later edit may blank both).
- `default_options` invalid JSON at add → exit 2 with position info, never a traceback.
- Pre-script failure must skip only that worktree's harness, not abort the whole fan-out.
- Dashboard dropdown must stay disabled with zero selection and must not fire with stale keys pruned from the table.

---

### Task 1: Store — `custom_actions` table + CRUD

**Files:**
- Create: `src/workagent/store_custom_actions.py`
- Modify: `src/workagent/store_sqlite.py` (SCHEMA + `_schema_current` + `init_db` chain)
- Modify: `src/workagent/store.py` (facade re-exports)
- Test: `tests/test_custom_actions.py`

**Interfaces:**
- Consumes: `store_sqlite.connect`, `store.config_dir`, `store._sqlite_path` patterns.
- Produces: `normalize_label(name) -> str`, `validate_label(label) -> str`,
  `list_actions(db) -> list[dict]`, `get_action(db, label) -> dict | None`,
  `create_action(db, *, name, label?, icon?, prompt?, pre_execution?, post_execution?, default_options?) -> dict`,
  `update_action(db, label, **fields) -> dict`, `delete_action(db, label) -> None`,
  `parse_default_options(raw: str) -> dict`. Row dict keys: `id, label, name,
  icon, prompt, pre_execution, post_execution, default_options (dict),
  created_at, modified_at`.

- [ ] **Step 1: Write the failing test**

```python
def test_create_normalizes_label(tmp_path):
    from workagent import store_custom_actions as ca
    db = tmp_path / "state.db"
    row = ca.create_action(db, name="Generate Post PRD")
    assert row["label"] == "generate_post_prd"
    assert row["icon"] == "Zap"
    assert row["created_at"] and row["modified_at"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_custom_actions.py::test_create_normalizes_label -v`
Expected: FAIL (module not defined)

- [ ] **Step 3: Write SCHEMA block + migration chain first**

In `src/workagent/store_sqlite.py`: add the `custom_actions` CREATE TABLE to
`SCHEMA` (spec §3 DDL); `_schema_current`: probe `"custom_actions" not in
tables → return False` (after the `release_diff_cache` check, ~line 524);
`init_db`: no new `_migrate_*` needed — `conn.executescript(SCHEMA)` creates
it on fresh DBs AND upgrades stale ones (CREATE TABLE IF NOT EXISTS is
already idempotent inside the retry loop). New module
`src/workagent/store_custom_actions.py` with `normalize_label`
(`re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")`, collapse runs),
`validate_label` (`^[a-z0-9_]+$` else ValueError), full CRUD via
`connect(db_path)`, `created_at`/`modified_at` UTC ISO, `default_options`
stored as JSON text / returned as dict, blank-prompt+blank-pre → ValueError,
bad JSON → ValueError, duplicate label → HarnessError naming the holder.

- [ ] **Step 4: Facade re-exports in `store.py`**

```python
from .store_custom_actions import (  # noqa: F401,E402
    create_action, delete_action, get_action, list_actions, update_action)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_custom_actions.py -v`
Expected: PASS (also add: explicit-label validation, blank rejection, bad
JSON, modified_at bump, idempotent init_db on stale DB, label-collision
message — same file, same style).

- [ ] **Step 6: Commit**

```bash
git add src/workagent/store_custom_actions.py src/workagent/store_sqlite.py src/workagent/store.py tests/test_custom_actions.py
git commit -m "feat: custom_actions table + store CRUD [no-issue]"
```

### Task 2: Sessions — `custom_action` session type

**Files:**
- Modify: `src/workagent/store_sessions.py`
- Test: `tests/test_custom_actions.py` (append)

**Interfaces:**
- Consumes: Task 1 (nothing directly — parallel-safe).
- Produces: extended `SESSION_TYPES`, `derive_session_type("action") ==
  "custom_action"`, `_backfill_session_type("action run ...")`.

- [ ] **Step 1: Write the failing test**

```python
def test_derive_session_type_action():
    from workagent.store_sessions import derive_session_type
    assert derive_session_type("action") == "custom_action"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_custom_actions.py::test_derive_session_type_action -v`
Expected: FAIL (`None == 'custom_action'`)

- [ ] **Step 3: Minimal implementation**

`SESSION_TYPES += ("custom_action",)`; `derive_session_type`: `if c ==
"action": return "custom_action"`; `_backfill_session_type`: add `"action"`
to the `first in (...)` tuple. Session dir segment becomes
`sessions/omp/<branch>/custom_action/` automatically via existing
`session_file_path`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_custom_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sessions.py tests/test_custom_actions.py
git commit -m "feat: custom_action session type [no-issue]"
```

### Task 3: CLI — `action add/list/show/delete` + `run` execution

**Files:**
- Create: `src/workagent/cli_action.py` (≤500 lines)
- Modify: `src/workagent/cli.py` (import for side effect: `from . import cli_action  # noqa: F401`)
- Test: `tests/test_cli_action.py`

**Interfaces:**
- Consumes: Task 1 CRUD, Task 2 session type, `worktrees.resolve_any`,
  `cli_harness._run_harness` + `append_extra_prompt`, `cli_core`
  (`app`, `_fail`, `_print_result`, `_print_rows`, `eprint`,
  `EXIT_USAGE`, `_catch_harness_errors`), `release_gates.run_repo_command`
  (shell choke point), `store.load_links`.
- Produces: `workagent action add|list|show|run|delete`; `build_action_prompt(action, entry, links) -> str` (exported for tests).

- [ ] **Step 1: Write the failing test**

```python
def test_build_action_prompt_appends_child_paths():
    from workagent.cli_action import build_action_prompt
    action = {"prompt": "Do the thing."}
    entry = {"worktree": "/wt/parent", "branch": "feat/x"}
    links = {"p": {"worktree": "/wt/parent", "branch": "feat/x"},
             "c": {"worktree": "/wt/child", "branch": "feat/x", "parent_key_ref": "p"}}
    out = build_action_prompt(action, entry, "p", links)
    assert "Do the thing." in out
    assert "/wt/parent" in out and "/wt/child" in out
    assert out.index("/wt/parent") < out.index("/wt/child")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_action.py::test_build_action_prompt_appends_child_paths -v`
Expected: FAIL (module not defined)

- [ ] **Step 3: Write minimal implementation**

New `src/workagent/cli_action.py`: five `@app.command("action ...")`
functions per spec §5 (add flags `--name/--label/--icon/--prompt/
--pre-execution/--post-execution/--default-options`; run flags
`--launch/--terminal/--harness/--extra-prompt/--env-file/--session-file/
--dry-run/--yes/--json`). `build_action_prompt` appends:

```
Related worktrees:
- /wt/parent
- /wt/child
```

(target first, then children with `parent_key_ref == key`, sorted by path).
`run`: `resolve_any` → skip deactivated/invalid with stderr note →
pre-script via `run_repo_command("<label>", "pre-execution", script,
worktree, 600, env_file)` (catch HarnessError → note + `continue`) →
empty prompt + pre-only → scripts-only result (no `_run_harness`) →
`_run_harness(harness, prompt, worktree, repo, no_tty=launch-or-terminal,
launch=bool, result={"command": "action", ...}, ...)` →
post-script on success (failure → exit 1). Mode precedence: `--launch` /
`--terminal` flags > `default_options.run_mode` > preview; both flags →
exit 2. `action` needs NO `--all` flag (single ref per invocation).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_action.py tests/test_custom_actions.py -v`
Expected: PASS (also add: unknown-label exit 2, XOR exit 2, dry-run no-op,
pre-fail skips harness, blank add rejection — same file).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_action.py src/workagent/cli.py tests/test_cli_action.py
git commit -m "feat: workagent action CLI (CRUD + run) [no-issue]"
```

### Task 4: Web — flag mirror + read endpoints + contract docs

**Files:**
- Modify: `src/workagent/web_args.py`
- Modify: `src/workagent/webapp.py`
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_webapp.py` (append)

**Interfaces:**
- Consumes: Task 3 CLI flags (exact names).
- Produces: `GET /api/actions`, `GET /api/actions/{label}`.

- [ ] **Step 1: Write the failing test**

```python
def test_actions_list_empty():
    from workagent.webapp import create_app
    from pathlib import Path
    app = create_app(Path("/tmp"), "127.0.0.1", 3344, [])
    from fastapi.testclient import TestClient
    c = TestClient(app)
    r = c.get("/api/actions")
    assert r.status_code == 200 and r.json() == {"actions": []}
```

(Check existing `test_webapp.py` for its TestClient convention first and
match it — do not invent a second pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp.py::test_actions_list_empty -v`
Expected: FAIL (404, no route)

- [ ] **Step 3: Minimal implementation**

`web_args.py`: `SPECS["action"] = {"confirm": True, "force": True,
"key": lambda args: f"action:{_first_positional(args, VAL_FLAGS['action run'])}:{_first_positional(args[1:], VAL_FLAGS['action run'])}" if args[:1] == ["run"] else "config"}`;
`SUBCOMMANDS["action"] = {"add", "list", "show", "run", "delete"}`;
`BOOL_FLAGS["action add"] = ("--json",)`, `["action list"]`, `["action
show"]`, `["action delete"] = ("--yes", "--json")`, `["action run"] =
("--launch", "--terminal", "--dry-run", "--yes", "--json")`;
`VAL_FLAGS["action add"] = ("--name", "--label", "--icon", "--prompt",
"--pre-execution", "--post-execution", "--default-options")`,
`["action run"] = ("--harness", "--extra-prompt", "--env-file",
"--session-file")`. `webapp.py`: `@app.get("/api/actions")` →
`{"actions": store.list_actions()}` (empty table → `[]`, always 200);
`@app.get("/api/actions/{label}")` → row or 404. Docs: one row per
subcommand in FEATURE_INVENTORY.md + endpoint entries in API_CONTRACT.md.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webapp.py tests/test_cli.py -v`
Expected: PASS (mirror test green; also add detail + 404 tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py src/workagent/webapp.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_webapp.py
git commit -m "feat: serve passthrough + /api/actions endpoints [no-issue]"
```

### Task 5: Frontend — Actions page + nav + API types

**Files:**
- Create: `web/src/pages/Actions.tsx` (≤500 lines)
- Create: `web/src/components/ActionIcon.tsx` (lucide map + Zap fallback)
- Modify: `web/src/App.tsx`, `web/src/components/layout/AppLayout.tsx`,
  `web/src/lib/api.ts`, `web/src/lib/queries.ts`
- Test: extend parity frontend test per checklist (find existing pattern first)

**Interfaces:**
- Consumes: Task 4 endpoints.
- Produces: `useActions()`, `api.actions()`, `api.action(label)`, `/actions` route.

- [ ] **Step 1: Write the failing check**

Render test (or `tsc -b` + route-presence grep if no frontend test
harness exists): `/actions` route renders the actions table. Follow the
existing Repos/Trackers page test pattern — do not invent a new one.

- [ ] **Step 2: Run to verify it fails**

Run: existing frontend check command
Expected: FAIL (no route)

- [ ] **Step 3: Minimal implementation**

`lib/api.ts`: `Action` type (id/label/name/icon/prompt/pre_execution/
post_execution/default_options/created_at/modified_at) +
`actions()`/`action(label)`. `queries.ts`: `queryKeys.actions =
["actions"]`, `useActions()`, `useAction(label)`. `Actions.tsx`: table +
Add/detail/edit/delete modals (mirror `Repos.tsx` run-based mutations:
`createRun({command: "action", args: [...]})`), relative timestamps +
tooltip (reuse `format.ts` helper — check name first), SWR render-cached +
overlay. `ActionIcon.tsx`: `{ [name]: LucideIcon }` map + `Zap` fallback.
Nav: AppLayout `NAV_ITEMS += { to: "/actions", label: "Actions", icon:
Zap }`, BottomNav `grid-cols-7 → grid-cols-8`; App.tsx route.

- [ ] **Step 4: Verify**

Run: `cd web && npm run build` (or repo's frontend check)
Expected: PASS, no TS errors

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Actions.tsx web/src/components/ActionIcon.tsx web/src/App.tsx web/src/components/layout/AppLayout.tsx web/src/lib/api.ts web/src/lib/queries.ts
git commit -m "feat: Actions management page + nav [no-issue]"
```

### Task 6: Frontend — Dashboard Actions dropdown

**Files:**
- Modify: `web/src/pages/Dashboard.tsx` (≤500-line check first)
- Test: control-presence (disabled-when-empty)

**Interfaces:**
- Consumes: Task 5 hooks + `selected` set (existing).
- Produces: `Actions` dropdown in PageHeader actions.

- [ ] **Step 1: Write the failing check**

Dropdown exists, `disabled={selCount === 0}`, lists action names. Match
the existing Dashboard test pattern.

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL (no dropdown)

- [ ] **Step 3: Minimal implementation**

Reuse existing `popover.tsx` (no new dep): `Actions` button + popover list
(name + ActionIcon per row); click → `confirm()` with
`scopeDetail("Every active linked worktree")` → loop `[...selected].sort()`
→ `createRun({command: "action", args: ["run", label, key], confirm:
true})` → per-key toast with View (same shape as `handleSyncAll`). Prune
stale keys via the existing `useEffect` (no new logic). Mode override: a
`CheckRow` trio (Preview/Headless/Terminal radio — reuse CheckRow or
native radio) appended only when the action's `default_options.run_mode`
is preview? NO — simpler: no mode control in v1; the stored default
applies, CLI overrides. (Keeps the dropdown to one click + confirm.)

- [ ] **Step 4: Verify**

Run: `cd web && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Dashboard.tsx
git commit -m "feat: Dashboard Actions dropdown (selection fan-out) [no-issue]"
```

### Task 7: Verify + changelog + reinstall

**Files:**
- Modify: `CHANGELOG.md` (gap insert under `## Unreleased`)

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q`
Expected: PASS (whole suite, not a subset)

- [ ] **Step 2: Smoke the CLI end to end**

Run: `workagent action add --name "Smoke Test" --prompt "hi" && workagent action list && workagent action show smoke_test && workagent action delete smoke_test --yes`
Expected: add/list/show/delete all succeed; label `smoke_test`

- [ ] **Step 3: Changelog + diff check**

Gap-insert one line (≤150 chars + `[no-issue]`) under `## Unreleased`,
then `git diff CHANGELOG.md` shows only `+` lines.

- [ ] **Step 4: Commit, push, reinstall**

```bash
git add CHANGELOG.md && git commit -m "docs: changelog custom actions [no-issue]"
./install.sh
```
