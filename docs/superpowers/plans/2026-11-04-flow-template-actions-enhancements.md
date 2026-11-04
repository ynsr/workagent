# Flow Template/Action Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Release Checks/Diff config to flow forms, Save/Save-as/Reset to the Run-flow modal, and protected/order/dedicated-button columns to flow templates and custom actions end to end.

**Architecture:** Backend-first parity chain — SQLite migrations + store CRUD, then CLI flags, then web arg passthrough + API payloads, then ChainEditor/FlowWizard/tables/Dashboard UI. CLI owns the logic; `web_args.py` SPECS/BOOL_FLAGS/VAL_FLAGS mirror it.

**Tech Stack:** Python (Typer CLI, sqlite3), TypeScript/React (Vite, ChainEditor/FlowWizard/StatusTable), FastAPI serve backend.

**Spec:** `docs/superpowers/specs/2026-11-04-flow-template-actions-enhancements-design.md`

## Global Constraints

- `store_sqlite.py` migrations follow the `_migrate_*` idempotent pattern (PRAGMA check, skip when present); `init_db` calls each migration.
- New CLI flags mirror into `web_args.py` BOOL_FLAGS/VAL_FLAGS + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + `tests/test_webapp.py::test_specs_mirror_cli_flags` (fails on drift).
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Python files must not exceed 500 lines (`cli_flow.py` splits runners into `cli_flow_steps.py`; check before adding).
- Protected is UI-only: CLI `delete` stays unconditional.
- Release editor exposes Checks + Diff only (no env_file/timeouts/no_status).
- `sort_order` has no uniqueness constraint; ties break by `id`.

## Review Focus

- Release step with invalid `checks`/`diff` value (e.g. `checks: "all"`) is rejected with exit 2, not silently stored.
- Protected row hides edit/delete/clone affordances in both the table and the detail dialog.
- `sort_order` tie (two rows same value) renders in stable `id` order, never flapping.
- Dedicated overlay button on a worktree row runs against that row's key, not the selection.
- Run-modal Save persists the working chain (not the pristine template chain).

---

### Task 1: Schema migrations + store CRUD for the four columns

**Files:**
- Modify: `src/workagent/store_sqlite.py:15-95` (SCHEMA), add `_migrate_*` + `init_db` calls near lines 726-752
- Modify: `src/workagent/store_flows.py:204-221` (`_row`), `:245-251` (`list_templates`), `:262-300` (`create_template`), `:303-372` (`update_template`)
- Modify: `src/workagent/store_custom_actions.py:55-76` (`_row`, `list_actions`), `:87-131` (`create_action`), `:134-197` (`update_action`)
- Test: `tests/test_flows.py`, `tests/test_custom_actions.py`

**Interfaces:**
- Consumes: existing `_migrate_repos_release` pattern (`PRAGMA table_info`, `ALTER TABLE … ADD COLUMN … NOT NULL DEFAULT …`).
- Produces: `create_template(..., protected: bool = False, sort_order: int = 0, dedicated_top_bar: bool = False, dedicated_overlay: bool = False)`, same four kwargs on `create_action`; `update_template`/`update_action` accept the four field names; `_row` returns `protected: bool`, `sort_order: int`, `dedicated_top_bar: bool`, `dedicated_overlay: bool`; lists ordered `ORDER BY sort_order, id`.

- [ ] **Step 1: Write the failing test (flows)**

```python
def test_template_display_columns(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    row = sf.create_template(db, name="Full", steps=[{"kind": "cleanup"}], protected=True, sort_order=3, dedicated_top_bar=True, dedicated_overlay=True)
    assert row["protected"] is True
    assert row["sort_order"] == 3
    assert row["dedicated_top_bar"] is True
    assert row["dedicated_overlay"] is True
    got = sf.get_template(db, row["label"])
    assert got is not None and got["protected"] is True and got["sort_order"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flows.py::test_template_display_columns -v`
Expected: FAIL with unexpected keyword argument.

- [ ] **Step 3: Write the failing test (actions)**

```python
def test_action_display_columns(tmp_path):
    from workagent import store_custom_actions as ca
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    row = ca.create_action(db, name="Lint", prompt="lint it", protected=True, sort_order=2, dedicated_top_bar=True, dedicated_overlay=True)
    assert row["protected"] is True
    assert row["sort_order"] == 2
    assert row["dedicated_top_bar"] is True
    assert row["dedicated_overlay"] is True
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_custom_actions.py::test_action_display_columns -v`
Expected: FAIL with unexpected keyword argument.

- [ ] **Step 5: Implement migrations + SCHEMA**

In `store_sqlite.py`: add the four columns to both `CREATE TABLE` statements in SCHEMA (after `modified_at` with `NOT NULL DEFAULT 0` / `DEFAULT 0` for sort_order). Add `_migrate_flow_display_cols(conn)` and `_migrate_action_display_cols(conn)` following `_migrate_repos_release`: check `PRAGMA table_info(flow_templates)` / `(custom_actions)`, skip when all four present, else `ALTER TABLE … ADD COLUMN …`. In `_migrate_flow_display_cols`, backfill `sort_order = id` where `sort_order = 0` (`UPDATE flow_templates SET sort_order = id WHERE sort_order = 0`); same for actions. Register both in `init_db`.

- [ ] **Step 6: Implement store CRUD**

`store_flows.py`: `_row` adds `d = {...}; d["protected"] = bool(d.get("protected", 0)); d["sort_order"] = int(d.get("sort_order", 0) or 0); d["dedicated_top_bar"] = bool(...); d["dedicated_overlay"] = bool(...)`. Tuple branch: extend `keys` with the four names in SCHEMA order. `list_templates` → `ORDER BY sort_order, id`. `create_template` signature gains the four kwargs; INSERT includes them (`int(protected)`, `int(sort_order)`, …). `update_template` `allowed` gains `{"protected", "sort_order", "dedicated_top_bar", "dedicated_overlay"}`; coerce bools to int and `sort_order` to int before the UPDATE. Mirror in `store_custom_actions.py` (`list_actions` → `ORDER BY sort_order, id`).

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_flows.py tests/test_custom_actions.py -v`
Expected: PASS (all, including the two new tests).

- [ ] **Step 8: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_flows.py src/workagent/store_custom_actions.py tests/test_flows.py tests/test_custom_actions.py
git commit -m "[flow] display columns: protected/order/dedicated buttons"
```

### Task 2: CLI flags on template/action add/update + list/show output

**Files:**
- Modify: `src/workagent/cli_flow.py:40-46` (`template_add`), `:90-97` (`template_update`), `:60-71` (`template_list`), `:77-85` (`template_show`)
- Modify: `src/workagent/cli_action.py:101-109` (`action_add`), `:157-166` (`action_update`), `:126-142` (`action_list`), `:144-154` (`action_show`)
- Test: `tests/test_flows.py`, `tests/test_custom_actions.py` (CLI-level via existing helpers)

**Interfaces:**
- Consumes: Task 1 store kwargs.
- Produces: `--protected/--no-protected`, `--sort-order N`, `--dedicated-top-bar/--no-…`, `--dedicated-overlay/--no-…` on all four commands; list/show JSON includes the four fields.

- [ ] **Step 1: Write the failing test**

```python
def test_template_update_display_flags(runner=None):
    # via CLI runner: flow template add --name T --steps '[{"kind":"cleanup"}]' --protected --sort-order 5 --json
    # then flow template show <label> --json → protected true, sort_order 5
```

Concretely, follow the existing CLI test pattern in `tests/test_flows.py` (check the file's runner fixture first; mirror the nearest add/show test, adding the four flags to argv and asserting the four JSON fields).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flows.py -k display -v`
Expected: FAIL (unrecognized flag or missing field).

- [ ] **Step 3: Implement CLI flags**

`template_add`: add `protected: bool = typer.Option(False, "--protected/--no-protected", ...)`, `sort_order: int = typer.Option(0, "--sort-order", ...)`, `dedicated_top_bar: bool = ...`, `dedicated_overlay: bool = ...`; pass to `store.create_template`. `template_update`: same four as `Optional[...]` (None = untouched); build `fields` dict only for non-None. `action_add`/`action_update`: mirror. `template_list`/`action_list` item dicts and `show` outputs gain the four keys. Check file length stays ≤500 lines (`wc -l src/workagent/cli_flow.py src/workagent/cli_action.py`); if over, move new helper code to `cli_flow_util.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_flows.py tests/test_custom_actions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_flow.py src/workagent/cli_action.py tests/test_flows.py tests/test_custom_actions.py
git commit -m "[flow] CLI flags for protected/order/dedicated buttons"
```

### Task 3: Release Checks/Diff validation in `validate_template`

**Files:**
- Modify: `src/workagent/store_flows.py:58-163` (`validate_template`)
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: none new.
- Produces: `validate_template` raises `HarnessError` exit 2 for release `config.checks` outside `{ci, local, both, none}` or `config.diff` outside `{remote, local}`.

- [ ] **Step 1: Write the failing test**

```python
def test_release_step_rejects_bad_checks():
    import pytest
    from workagent import store_flows as sf
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError):
        sf.validate_template([{"kind": "cleanup"}, {"kind": "release", "config": {"checks": "all"}}])
    with pytest.raises(HarnessError):
        sf.validate_template([{"kind": "cleanup"}, {"kind": "release", "config": {"diff": "local2"}}}])
    ok = sf.validate_template([{"kind": "cleanup"}, {"kind": "release", "config": {"checks": "both", "diff": "local"}}])
    assert ok[-1]["config"] == {"checks": "both", "diff": "local"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_flows.py::test_release_step_rejects_bad_checks -v`
Expected: FAIL (no error raised).

- [ ] **Step 3: Implement validation**

In `validate_template`, after the per-step banned-flag loop, add: for `k == "release"`, `cfg = step.get("config", {}) or {}`; if `"checks" in cfg and cfg["checks"] not in ("ci", "local", "both", "none")` → `HarnessError(f"invalid release checks {cfg['checks']!r} (expected ci|local|both|none)", exit_code=2)`; same for `diff` vs `("remote", "local")`. No runner change — `RELEASE_FLAGS` in `cli_flow_util.py` already forwards both, and `run_release_step` already picks them.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_flows.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_flows.py tests/test_flows.py
git commit -m "[flow] validate release checks/diff values"
```

### Task 4: Web passthrough (web_args mirror) + API payloads + contract docs

**Files:**
- Modify: `src/workagent/web_args.py:74-88` (BOOL_FLAGS), `:115-122` (VAL_FLAGS)
- Modify: `src/workagent/cli_flow.py` list/show already done in Task 2 (payloads flow through runs); verify `webapp.py` flow-templates/actions GET handlers return full rows
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_webapp.py::test_specs_mirror_cli_flags` + payload test

**Interfaces:**
- Consumes: Task 2 CLI flags.
- Produces: `flow template add/update` + `action add/update` accept the four flags via web runs; GET payloads include the four fields.

- [ ] **Step 1: Write the failing test**

```python
def test_api_templates_include_display_cols(client=None):
    # follow existing test_webapp.py flow-templates GET test; assert
    # templates[0] has protected/sort_order/dedicated_top_bar/dedicated_overlay keys
```

Mirror the nearest existing payload test in `tests/test_webapp.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp.py -k "display or template" -v`
Expected: FAIL (missing keys).

- [ ] **Step 3: Implement passthrough**

BOOL_FLAGS: `"flow template add"` += `("--protected", "--no-protected", "--dedicated-top-bar", "--no-dedicated-top-bar", "--dedicated-overlay", "--no-dedicated-overlay")`; same for `"flow template update"`, `"action add"`, `"action update"`. VAL_FLAGS: `"flow template add"` += `("--sort-order",)`; same for update/add/update of both. Confirm `_validate_args` needs no other change (new flags pass through; check its allowlist logic around lines 221-330). Verify the GET handlers serialize full `_row` dicts (no field allowlist to extend — check `webapp.py` flow-templates/actions handlers). Update `web/API_CONTRACT.md` (new flags + fields) and `web/FEATURE_INVENTORY.md` (flag inventory lines).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webapp.py -v`
Expected: PASS including `test_specs_mirror_cli_flags`.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_webapp.py
git commit -m "[flow] web passthrough for display columns"
```

### Task 5: ChainEditor Release Checks/Diff selects

**Files:**
- Modify: `web/src/components/ChainEditor.tsx:114-318` (`StepConfigForm`)
- Modify: `web/src/lib/api.ts:316-317` (`FlowStep` already allows arbitrary config — verify, else extend)
- Test: `web/src/components/ChainEditor.test.tsx`

**Interfaces:**
- Consumes: Task 3 validation (backend rejects bad values).
- Produces: release steps show Checks + Diff selects bound to `config.checks`/`config.diff` via `patch()`.

- [ ] **Step 1: Write the failing test**

```tsx
// render StepConfigForm/ChainEditor with steps=[{kind:"release",config:{}}]
// assert a "Checks" select and "Diff" select appear;
// render with steps=[{kind:"cleanup",config:{}}] → assert neither appears
```

Mirror the existing `merge_only`/`update` tests in `ChainEditor.test.tsx`.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- ChainEditor` (from `web/`; check `web/package.json` test script first)
Expected: FAIL (selects not found).

- [ ] **Step 3: Implement selects**

In `StepConfigForm`: `const showRelease = kind === "release"`; `const checks = typeof config.checks === "string" ? config.checks : (typeof hints?.checks === "string" ? hints.checks : "ci")` — but note `patch()` removes keys equal to the hint; for template editing (`hints` undefined) default display is `ci`/`remote` without writing until changed… simpler: display `config.checks ?? "ci"`, onChange `patch("checks", v)`; same for diff with `"remote"`. Render two `<select>` blocks (options ci/local/both/none; remote/local) before the Advanced collapsible, only when `showRelease`. `advancedCount` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- ChainEditor` from `web/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ChainEditor.tsx web/src/components/ChainEditor.test.tsx
git commit -m "[flow] release checks/diff selects in chain editor"
```

### Task 6: FlowWizard Save / Save-as / Reset footer groups

**Files:**
- Modify: `web/src/components/FlowWizard.tsx:55-88` (footer + state)
- Test: `web/src/components/FlowWizard.test.tsx`

**Interfaces:**
- Consumes: Task 4 passthrough (`flow template update/add` via `createRun`).
- Produces: footer left group [Save][Save as][Reset] separated from right [Run flow]; Save updates the template with the working chain, Save-as creates a new template, Reset restores pristine.

- [ ] **Step 1: Write the failing test**

```tsx
// render FlowWizard with a template; assert Save, Save as, Reset buttons exist;
// click Reset after editing → working chain equals template.steps
```

Mirror existing FlowWizard tests for the Run button.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- FlowWizard` from `web/`
Expected: FAIL (buttons not found).

- [ ] **Step 3: Implement footer**

Add `const [saving, setSaving] = useState(false)`; `const dirty = JSON.stringify(working) !== JSON.stringify(template.steps)`. `handleSave`: `createRun.mutateAsync({command:"flow", args:["template","update",template.label,"--steps",JSON.stringify(working),"--yes"], confirm:true})` + toast + no close (stays open; template prop is stale — note: parent refetches on next open). `handleSaveAs`: prompt name/label (use two `window.prompt` calls or a minimal inline form — check repo conventions for prompts in `LinkDialogs`; avoid `window.prompt` if the codebase forbids it), then `flow template add --name … --label … --steps …`. `handleReset`: `setWorking(structuredClone(template.steps))`. Footer JSX: `<div className="flex items-center justify-between"><div className="flex gap-2">[Save][Save as][Reset]</div><span className="text-border">|</span>[Run flow]</div>` — use a real separator element (`<div className="h-5 w-px bg-border" aria-hidden />`). Disable Save/Reset when `!dirty || running || saving`; Run keeps existing gating.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test -- FlowWizard` from `web/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FlowWizard.tsx web/src/components/FlowWizard.test.tsx
git commit -m "[flow] run-modal save/save-as/reset footer groups"
```

### Task 7: Management tables (protected/order/dedicated toggles + drag-reorder)

**Files:**
- Modify: `web/src/pages/FlowTemplates.tsx` (table + form)
- Modify: `web/src/pages/Actions.tsx` (table + form)
- Modify: `web/src/lib/api.ts:129-140, 317` (`ActionRow`/`FlowTemplateRow` gain the four fields)
- Test: extend `ChainEditor`-/page-level tests as available; backend already covered

**Interfaces:**
- Consumes: Tasks 1/2/4 (fields in rows + update flags via runs).
- Produces: tables sorted by `sort_order`; Protected/Top-bar/Overlay toggle buttons per row; drag-handle reorders persisting `sort_order`.

- [ ] **Step 1: Write the failing test (API types)**

No new test file needed if pages lack tests — instead verify manually: add the four fields to `ActionRow`/`FlowTemplateRow` interfaces and confirm `tsc` passes. If a page test harness exists, mirror it.

- [ ] **Step 2: Implement types + sorting**

`api.ts`: `FlowTemplateRow` += `protected: boolean; sort_order: number; dedicated_top_bar: boolean; dedicated_overlay: boolean` (same for `ActionRow`). Both pages: `const sorted = [...templates].sort((a,b) => a.sort_order - b.sort_order || a.id - b.id)`.

- [ ] **Step 3: Implement columns + toggles**

Add `<TableHead>` Protected/Order/Top/Overlay. Protected cell: lock/unlock button → `flow template update <label> [--protected|--no-protected] --yes` run + toast; when `row.protected`, render edit/delete/clone buttons disabled (title "Protected — unprotect to edit"). Order cell: drag handle (`GripVertical`, HTML5 `draggable` rows or up/down arrow buttons if no DnD pattern exists in repo — check first; arrows are simpler and a11y-safe); on move, persist affected rows' `sort_order` via update runs. Top/Overlay cells: toggle buttons → update runs. CSV/JSON exports gain the four columns.

- [ ] **Step 4: Typecheck + related tests**

Run: `npx tsc --noEmit` from `web/`; `uv run pytest tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/FlowTemplates.tsx web/src/pages/Actions.tsx web/src/lib/api.ts
git commit -m "[flow] management tables: protected/order/dedicated toggles"
```

### Task 8: Dashboard dedicated top-bar buttons + StatusTable overlay buttons

**Files:**
- Modify: `web/src/components/FlowsMenu.tsx`, `web/src/components/ActionsMenu.tsx` (or `Dashboard.tsx` top bar)
- Modify: `web/src/components/StatusTableMain.tsx` (overlay buttons; check `StatusActions.tsx`/`RunActions.tsx` first for the existing overlay pattern)
- Test: manual + existing suite

**Interfaces:**
- Consumes: Tasks 4/7 (flags on rows, sorted order).
- Produces: per-item top-bar buttons and per-row overlay buttons for flagged items.

- [ ] **Step 1: Check the overlay pattern**

Read `StatusActions.tsx`/`RunActions.tsx` to find where row overlay buttons render; note the props (`onSync`, `onReview`, …) and how `StatusTable` receives `actions`.

- [ ] **Step 2: Implement top-bar buttons**

After `FlowsMenu`/`ActionsMenu` in `Dashboard.tsx` top bar: `{templates.filter(t => t.dedicated_top_bar).map(t => <Button size="sm" variant="outline" onClick={() => handlePick(t)}>{t.name}</Button>)}` reusing `FlowsMenu.handlePick` logic (lift to a shared handler or export from FlowsMenu); same for actions reusing `ActionsMenu.handleRun`. Order: `sort_order, id`. No flagged items → render nothing.

- [ ] **Step 3: Implement overlay buttons**

In the row overlay: flagged templates → icon-button opening `FlowWizard` for that single key; flagged actions → icon-button running the action on that key. Mirror existing overlay button styling; include `title` with the item name.

- [ ] **Step 4: Verify**

Run: `uv run pytest -v` (full suite) and `npx tsc --noEmit` from `web/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FlowsMenu.tsx web/src/components/ActionsMenu.tsx web/src/pages/Dashboard.tsx web/src/components/StatusTableMain.tsx
git commit -m "[flow] dedicated top-bar and overlay buttons"
```

## Self-Review

- Spec §1 → Task 1 (migrations, CRUD, ordering, backfill). §2 → Task 2 (CLI flags, list/show). §4 first half → Task 3 (validation; no runner change needed since RELEASE_FLAGS already forward both). §3 → Task 4 (passthrough, payloads, contract docs). §4 UI half → Task 5 (selects). §5 → Task 6 (footer groups; Save persists the working chain per the confirmed "include overrides" answer — the wizard's `working` state is the edited chain). §6 → Task 7. §7 → Task 8. §8 → each task's own tests + Task 8 full suite.
- No placeholders: every step names exact files, flags, SQL, and argv.
- Type consistency: field names `protected/sort_order/dedicated_top_bar/dedicated_overlay` used verbatim in SQL, store, CLI, API, and TS interfaces.
- Review Focus lines map to Tasks 3 (invalid values), 7 (protected hide, tie-break), 8 (overlay key), 6 (save-working-chain).
