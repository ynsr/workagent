# Flow Template Clone + Compose + Ad-hoc Steps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add template clone/compose commands and launch-time ad-hoc step editing to flow templates, with lineage tracking and effective-step persistence.

**Architecture:** Flatten-and-lineage: clone/compose produce ordinary independent template rows with a `parent_labels` audit trail (no live references); `flow run --steps` accepts a full launch-time chain validated through the existing `validate_template` gate and persisted as `flows.effective_steps` for resume/show.

**Tech Stack:** Python (Typer CLI, sqlite3 store), React + TypeScript (ChainEditor/FlowWizard/FlowTemplates), FastAPI passthrough (`web_args.py`/`webapp.py` run children, no new endpoints needed).

**Spec:** `docs/superpowers/specs/2026-10-31-flow-template-clone-compose-adhoc-design.md`

## Global Constraints

- Python files and React components/classes must not exceed 500 lines.
- stdout carries ONLY data; every log/progress/confirmation line goes to stderr.
- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- Parity checklist: CLI flag → web arg passthrough → API_CONTRACT.md entry → frontend control → both test files, in the same PR.
- `tests/test_webapp.py::test_specs_mirror_cli_flags` must stay green.
- Full test suite (`cd /home/bs/projects/personal/harness && uv run pytest -v`) green before done.
- CHANGELOG.md: one line per change, ≤150 chars, non-technical wording, ID at end; append-only.
- Branch `chore/flow-template-enhancements` already created; work there.

## Review Focus

- Compose of two full e2e templates must not fail on duplicate `start` — keep-first-start collapse must drop the second `start` with a visible note, not an error.
- `--steps` with a banned flag (`then-fix-comments`, cleanup hard-delete) must fail fast before any flow row exists, same as `--step-config` does today.
- Resume of a flow launched with `--steps` must continue from the effective chain, not silently revert to template steps.
- `flow show` on a pre-migration flow row (no `effective_steps` column value) must fall back to template steps without crashing.
- Launch wizard with a working copy that violates ordering (e.g. release not last) must disable Run with the same message text the template editor shows.

---

## File Structure

- `src/workagent/store_sqlite.py` — DDL: add `flow_templates.parent_labels`, `flows.effective_steps`; extend `_schema_current` probes.
- `src/workagent/store_flows.py` — `_row`/`_flow_row` parse new columns with pre-migration defaults; `create_template`/`update_template` accept `parent_labels`; `create_flow`/`update_flow` accept `effective_steps`; new pure helpers `collapse_first_start(steps)` and `compose_chains(sources)` (index-mapped errors).
- `src/workagent/cli_flow.py` — `template clone`, `template compose` commands.
- `src/workagent/cli_flow_run.py` — `flow run --steps` option; effective-chain resolution; persist `effective_steps`; resume/show read effective-first.
- `src/workagent/store_backup.py` — round-trip `parent_labels` on export/import (defaults `[]` for old files).
- `src/workagent/web_args.py` — SPECS/BOOL_FLAGS/VAL_FLAGS for `flow template clone/compose`, `flow run --steps`.
- `web/src/lib/api.ts` — `FlowTemplateRow.parent_labels?`, `FlowRow.effective_steps?`.
- `web/src/pages/FlowTemplates.tsx` — Clone button, compose multi-select.
- `web/src/components/FlowWizard.tsx` + `ChainEditor.tsx` — launch-mode working copy with add/remove/reorder, `--steps` passthrough on confirm.
- `web/src/pages/Flows.tsx` — effective-steps display with "modified at launch" badge.
- `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md` — parity doc entries.
- `tests/test_flows.py`, `tests/test_cli_flow.py`, `tests/test_webapp.py`, `tests/test_cli_backup.py` — coverage per task.
- `CHANGELOG.md` — append-only lines.

---

### Task 1: Schema + store lineage columns

**Files:**
- Modify: `src/workagent/store_sqlite.py:71-85` (DDL + `_schema_current`)
- Modify: `src/workagent/store_flows.py:156-183` (`_row`, `_flow_row`)
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: existing `SCHEMA`, `_schema_current`, `_row`, `_flow_row`.
- Produces: `_row` returns `parent_labels: list[str]` (default `[]`); `_flow_row` returns `effective_steps: list[dict] | None` (default `None`).

- [ ] **Step 1: Write the failing test**

```python
def test_template_row_parses_parent_labels_default(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    row = sf.create_template(name="T", label="t", steps='[{"kind": "sync"}]')
    assert row["parent_labels"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_flows.py::test_template_row_parses_parent_labels_default -v`
Expected: FAIL with KeyError or AssertionError (`parent_labels` missing).

- [ ] **Step 3: Write minimal implementation**

```python
# store_sqlite.py SCHEMA: flow_templates gains
#   parent_labels TEXT NOT NULL DEFAULT '[]',
# flows gains
#   effective_steps TEXT NOT NULL DEFAULT '',
# _schema_current: after the flow_templates/flows table check, probe
#   PRAGMA table_info for both columns, return False when absent.
# store_flows._row: d["parent_labels"] = json.loads(d.get("parent_labels") or "[]")
#   with try/except defaulting to [].
# store_flows._flow_row: raw = d.get("effective_steps") or ""; d["effective_steps"] =
#   json.loads(raw) if raw.strip() else None (try/except → None).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_flows.py -v`
Expected: PASS (full file, no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_flows.py tests/test_flows.py
git commit -m "[no-issue] flow store: parent_labels + effective_steps columns"
```

---

### Task 2: clone/compose store helpers + create/update support

**Files:**
- Modify: `src/workagent/store_flows.py:203-296` (`create_template`, `update_template`)
- Modify: `src/workagent/store_flows.py` (new helpers `collapse_first_start`, `compose_chains`)
- Test: `tests/test_flows.py`

**Interfaces:**
- Consumes: `validate_template`, `normalize_label`, `validate_label`.
- Produces: `collapse_first_start(steps: list[dict]) -> tuple[list[dict], bool]` (chain, dropped flag); `compose_chains(sources: list[tuple[str, list[dict]]]) -> tuple[list[dict], list[str], bool]` (merged steps, parent labels, start-dropped flag); `create_template(..., parent_labels: list[str] | None = None)`; `update_template` accepts/preserves `parent_labels` on import path.

- [ ] **Step 1: Write the failing tests**

```python
def test_compose_chains_collapses_second_start(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    a = [{"kind": "start"}, {"kind": "sync"}]
    b = [{"kind": "start"}, {"kind": "cleanup"}]
    merged, parents, dropped = sf.compose_chains([("a", a), ("b", b)])
    assert [s["kind"] for s in merged] == ["start", "sync", "cleanup"]
    assert parents == ["a", "b"] and dropped is True


def test_compose_chains_index_mapped_error(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    from workagent.errors import HarnessError
    import pytest
    with pytest.raises(HarnessError) as ei:
        sf.compose_chains([("a", [{"kind": "sync"}]),
                           ("b", [{"kind": "release"}])])
    assert "b:1" in str(ei.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_flows.py -k "compose_chains" -v`
Expected: FAIL with AttributeError (helpers not defined).

- [ ] **Step 3: Write minimal implementation**

```python
def collapse_first_start(steps):
    """Drop every 'start' except the chain's first step. Returns (chain, dropped)."""
    out = []
    dropped = False
    for i, s in enumerate(steps):
        if s.get("kind") == "start" and i != 0:
            dropped = True
            continue
        out.append(s)
    return out, dropped


def compose_chains(sources):
    """Concatenate (label, steps) pairs; collapse extra starts; validate with index-mapped errors."""
    from .errors import HarnessError
    merged = []
    origin = []  # merged index -> "label:local-index"
    for label, steps in sources:
        for j, s in enumerate(steps or []):
            origin.append(f"{label}:{j + 1}")
            merged.append(dict(s))
    merged, dropped = collapse_first_start(merged)
    try:
        norm = validate_template(merged)
    except HarnessError as e:
        # Map merged index back when the message names "Step N"
        raise HarnessError(f"compose: {e} (sources: {', '.join(o for o in origin)})", exit_code=2) from None
    return norm, [label for label, _ in sources], dropped
```

Plus: `create_template(..., parent_labels=None)` — validate list-of-str, store `json.dumps(parent_labels or [])`, and include `parent_labels` in the INSERT column list. `update_template` — allow `parent_labels` in `allowed` (used by backup import upsert), normalizing the same way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_flows.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_flows.py tests/test_flows.py
git commit -m "[no-issue] flow store: compose helpers + parent_labels support"
```

---

### Task 3: `flow template clone` + `compose` CLI

**Files:**
- Modify: `src/workagent/cli_flow.py:88-133` (add `clone`, `compose` commands after `update`)
- Modify: `src/workagent/store.py` (facade: pass through `parent_labels` kwarg)
- Modify: `src/workagent/web_args.py:78-83,105-106` (SPECS entries)
- Test: `tests/test_cli_flow.py`

**Interfaces:**
- Consumes: `store.get_template/create_template`, `sf.compose_chains`, `cli_core._catch_harness_errors/_fail/_print_result`, `EXIT_USAGE`.
- Produces: `flow template clone SRC [--name] [--label] [--json]`; `flow template compose LABEL --from a,b [--name] [--json]`.

- [ ] **Step 1: Write the failing test**

```python
def test_template_clone_copies_steps_with_lineage(runner, monkeypatch):
    import json
    from workagent import cli_flow
    src = {"label": "base", "name": "Base", "steps": [{"kind": "sync", "config": {}}],
           "pre_execution": "", "post_execution": ""}
    created = {}
    monkeypatch.setattr("workagent.store.get_template", lambda label: src if label == "base" else None)
    def fake_create(**fields):
        created.update(fields)
        return {"id": 2, "label": fields.get("label") or "base_copy", "name": fields["name"]}
    monkeypatch.setattr("workagent.store.create_template", fake_create)
    result = runner.invoke(cli_flow.flow_app, ["template", "clone", "base", "--name", "Base Copy"])
    assert result.exit_code == 0, result.output
    assert created["parent_labels"] == ["base"]
    assert created["steps"] == json.dumps([{"kind": "sync", "config": {}}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_flow.py -k "test_template_clone_copies_steps_with_lineage" -v`
Expected: FAIL (no such command / exit code 2).

- [ ] **Step 3: Write minimal implementation**

```python
@template_app.command("clone")
@_catch_harness_errors
def template_clone(
    src: str = typer.Argument(..., help="Source template label."),
    name: Optional[str] = typer.Option(None, "--name", help="Display name (default: '<src name> (copy)')."),
    label: Optional[str] = typer.Option(None, "--label", help="Unique key (default: normalized name)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Duplicate a flow template (lineage recorded in parent_labels)."""
    row = store.get_template(src)
    if row is None:
        _fail(f"unknown flow template: {src!r}", EXIT_USAGE)
    new = store.create_template(
        name=name or f"{row['name']} (copy)", label=label or "",
        steps=row.get("steps") or [],
        pre_execution=row.get("pre_execution") or "",
        post_execution=row.get("post_execution") or "",
        parent_labels=[src])
    _print_result({"cloned": new["label"], "id": new["id"], "from": src}, json_output)


@template_app.command("compose")
@_catch_harness_errors
def template_compose(
    label: str = typer.Argument(..., help="New template label."),
    from_: str = typer.Option(..., "--from", help="Comma-separated source template labels in order."),
    name: Optional[str] = typer.Option(None, "--name", help="Display name (default: label)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Combine templates into one flat chain (keep-first-start; lineage recorded)."""
    srcs = [s.strip() for s in (from_ or "").split(",") if s.strip()]
    if not srcs:
        _fail("--from needs at least one template label", EXIT_USAGE)
    chains = []
    for s in srcs:
        row = store.get_template(s)
        if row is None:
            _fail(f"unknown flow template: {s!r}", EXIT_USAGE)
        chains.append((s, row.get("steps") or []))
    merged, parents, dropped = sf.compose_chains(chains)
    new = store.create_template(name=name or label, label=label, steps=merged, parent_labels=parents)
    out = {"composed": new["label"], "id": new["id"], "from": parents}
    if dropped:
        out["note"] = "extra 'start' steps collapsed (kept first)"
    _print_result(out, json_output)
```

`store.py` facade: add `parent_labels=None` kwarg passthrough on `create_template`/`update_template`. `web_args.py`: `"flow template clone": ("--json",)`, `"flow template compose": ("--json",)` in BOOL_FLAGS; VAL_FLAGS `"flow template clone": ("--name", "--label")`, `"flow template compose": ("--from", "--name")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_flow.py tests/test_webapp.py::test_specs_mirror_cli_flags -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_flow.py src/workagent/store.py src/workagent/web_args.py tests/test_cli_flow.py
git commit -m "[no-issue] flow template clone + compose commands"
```

---

### Task 4: `flow run --steps` + effective_steps persistence

**Files:**
- Modify: `src/workagent/cli_flow_run.py:102-181` (`flow run` signature + chain resolution + create_flow call)
- Modify: `src/workagent/store_flows.py:309-401` (`create_flow`/`update_flow` accept `effective_steps`)
- Modify: `src/workagent/cli_flow.py:387-393` (`flow show` effective-first)
- Test: `tests/test_cli_flow.py`, `tests/test_flows.py`

**Interfaces:**
- Consumes: `sf.validate_template`, `_merge_config`, `_futil.has_banned`, `store.create_flow/update_flow/get_flow`.
- Produces: `flow run` accepts `--steps JSON`; `create_flow(..., effective_steps: list | str | None = None)`; `flow show --json` includes `effective_steps`.

- [ ] **Step 1: Write the failing test**

```python
def test_flow_run_steps_override_persisted(runner, monkeypatch):
    import json
    from workagent import cli_flow_run
    tpl = {"label": "t", "steps": [{"kind": "sync", "config": {}}],
           "pre_execution": "", "post_execution": ""}
    monkeypatch.setattr("workagent.store.get_template", lambda label: tpl)
    monkeypatch.setattr("workagent.store.get_action", lambda label: None)
    saved = {}
    def fake_create(**fields):
        saved.update(fields)
        return {"id": 1, "template_label": "t", "worktree_ref": "k",
                "repo_snapshot": {"repo": "r"}, "status": "pending",
                "current_step": 0, "step_results": []}
    monkeypatch.setattr("workagent.store.create_flow", fake_create)
    monkeypatch.setattr("workagent.store.update_flow", lambda *a, **k: saved)
    monkeypatch.setattr("workagent.store.get_flow", lambda *a, **k: None)
    result = runner.invoke(cli_flow_run.flow_app,
        ["run", "t", "k", "--steps", '[{"kind": "sync"}, {"kind": "cleanup"}]', "--yes"])
    assert result.exit_code == 0, result.output
    assert [s["kind"] for s in json.loads(saved["effective_steps"])] == ["sync", "cleanup"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_flow.py -k "test_flow_run_steps_override_persisted" -v`
Expected: FAIL (unrecognized `--steps` option).

- [ ] **Step 3: Write minimal implementation**

```python
# flow_run signature gains:
steps_override: Optional[str] = typer.Option(None, "--steps", help="Full launch-time step chain as JSON (validated; stored as effective_steps)."),
# Resolution (replaces lines 119-120):
base = json.loads(steps_override) if steps_override else (tpl.get("steps") or [])
try:
    steps = sf.validate_template(base, get_action=store.get_action)
except HarnessError as e:
    _fail(f"--steps invalid: {e}", EXIT_USAGE)
# --step-config merge + banned scan unchanged, operating on steps.
# create_flow call gains: effective_steps=(steps_override or None) — store the
# validated normalized chain: effective_steps=steps (list; store serializes).
# Resume path (_find_resumable + merged rebuild): after loading flow, if
# flow.get("effective_steps"): steps = sf.validate_template(flow["effective_steps"], ...)
# else template steps. --skip-steps bounds use len(steps) of the effective chain.
# flow show: out["steps"] = json.dumps(flow effective or template steps);
# out["effective_steps"] = json.dumps(flow.get("effective_steps") or []).
```

`create_flow`/`update_flow`: accept `effective_steps` (list/str/None → `json.dumps` when not str; None → `''` stored, read back as None). Add `"effective_steps"` to `update_flow.allowed`. `web_args.py` VAL_FLAGS `"flow run"` gains `"--steps"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_flow.py tests/test_flows.py tests/test_webapp.py::test_specs_mirror_cli_flags -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_flow_run.py src/workagent/store_flows.py src/workagent/cli_flow.py src/workagent/web_args.py tests/test_cli_flow.py tests/test_flows.py
git commit -m "[no-issue] flow run --steps + effective_steps persistence"
```

---

### Task 5: Backup + web passthrough + frontend types

**Files:**
- Modify: `src/workagent/store_backup.py:65-95` (round-trip `parent_labels`)
- Modify: `web/src/lib/api.ts:295-298` (optional fields)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_cli_backup.py`, `tests/test_webapp.py`

**Interfaces:**
- Consumes: existing backup validators, `FlowTemplateRow`, `FlowRow`.
- Produces: backup rows carry `parent_labels`; TS types carry `parent_labels?: string[]` / `effective_steps?: FlowStep[] | null`.

- [ ] **Step 1: Write the failing test**

```python
def test_backup_roundtrips_parent_labels(tmp_path, monkeypatch):
    from workagent import store_backup as b
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    b.import_backup(db, {"version": b.BACKUP_VERSION, "actions": [],
                         "flow_templates": [{"name": "T", "label": "t",
                                             "steps": [{"kind": "sync"}],
                                             "parent_labels": ["base"]}]})
    snap = b.export_backup(db)
    assert snap["flow_templates"][0]["parent_labels"] == ["base"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_backup.py -k "test_backup_roundtrips_parent_labels" -v`
Expected: FAIL (KeyError on `parent_labels`).

- [ ] **Step 3: Write minimal implementation**

```python
# store_backup._import_templates: pass parent_labels=raw.get("parent_labels", []) in both
# create_template and update_template calls; export_backup already returns full rows
# via list_templates (which now include parent_labels) — no export change needed.
# api.ts: FlowTemplateRow gains parent_labels?: string[]; FlowRow gains
# effective_steps?: FlowStep[] | null.
# API_CONTRACT.md: document flow template clone/compose (web run passthrough args)
# + flow run --steps + flow show effective_steps. FEATURE_INVENTORY.md: matching rows.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_backup.py tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_backup.py web/src/lib/api.ts web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_cli_backup.py tests/test_webapp.py
git commit -m "[no-issue] backup lineage + web parity for clone/compose"
```

---

### Task 6: FlowTemplates clone/compose UI

**Files:**
- Modify: `web/src/pages/FlowTemplates.tsx` (Clone button, compose multi-select, lineage caption)
- Test: `tests/test_webapp.py` (passthrough coverage for clone/compose args)

**Interfaces:**
- Consumes: `useCreateRun`, `createRun` with `command: "flow"`, `FlowTemplateRow.parent_labels`.
- Produces: per-row Clone action; selected-rows Compose action; lineage caption.

- [ ] **Step 1: Write the failing test**

```python
def test_flow_template_clone_compose_passthrough(client):
    body = client.post("/api/runs", json={"command": "flow",
        "args": ["template", "clone", "base", "--name", "Base Copy", "--yes"],
        "confirm": True}).json()
    assert body["ok"] is True
```

(Adapt to the repo's actual run-creation test helper shape; key assertion: clone/compose args pass `_validate_args` without 400.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp.py -k "clone_compose" -v`
Expected: FAIL (validation rejects unknown subcommand args).

- [ ] **Step 3: Write minimal implementation**

```tsx
// FlowTemplates.tsx: add selected: Set<string> state + header checkbox.
// Per-row: Clone button → handleEdit({...row, name: row.name + " (copy)", label: ""})
// with lineage note prefilled; Delete stays. Compose bar (visible when
// selected.size > 0): "Compose N selected" → concatenates steps in row order
// into the form via setSteps, sets lineage note, clears selection.
// Lineage caption under name: {t.parent_labels?.length ? `from ${t.parent_labels.join(", ")}` : null}
// Keep file under 500 lines; extract a ComposeBar/CloneButton only if it exceeds.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp.py -v && cd /home/bs/projects/personal/workagent/web && npx tsc --noEmit`
Expected: PASS + clean tsc.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/FlowTemplates.tsx tests/test_webapp.py
git commit -m "[no-issue] FlowTemplates clone + compose UI"
```

---

### Task 7: FlowWizard ad-hoc working copy + Flows detail

**Files:**
- Modify: `web/src/components/FlowWizard.tsx` (working-copy state, `--steps` on confirm)
- Modify: `web/src/components/ChainEditor.tsx` (launch-mode add/remove/reorder — keep ≤500 lines)
- Modify: `web/src/pages/Flows.tsx` (effective-steps badge)
- Test: `tests/test_webapp.py` (`flow run --steps` passthrough)

**Interfaces:**
- Consumes: `ChainEditor` edit-mode controls, `FlowRow.effective_steps`.
- Produces: wizard edits a working copy; confirm sends `--steps <working copy>` when it differs from template steps; detail shows effective steps with badge.

- [ ] **Step 1: Write the failing test**

```python
def test_flow_run_steps_passthrough(client):
    body = client.post("/api/runs", json={"command": "flow",
        "args": ["run", "t", "k", "--steps", '[{"kind": "sync"}]', "--yes"],
        "confirm": True}).json()
    assert body["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp.py -k "test_flow_run_steps_passthrough" -v`
Expected: FAIL (`--steps` rejected by `_validate_args`).

- [ ] **Step 3: Write minimal implementation**

```tsx
// FlowWizard.tsx: const [working, setWorking] = useState(template.steps ?? []);
// ChainEditor mode="launch" gains optional working-copy props OR a new
// mode="launch-edit": reuse edit-mode row controls (kind select, remove,
// add, drag) while keeping override editors. Simplest: pass mode="edit"
// visuals for structure + launch override forms below — implement as
// mode="launch-edit" branch reusing setStep/addStep/removeStep/moveStep.
// validateChain(working) live; Run disabled until clean.
// Confirm: const changed = JSON.stringify(working) !== JSON.stringify(template.steps ?? []);
// args gain ...(changed ? ["--steps", JSON.stringify(working)] : []).
// startRepoState(working, overrides) gates Run as today.
// Flows.tsx detail: const eff = flow.effective_steps; show eff ?? template steps;
// badge "modified at launch" when eff present.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp.py -v && cd /home/bs/projects/personal/workagent/web && npx tsc --noEmit && npm run build`
Expected: PASS + clean build.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FlowWizard.tsx web/src/components/ChainEditor.tsx web/src/pages/Flows.tsx tests/test_webapp.py
git commit -m "[no-issue] FlowWizard ad-hoc steps + effective display"
```

---

### Task 8: Full verification + changelog + PR

**Files:**
- Modify: `CHANGELOG.md` (append-only lines)
- All touched files (final review)

- [ ] **Step 1: Run the full suite**

Run: `cd /home/bs/projects/personal/harness && uv run pytest -v`
Expected: PASS, zero failures.

- [ ] **Step 2: Smoke the new commands**

Run: `cd /tmp && rm -f smoke-state.db && python -m workagent flow template add --name "Smoke Base" --label smoke_base --steps '[{"kind": "sync"}]' && python -m workagent flow template clone smoke_base --name "Smoke Copy" && python -m workagent flow template compose smoke_composed --from smoke_base,smoke_copy --name "Composed" && python -m workagent flow template show smoke_composed --json`
Expected: clone/compose succeed; show lists `parent_labels: ["smoke_base", "smoke_copy"]`.

- [ ] **Step 3: Append changelog lines**

```markdown
* - Flow templates: clone any template as an editable copy with lineage (`flow template clone`) [no-issue]
* - Flow templates: compose several templates into one chain (`flow template compose --from a,b`) [no-issue]
* - Flows: tweak steps at launch time in the wizard; runs remember the exact chain executed [no-issue]
```

(Keep each ≤150 chars, non-technical wording, append after the targeted line per repo rules — new lines only, zero `-` lines in `git diff` for CHANGELOG.md.)

- [ ] **Step 4: Self-review the diff + push + open MR**

Run: `git diff main...HEAD --stat && git push -u origin chore/flow-template-enhancements`
Expected: only `+` lines in CHANGELOG.md; diff touches only the files listed in Tasks 1–7.
Then open a PR/MR to the default branch per repo §5.
