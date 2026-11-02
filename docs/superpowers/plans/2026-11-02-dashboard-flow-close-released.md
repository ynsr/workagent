# Dashboard/Flow/Close-released Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix deleted-table stalls, FlowWizard custom-action/checkbox wiring, and close-released skipped handling.

**Architecture:** Keep bulk-operation/active-only invariant; fix data-flow wiring in FlowWizard/ChainEditor, add NO_PROXY parity + short timeout to status gh path, add Jira Released fallback only as opt-in after user decision.

**Tech Stack:** Python (Typer/Click CLI, FastAPI webapp), React+TS frontend, pytest + vitest.

**Spec:** This conversation (3 reported issues + probe evidence 2026-11-02).

## Global Constraints

- stdout carries ONLY data; logs/progress/confirmation → stderr.
- Confirmations only on TTY; non-TTY or --yes acts/fails with actionable message.
- Exit codes: 0 success, 1 error, 2 usage/needs-human.
- CLI owns logic; web mirrors via SPECS/BOOL_FLAGS/VAL_FLAGS; update API_CONTRACT.md + FEATURE_INVENTORY.md + frontend + both test files in same PR.
- Python files and React components/classes must not exceed 500 lines.
- Delete dead weight; boring design over abstraction.
- Production code change → integration test; run tests before done; cannot run → say so.

## Review Focus

- Deleted table with slow gh: stale rows must render + overlay, never blank/hidden during refetch.
- FlowWizard with zero custom actions: dropdown shows placeholder only, run still allowed for non-action steps.
- Fix-comments checkbox round-trip: check → --use-review-session present; uncheck → absent (fresh session default).
- close-released on Jira issue already Released: must not fail or duplicate-move when decision B/C chosen.
- close-released on deactivated/no-release-branch row: skips preserved when no Jira fallback (decision A).

---

### Task 1: FlowWizard custom-action dropdown shows actions

**Files:**
- Modify: `web/src/components/FlowWizard.tsx:1-87`
- Test: `web/` frontend test colocated with FlowWizard (or `web/API_CONTRACT.md` entry if no runner; then add pytest mirror in `tests/test_webapp_flow.py`)

**Interfaces:**
- Consumes: `useActions()` from `@/lib/queries:93-95`, `GET /api/actions` → `store.list_actions()` (`src/workagent/webapp.py:215-218`).
- Produces: `knownActions: {label,name}[]` prop for `ChainEditor` (same shape as `web/src/pages/FlowTemplates.tsx:46`).

- [ ] **Step 1: Write the failing test**

```tsx
// FlowWizard populates knownActions from useActions
// render <FlowWizard template={with action step} refs={["k"]} onClose={()=>{}} />
// with actionsData = {actions:[{label:"a",name:"A"}]} mocked
// expect getByText("A (a)") option to exist
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- FlowWizard` (from `web/`)
Expected: FAIL — option missing (knownActions hardcoded `[]` at `FlowWizard.tsx:83`)

- [ ] **Step 3: Write minimal implementation**

```tsx
import { useActions } from "@/lib/queries"
// inside FlowWizard:
const { data: actionsData } = useActions()
const knownActions = (actionsData?.actions ?? []).map((a) => ({ label: a.label, name: a.name }))
// <ChainEditor ... knownActions={knownActions} />
```

Mirror `FlowTemplates.tsx:44-46`. No endpoint change (healthy, just never called).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- FlowWizard` then `pytest tests/test_webapp_flow.py -q`
Expected: PASS, no regressions

- [ ] **Step 5: Commit**

```bash
git add web/src/components/FlowWizard.tsx <test>
git commit -m "[no-issue] FlowWizard populates custom-action dropdown"
```

### Task 2: Fix-comments checkbox toggles in FlowWizard

**Files:**
- Modify: `web/src/components/ChainEditor.tsx:405-411` (preferred) — change guard from `overrides` truthiness to `onOverridesChange` presence
- Test: frontend test for StepConfigForm onChange reaching setWorking via ChainEditor in wizard wiring

**Interfaces:**
- Consumes: `FlowWizard` working state (`useState<FlowStep[]>`, `FlowWizard.tsx:25`), `patch("use_review_session", ...)` (`ChainEditor.tsx:148-154,215-216`).
- Produces: `working[i].config.use_review_session === true | undefined` → flow key map `cli_flow_util.py:44` → `--use-review-session` flag (`cli_review.py:481`).

- [ ] **Step 1: Write the failing test**

```tsx
// render ChainEditor mode="edit" steps=[{kind:"fix_comments",config:{}}] overrides={{}} onChange=setWorking (no onOverridesChange)
// fire click on "Use review session" checkbox
// expect setWorking called with config {use_review_session:true}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- ChainEditor`
Expected: FAIL — `setOverride` early-returns (`:315-316`), checkbox dead

- [ ] **Step 3: Write minimal implementation**

```tsx
onChange={(config) => mode === "edit" && onOverridesChange ? setOverride(i, config) : setStep(i, { ...step, config })}
config={mode === "edit" && onOverridesChange ? {...} : (step.config ?? {})}
requiredRepo={mode === "edit" && onOverridesChange ? step.kind === "start" : undefined}
```

One-spot guard fix; FlowWizard keeps `overrides={{}}` (harmless once guard keys on handler presence). Alternative (omit `overrides` in FlowWizard) also works but leaves the trap for next caller.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- ChainEditor` then full web suite for regressions
Expected: PASS; checkbox toggles checked/unchecked; unchecked deletes key (fresh-session default preserved)

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ChainEditor.tsx <test>
git commit -m "[no-issue] ChainEditor override guard keys on handler presence"
```

### Task 3: run_cmd NO_PROXY/env parity (Dashboard gh timeout root cause)

**Files:**
- Modify: `src/workagent/errors.py:66-109` (`run_cmd`), `src/workagent/env.py:37-61` (`ensure_proxy_env`)
- Test: `tests/test_errors.py` (extend), existing `tests/test_env.py`

**Interfaces:**
- Consumes: `~/.config/workagent/env.sh` (`export NO_PROXY=...,chat.jibit.cloud`), `ensure_proxy_env()` proxy guard.
- Produces: `run_cmd` subprocess env honoring file NO_PROXY + proxy without clobbering explicit user config.

Evidence: `run_cmd` never sources env.sh; only `ensure_proxy_env` (listener :10808) covers proxy, NO_PROXY from file invisible → Dashboard `gh` (60s each, sequential per-row in `webapp.py:156-158`) stalls. Listener confirmed up (`ss` shows :10808). Shell env has NO_PROXY with `;` separators + chat.jibit.cloud already.

- [ ] **Step 1: Write the failing test**

```python
def test_run_cmd_honors_env_file_no_proxy(tmp_path, monkeypatch):
    # env file exports NO_PROXY including chat.jibit.cloud
    # run_cmd("gh", ...) child env must contain that NO_PROXY
    # currently fails: run_cmd never reads env.sh
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_errors.py::test_run_cmd_honors_env_file_no_proxy -v`
Expected: FAIL — NO_PROXY absent in child env

- [ ] **Step 3: Write minimal implementation**

```python
# errors.run_cmd: after ensure_proxy_env(), overlay parsed env file NO_PROXY/proxy keys only
from .env import resolve_env_file, parse_env_file
env_file = resolve_env_file()
if env_file:
    for k in ("NO_PROXY","no_proxy","http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","all_proxy"):
        v = parse_env_file(env_file).get(k)
        if v and k not in os.environ:
            os.environ[k] = v  # or pass env= to subprocess
```

Keep explicit-user-config-wins (never overwrite existing). Do NOT source full env.sh (VISUAL/EDITOR/JAVA_HOME irrelevant to gh; minimal keys only).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_errors.py tests/test_env.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/errors.py src/workagent/env.py tests/test_errors.py
git commit -m "[no-issue] run_cmd honors env.sh proxy/NO_PROXY"
```

### Task 4: Bound status-path gh wait + don't hide deleted section

**Files:**
- Modify: `src/workagent/cli_status.py:106-126` (`_query_pr` timeout), `src/workagent/refs_ci.py` fetch paths (timeout), `web/src/pages/Dashboard.tsx:699-724`, `web/src/components/DeletedTable.tsx:38`
- Test: `tests/test_cli_status.py` (timeout passed), frontend conditional-render test

**Interfaces:**
- Consumes: `_cache_usable` SWR (30d PR / 1d no-PR), `_ci_usable` (1d), `useStatusDeleted()` SWR 30d.
- Produces: bounded `/api/status` latency; deleted section visible from stale cache during refetch.

- [ ] **Step 1: Write the failing test**

```python
def test_query_pr_uses_short_timeout(monkeypatch):
    # _query_pr must call fetch with timeout <= 15s (currently 60s default, sequential per row)
```

```tsx
// DeletedTable with stale data + isFetching: section header visible (currently return null when keys empty during stall)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_status.py -q`; `npm test -- DeletedTable`
Expected: FAIL both

- [ ] **Step 3: Write minimal implementation**

```python
# _query_pr / fetch_ci / reviews: pass timeout=12 (or 15) to run_cmd
```

```tsx
// Dashboard: gate on isPending && !data (like main table :643), not empty keys; DeletedTable renders header from stale deletedAll
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_status.py -q` + web suite
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_status.py src/workagent/refs_ci.py web/src/pages/Dashboard.tsx web/src/components/DeletedTable.tsx
git commit -m "[no-issue] bound status gh timeout; keep deleted section visible"
```

### Task 5: Close-released Jira fallback (only after user picks B or C)

**Files:**
- Modify: `src/workagent/cli_cleanup.py:100-164`, `src/workagent/issue_status.py:122-143` (reuse `_issue_state`), `web/src/pages/Dashboard.tsx:331-371` (copy), `web/API_CONTRACT.md`
- Test: `tests/test_cli_cleanup.py` (new cases per decision)

**Interfaces:**
- Consumes: `_issue_state(number, tool, target)` (`issue_status.py:122`), `set_issue_status(stored,"done")` (`:185-240`), Jira variants `Released/Done` (`:74-75`).
- Produces: rows `closed:released-already | closed | skipped:*` per decision.

Decision required (see ask below) BEFORE implementing. Do not implement on assumption.

- [ ] **Step 1: Write the failing test (per chosen decision)**

```python
def test_cleanup_released_skipped_released_jira(monkeypatch):
    # deactivated + no-release-branch rows with Jira status Released → closed (B) / closed:released-already (C)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_cleanup.py -q`
Expected: FAIL — currently `skipped:deactivated` / `skipped:no-release-branch` without Jira check

- [ ] **Step 3: Write minimal implementation**

```python
# only for rows that would skip as deactivated/no-release-branch:
parsed = _parse(stored); cur = _issue_state(...) if parsed
if cur == "released": set_issue_status(stored, "done") → "closed"
```

Guardrails: never move non-Released issues; `Released` only (not `Done` — terminal Done rows need no action; report as `closed:already-done` or leave skipped per decision); no worktree/branch/PR touched (issue state only, matching current contract).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_cleanup.py tests/test_issue_status.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_cleanup.py tests/test_cli_cleanup.py web/API_CONTRACT.md
git commit -m "[ID] cleanup --released closes Jira-Released skips"
```
(ID = issue number if user supplies one; else [no-issue].)

## Self-Review

- Spec coverage: deleted stall (Tasks 3+4), dropdown (Task 1), checkbox (Task 2), close-released (Task 5, gated on decision). ✓
- Placeholders: none — every step has file:line + code + command.
- Type consistency: `knownActions {label,name}[]` matches ChainEditorProps `:28`; `use_review_session boolean|undefined` matches `cli_flow_util.py:44` bool flag; `closed` row statuses match `_print_rows columns=[key,branch,status]`.
- Review Focus tests pinned to owning tasks above.
