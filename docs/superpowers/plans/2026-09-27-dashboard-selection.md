# Dashboard Selection + Bulk Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Selectable Dashboard worktree rows with scope-aware bulk actions, plus the `--session-file` + `--all` fix.

**Architecture:** Frontend selection (`?sel=`-backed checkbox column, N-run fan-out in `Dashboard.tsx`) plus a 3-line backend carve-out skipping `--session-file` injection on `--all`. No CLI grammar or registry changes.

**Tech Stack:** React + TanStack Query + shadcn/Radix Checkbox (web); FastAPI serve backend + Typer CLI + pytest (python).

**Spec:** `docs/superpowers/specs/2026-09-27-dashboard-selection-design.md`

## Global Constraints

- Suite via `.venv/bin/python -m pytest tests -q` (system `python3` lacks `typer`).
- Must verify BOTH `./web/node_modules/.bin/tsc --noEmit --project web` AND `npm run build --prefix web` (vite-only failures exist).
- `serve` serves gitignored `web/dist` — rebuild + hard-refresh to see changes.
- stdout carries ONLY data; every log/progress/confirmation line → stderr (CLI).
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input (CLI).
- No behavior change to empty-selection bulk semantics (`--all` / `--merged` over every active linked worktree).
- Explicit `--session-file` + `--all` still 400s (validator unchanged).
- Do NOT run `./install.sh` — user reinstalls manually; new `web/dist` built locally only.

## Review Focus

- `?sel=` with unknown/stale keys must drop them silently, never crash the table.
- Header checkbox with zero visible rows (filter matches nothing) must not claim "all selected".
- `review --all` parent run must carry NO `--session-file` in argv (the exact reported error).
- Cleanup merged button while a selection exists must be disabled with an explanatory tooltip, not silently act on all.
- Bulk selection of a key whose worktree has no PR must fail only that run, siblings continue.

---

### Task 1: Backend `--session-file` carve-out for `--all`

**Files:**
- Modify: `src/workagent/web_runs_routes.py:66-86`
- Test: `tests/test_webapp_runs.py` (append new tests)
- Modify: `web/API_CONTRACT.md` (one note under the `--all` runs entry)

**Interfaces:**
- Consumes: `register_runs_routes.create_run(body: RunIn)` — existing closure; `_has_session_file`, `_session_file_arg`, `_session_file_for` from `web_args.py`.
- Produces: `--all` runs spawn with `run.session_file == ""` and no `--session-file` in `run.argv` (assertable via the existing `fake_spawn` monkeypatch pattern).

- [ ] **Step 1: Write the failing test**

```python
def test_all_runs_skip_session_file_injection(client, monkeypatch):
    """Review/sync --all: no --session-file injected (CLI rejects it with --all)."""
    seen: dict = {}

    def fake_spawn(run, registry):
        seen["argv"] = run.argv
        seen["session_file"] = run.session_file
        with run.lock:
            run.exit_code = 0
            run.state = "succeeded"
        registry.release_target(run)

    monkeypatch.setattr("workagent.webapp._spawn", fake_spawn)
    for command in ("review", "sync"):
        seen.clear()
        r = client.post("/api/runs", json={"command": command,
                                           "args": ["--all"],
                                           "confirm": True})
        assert r.status_code == 202, r.text
        assert "--session-file" not in seen["argv"]
        assert seen["session_file"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_webapp_runs.py::test_all_runs_skip_session_file_injection -v`
Expected: FAIL with `assert '--session-file' not in [..., '--session-file', ...]`

- [ ] **Step 3: Write minimal implementation**

In `src/workagent/web_runs_routes.py`, inside `create_run`, change the injection guard:

```python
        if body.command in ("start", "review") \
                and "--dry-run" not in body.args \
                and "--all" not in body.args \
                and not _has_session_file(body.command, body.args):
```

(No other change: the `else` branch already records `run.session_file = ""` when no explicit flag is present.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_webapp_runs.py -q`
Expected: all pass (including the pre-existing `test_all_with_session_file_rejected`, `test_start_run_injects_session_file`, `test_sync_explicit_session_file_recorded` — injection still works for non-`--all`).

- [ ] **Step 5: Add the contract note**

In `web/API_CONTRACT.md`, under the runs entry covering `--all` bulk actions, append one sentence: "`--all` runs carry no `--session-file` (the CLI rejects it with `--all`); each per-worktree child generates its own transcript."

- [ ] **Step 6: Commit**

```bash
git add src/workagent/web_runs_routes.py tests/test_webapp_runs.py web/API_CONTRACT.md
git commit -m "fix(web): skip --session-file injection on --all runs"
```

---

### Task 2: Selection column in StatusTableMain (+ types)

**Files:**
- Modify: `web/src/components/StatusTableMain.tsx`
- Modify: `web/src/components/StatusTable.tsx`
- Test: `./web/node_modules/.bin/tsc --noEmit --project web` + `npm run build --prefix web` (no unit harness for web; typecheck + build are the gate)

**Interfaces:**
- Consumes: existing `keys` memo (`StatusTableMain.tsx:100-116`), `expanded` state, shadcn `Table`/`Checkbox` primitives, `useSearchParams` (`?q=` pattern at `:95-123`).
- Produces (for Task 3): `StatusTable` accepts optional `selected?: Set<string>`, `onSelectionChange?: (s: Set<string>) => void`, and reports filtered visible keys (via `visibleKeys?: string[]` prop-out or an `onVisibleKeys` callback — Task 3 wires `Dashboard`).

- [ ] **Step 1: Extend the StatusTable prop types**

In `web/src/components/StatusTable.tsx`, extend the props consumed by `StatusTableMain` (keep `StatusTableActions` untouched):

```tsx
export interface StatusTableSelection {
  selected?: Set<string>
  onSelectionChange?: (next: Set<string>) => void
  onVisibleKeys?: (keys: string[]) => void
}
```

- [ ] **Step 2: Add `sel` ↔ `?sel=` sync + tri-state header**

In `web/src/components/StatusTableMain.tsx`:
- Parse `params.get("sel")` into a `Set` (drop keys not in `worktrees`); default to the controlled `selected` prop when provided, else internal `useState`.
- Every render, call `onVisibleKeys?.(keys)` with the filtered memo (parent needs it for select scope; guard with a ref-equality check to avoid loops — only call when the joined string changes).
- Desktop `TableHeader` row: leading `TableHead` (`className="w-10"`) with a `Checkbox` whose `checked` is `true` / `false` / `"indeterminate"` (Radix supports `"indeterminate"`) over visible keys; `onCheckedChange` selects/clears all visible; `aria-label="Select all visible worktrees"`.
- Desktop `TableBody` rows: leading `TableCell` with `Checkbox checked={sel.has(key)}` `onCheckedChange={() => toggle(key)}` `aria-label={\`Select ${key}\`}` `onClick={(e) => e.stopPropagation()}`; row gets `aria-selected={sel.has(key)}` and `data-[state=selected]:bg-muted/50` highlight (keep `bg-destructive/5` for invalid via `cn()` order).
- Mobile cards (`:339-448`): same `Checkbox` in the card header row beside `WorktreeKeyLink`.
- Filter pill row (`:164-175`): when `sel.size > 0`, render a `"{n} selected"` badge + "Clear" button (`onSelectionChange(new Set())`, and strip `sel` from `?sel=`).

- [ ] **Step 3: Typecheck and build**

Run: `./web/node_modules/.bin/tsc --noEmit --project web`
Expected: clean.
Run: `npm run build --prefix web`
Expected: `✓ built`.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/StatusTableMain.tsx web/src/components/StatusTable.tsx
git commit -m "feat(web): selectable worktree rows (?sel=-backed, tri-state header)"
```

---

### Task 3: Dashboard scope-aware toolbar + N-run fan-out + Sync All styling

**Files:**
- Modify: `web/src/pages/Dashboard.tsx`
- Test: `./web/node_modules/.bin/tsc --noEmit --project web` + `npm run build --prefix web`
- Modify: `web/FEATURE_INVENTORY.md` (bulk-action row: selection scope)

**Interfaces:**
- Consumes: Task 2's `selected` / `onSelectionChange` / visible-keys reporting; existing `useCreateRun` (`CreateRunInput{command,args,confirm,force?}`), `confirm()` (`ConfirmOptions{action,title,description,destructive,confirmLabel,details,extras?}`), `runCreated(run_id,label)`.
- Produces: scope-aware toolbar; no new exports (page-level behavior only).

- [ ] **Step 1: Own selection state + wire the table**

```tsx
const [selected, setSelected] = useState<Set<string>>(new Set())
// pass selected + onSelectionChange into <StatusTable .../>; prune keys
// that vanish from `worktrees` via useEffect.
const selCount = selected.size
const scopeSuffix = selCount > 0 ? ` (${selCount})` : ""
```

- [ ] **Step 2: Scope-aware labels + Sync All styling**

Change the four buttons: `Sync all` → `` `Sync${selCount ? ` (${selCount})` : " all"}` `` (same for Review / Fix / Cleanup), each with a `title` (`Sync 3 selected worktrees` vs `Sync every active linked worktree`). Change Sync All `variant="secondary"` → `variant="outline"` (`Dashboard.tsx:345`). Disable Cleanup merged while `selCount > 0` with `title="Clear selection to clean all merged (or Delete per row)"`.

- [ ] **Step 3: Branch the four handlers on selection**

For `handleSyncAll` / `handleReviewAll` / `handleFixAll`: when `selCount === 0`, keep today's single call verbatim (same confirm copy, same args). When non-empty: one confirm dialog whose Scope `details` row lists the keys (truncate after 5 with `+M more`), then sequential awaits:

```tsx
const keys = [...selected]
for (const key of keys) {
  try {
    const { run_id } = await createRun.mutateAsync({ command: "sync", args: [key, ...(syncMerge ? ["--merge"] : [])], confirm: true })
    toast.success(`Sync ${key} started`, { action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) } })
  } catch (err) { toast.error(`${key}: ${errorText(err)}`) }
}
```

Review adds `...(reviewForceAll ? ["--force-all"] : [])`; Fix uses `args: [key, "--fix-comments"]`. One failure doesn't stop the rest. `handleCleanupMerged` with a selection is unreachable (disabled); keep its body all-only.

- [ ] **Step 4: Typecheck, build, full suite**

Run: `./web/node_modules/.bin/tsc --noEmit --project web` (clean), `npm run build --prefix web` (`✓ built`), `.venv/bin/python -m pytest tests -q` (all pass — Task 1's tests plus no regressions).

- [ ] **Step 5: Inventory note + commit**

In `web/FEATURE_INVENTORY.md`, bulk-action row: append "Toolbar acts on the `?sel=` selection when non-empty (N per-key runs), else `--all`/`--merged`; `review --all` carries no `--session-file`."

```bash
git add web/src/pages/Dashboard.tsx web/FEATURE_INVENTORY.md
git commit -m "feat(web): scope-aware bulk toolbar with selection fan-out"
```
