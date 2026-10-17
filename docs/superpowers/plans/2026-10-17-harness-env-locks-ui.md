# Harness env + locks, Candidates, StatusTable, Launch prefs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Env.sh on every harness path, terminal locks+dots everywhere, Candidates/StatusTable/Launch fixes.

**Architecture:** Centralize wrap+lock in `cli_harness` helpers; web endpoints call them. Frontend follows existing `releasePrefs.ts` and `useRepoTabs` patterns.

**Tech Stack:** Python (Typer/FastAPI), React + TanStack Query, localStorage prefs.

**Spec:** `docs/superpowers/specs/2026-10-17-harness-env-locks-ui-design.md`

## Global Constraints

- Python files and React components/classes must not exceed 500 lines.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- Per-repo `tool` persisted in registry; host-scoped tracker ids.
- CHANGELOG entries ≤150 chars + issue bracket.
- Tests run from `/home/bs/projects/personal/harness` via `uv run pytest -v`.

## Review Focus

- Preview command containing single quotes must survive wrap + replay without breaking shell quoting.
- Terminal lock claimed but terminal dies instantly: sweep must reap within seconds, never wedge the worktree.
- `?repo=` naming a zero-count (now hidden) repo must fall back to All, never blank.
- Restored Launch prefs must never override explicit URL prefill params.
- Normalized PR URL variants (trailing slash, `.git`) must all match linked rows.

---

### Task 1: WS1 preview command always wrapped

**Files:**
- Modify: `src/workagent/cli_harness.py:210-246`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: `env.ensure_env_file`, `env.wrap_command` (existing).
- Produces: preview `full_cmd` always wrapped with `source <env> &&`.

- [ ] **Step 1: Write the failing test**

```python
def test_preview_command_sources_env(tmp_path, monkeypatch):
    from workagent import cli_harness as _h
    monkeypatch.setattr(_h, "ensure_env_file", None)  # placeholder
    assert False, "preview full_cmd must contain 'source ' prefix"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backend.py::test_preview_command_sources_env -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# cli_harness.py:215 — drop the `if (launch or terminal)` gate:
env_path = _env.ensure_env_file(env_file)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backend.py::test_preview_command_sources_env -v`
Expected: PASS (after fleshing the test to build a real preview result and assert `result["harness_command"].startswith("source ")`)

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_harness.py tests/test_backend.py
git commit -m "Preview harness command always wraps env.sh [no-issue]"
```

### Task 2: WS1 session-resume honors run --env-file

**Files:**
- Modify: `src/workagent/web_runs_routes.py:408-427`
- Test: `tests/test_webapp_runs.py`

**Interfaces:**
- Consumes: `_env_file_arg` helper (used by run-resume at :333-334, :360-361).
- Produces: `resume_session` passes originating run's `--env-file`, else default.

- [ ] **Step 1: Write the failing test**

```python
def test_resume_session_uses_run_env_file(client, monkeypatch):
    assert False, "session resume must forward the run's --env-file"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_runs.py::test_resume_session_uses_run_env_file -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# resume_session: find latest run for the session's worktree_ref, extract
# --env-file via _env_file_arg, pass to _open_terminal(worktree, session_file, env)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapp_runs.py::test_resume_session_uses_run_env_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_runs_routes.py tests/test_webapp_runs.py
git commit -m "Session resume honors originating --env-file [no-issue]"
```

### Task 3: WS5 terminal lock on resume/start-terminal endpoints

**Files:**
- Modify: `src/workagent/web_runs_routes.py:307-392`, `src/workagent/web_runs.py:415-429`
- Test: `tests/test_webapp_runs.py`

**Interfaces:**
- Consumes: `store.record_harness_run(key, harness, worktree, origin="terminal", pid)` (store.py:552-591).
- Produces: endpoints return 409 `{code:"conflict"}` when blocked; lock visible via `_harness_cell`.

- [ ] **Step 1: Write the failing test**

```python
def test_resume_endpoint_claims_terminal_lock(client, monkeypatch):
    assert False, "resume must record_harness_run with origin=terminal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_runs.py::test_resume_endpoint_claims_terminal_lock -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# After _open_terminal* spawn in each of the 3 endpoints:
#   blocker = _store.record_harness_run(ref_key, "omp", worktree, origin="terminal", pid=spawned_pid)
#   if blocker: raise ApiError("conflict", ..., 409)
# _open_terminal/_open_terminal_command must return the spawned pid.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapp_runs.py -q -k "terminal_lock or resume or start_terminal"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_runs_routes.py src/workagent/web_runs.py tests/test_webapp_runs.py
git commit -m "Terminal lock claimed on all web terminal spawns [no-issue]"
```

### Task 4: WS2 Candidates Review → Launch prefill + repo badge

**Files:**
- Modify: `web/src/components/CandidatesCard.tsx:133-157, 241-266`
- Test: `npx tsc --noEmit` (no frontend unit harness)

**Interfaces:**
- Consumes: react-router `useNavigate`; existing Dashboard deep-link shape `mode=review&ref=`.
- Produces: Review button navigates; repo `name` badge per row.

- [ ] **Step 1: Replace handleAction review branch with navigate**

```tsx
// review: navigate(`/launch?mode=review&ref=${encodeURIComponent(pr.url)}`)
```

- [ ] **Step 2: Render registered repo name badge**

```tsx
// backend adds `repo_name`; row shows <Badge>{pr.repo_name ?? pr.repo}</Badge>
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit` in `web/`
Expected: clean

- [ ] **Step 4: Commit**

```bash
git add web/src/components/CandidatesCard.tsx
git commit -m "Candidates review opens Launch prefill + repo badge [no-issue]"
```

### Task 5: WS2 backend linked + default→release filtering

**Files:**
- Modify: `src/workagent/cli_candidates.py:27-85`, `src/workagent/refs.py:261-295`
- Test: `tests/test_cli_candidates.py` (create if absent — check first)

**Interfaces:**
- Consumes: `store.load_repos()` (default_branch/release_branch), `_linked_sets()`.
- Produces: `_candidate_prs()` drops normalized-URL/path matches and default→release rows; rows carry `repo_name`.

- [ ] **Step 1: Write the failing test**

```python
def test_candidate_prs_drops_linked_variants_and_release_mrs(monkeypatch):
    assert False, "slash-variant URLs and default->release MRs must be filtered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_candidates.py -v -k drops_linked`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# _linked_sets: normalize (.rstrip('/') + strip '.git'); _candidate_prs: consult
# linked_paths; refs.fetch_open_prs: add baseRefName/target_branch; drop when
# target == release_branch or source == default_branch.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_candidates.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_candidates.py src/workagent/refs.py tests/test_cli_candidates.py web/API_CONTRACT.md
git commit -m "Candidates filter linked variants + release MRs [no-issue]"
```

### Task 6: WS3 zero-count repos hidden in shared hook

**Files:**
- Modify: `web/src/lib/useRepoTabs.ts:47-71`
- Test: `npx tsc --noEmit`

**Interfaces:**
- Consumes: `tabs.names/counts/other` shape (unchanged).
- Produces: `names` only includes repos with count > 0; stale `?repo=` falls back to All.

- [ ] **Step 1: Filter names to counted repos + fallback**

```ts
const names = list.map((r) => r.name).filter((n) => (counts.get(n) ?? 0) > 0)
// in consumer: if repoFilter && !names.includes(repoFilter) && repoFilter !== "(other)" → treat as ""
```

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit` in `web/`
Expected: clean

- [ ] **Step 3: Commit**

```bash
git add web/src/lib/useRepoTabs.ts
git commit -m "Hide zero-count repos in shared repo tabs [no-issue]"
```

### Task 7: WS3 deactivated restart diagnosis

**Files:**
- Modify: TBD by diagnosis (serve boot, `useStatusInactive`, or `?include_inactive` wiring)
- Test: restart repro script (throwaway) + regression test at owning layer

- [ ] **Step 1: Reproduce with service restart**

Run: `systemctl --user restart workagent && curl localhost:PORT/api/status?include_inactive=true | jq 'keys'`
Expected: confirm whether `jira:IPG-1043@projectx` is present (DB says active=0)

- [ ] **Step 2: Fix at the layer that drops it**

```python
# Only after Step 1 identifies the layer — no speculative change.
```

- [ ] **Step 3: Regression test + commit**

```bash
git commit -m "Deactivated worktrees survive service restart [no-issue]"
```

### Task 8: WS4 Launch prefs + layout

**Files:**
- Create: `web/src/lib/launchPrefs.ts` (mirror `releasePrefs.ts`)
- Modify: `web/src/pages/Launch.tsx:110-122, 185-188, 282-360`, `web/src/components/StartForm.tsx`
- Test: `npx tsc --noEmit`

**Interfaces:**
- Consumes: localStorage key `launch.prefs.v1` shaped `{start:{runMode,base},review:{runMode,thenFix,fixAction,newFixSession},sync:{merge}}`.
- Produces: `loadLaunchPrefs()/saveLaunchPrefs()`; URL params override stored.

- [ ] **Step 1: Create launchPrefs.ts mirroring releasePrefs.ts**

```ts
export function loadLaunchPrefs(): LaunchPrefs { /* try/catch JSON, defaults on miss */ }
export function saveLaunchPrefs(p: LaunchPrefs): void { /* try/catch set */ }
```

- [ ] **Step 2: Wire into Launch.tsx (restore on mount, save on change, URL wins)**

- [ ] **Step 3: Pin tablist layout (flex-nowrap + shrink-0 + min-h reserve)**

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit` in `web/`
Expected: clean

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/launchPrefs.ts web/src/pages/Launch.tsx web/src/components/StartForm.tsx
git commit -m "Launch cards persist prefs; Review tab layout pinned [no-issue]"
```

### Task 9: Full verification + docs

**Files:**
- Modify: `CHANGELOG.md`, `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`

- [ ] **Step 1: Run full suite**

Run: `uv run pytest -v` (from `/home/bs/projects/personal/harness` per AGENTS.md)
Expected: all pass

- [ ] **Step 2: Typecheck + reinstall**

Run: `npx tsc --noEmit` in `web/`; `./install.sh`
Expected: clean, `doctor: ok`

- [ ] **Step 3: CHANGELOG (gap insert, additions only)**

```bash
git diff CHANGELOG.md | grep "^-" | grep -v "^---" # must be empty
```

- [ ] **Step 4: Commit + push on main**

```bash
git push origin main
```
