# Adhoc Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `workagent adhoc REF… --prompt TEXT` plus Launch tab, Dashboard bulk entry, and WorktreeDetail button.

**Architecture:** New `cli_adhoc.py` composes `worktrees.resolve_any` + `cli_harness._guard_harness/_run_harness` (same path as `action run`); web passthrough via `web_args.py` SPECS/BOOL/VAL_FLAGS; frontend extends the Launch mode union plus two navigation buttons.

**Tech Stack:** Typer CLI, FastAPI passthrough (`webapp.SPECS`), React/Vite frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-adhoc-prompt-design.md`

## Global Constraints

- Python files and React components/classes must not exceed 500 lines.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- New/renamed flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR.
- `tests/test_webapp.py::test_specs_mirror_cli_flags` fails on CLI/web flag drift — keep them in sync.
- Spec correction: `_guard_harness` lives in `workagent/cli_harness.py` (not `cli_action`); the spec's import note is wrong, the plan below imports from `cli_harness`.
- Spec correction: `derive_session_type("adhoc")` returns None today; Task 2 maps `"adhoc"` → `"custom_action"` so sessions land typed with `session_metadata {"action": "adhoc"}` instead of NULL.

## Review Focus

- Empty prompt (`--prompt ""` or whitespace): usage error exit 2, never launches a harness with an empty prompt.
- Zero positional refs: usage error exit 2, never a silent no-op success.
- `--launch` + `--terminal` together: usage error exit 2, never an ambiguous double launch.
- Deactivated or invalid worktree mid-list: skipped row with stderr note, remaining refs still run, exit code 1.
- Unknown `?mode=` deep-link value: Launch form left blank with warning toast (existing behavior must keep working for the new `adhoc` value).

---

### Task 1: CLI `adhoc` command

**Files:**
- Create: `src/workagent/cli_adhoc.py`
- Modify: `src/workagent/cli.py` (register import next to `cli_action`, ~line 198)
- Test: `tests/test_cli_adhoc.py`

- Consumes: `worktrees.resolve_any(ref, links)`, `cli_harness._guard_harness(key, wt)`, `cli_harness._run_harness(name, prompt, wt, repo, no_tty, use_launch, result, json_output, run_key=key, session_file=…, terminal=…, env_file=…)`, `cli_harness.append_extra_prompt(prompt, extra)`, `cli_core._fail/_print_result/eprint/EXIT_USAGE`.
- Produces: `adhoc()` flat command on the root app (same registration shape as `harness run`'s `argv: list[str]`); Task 3's web passthrough uses the argv shape only, no import.

- [ ] **Step 1: Write the failing tests**

```python
"""CLI adhoc tests."""
from workagent import cli
from tests.cli_helpers import runner, _invoke


def test_adhoc_requires_refs(isolated_config):
    r = runner.invoke(cli.app, ["adhoc", "--prompt", "hi", "--json"])
    assert r.exit_code == 2
    assert "ref" in (r.stdout + r.stderr).lower()


def test_adhoc_requires_nonblank_prompt(isolated_config):
    r = runner.invoke(cli.app, ["adhoc", "jira:IPG-1", "--prompt", "  ", "--json"])
    assert r.exit_code == 2
    assert "prompt" in (r.stdout + r.stderr).lower()


def test_adhoc_rejects_launch_plus_terminal(isolated_config):
    r = runner.invoke(cli.app, ["adhoc", "jira:IPG-1", "--prompt", "hi",
                                "--launch", "--terminal", "--json"])
    assert r.exit_code == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_adhoc.py -v`
Expected: FAIL with "adhoc: command not found" style usage error (no such command).

- [ ] **Step 3: Write minimal implementation**

```python
"""`adhoc` command: run a user prompt on linked worktrees, sequentially."""
from __future__ import annotations

from typing import Optional

import typer

from . import store, worktrees
from .cli_core import EXIT_USAGE, _catch_harness_errors, _fail, _print_result, app, eprint
from .cli_harness import _guard_harness, _run_harness, append_extra_prompt

@app.command("adhoc")
@_catch_harness_errors
def adhoc(
    refs: list[str] = typer.Argument(..., help="Worktree refs (key/branch/path)."),
    prompt: str = typer.Option("", "--prompt", help="Adhoc prompt body (required)."),
    launch: bool = typer.Option(False, "--launch", help="Run headless (default when neither --launch nor --terminal)."),
    terminal: bool = typer.Option(False, "--terminal", help="Run in OS terminal (XOR --launch)."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the prompt."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before commands."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Run an adhoc prompt on one or more linked worktrees, sequentially."""
    if launch and terminal:
        _fail("--terminal cannot be used with --launch (pick one run mode)", EXIT_USAGE)
    if not (refs or []):
        _fail("at least one worktree ref is required", EXIT_USAGE)
    if not (prompt or "").strip():
        _fail("--prompt is required and must not be blank", EXIT_USAGE)
    mode = "terminal" if terminal else "headless"
    use_launch = not terminal
    links = store.load_links()
    rows: list[dict] = []
    failed = False
    for ref in refs or []:
        try:
            key, entry, _ = worktrees.resolve_any(ref, links)
        except worktrees.NoLinkedState as e:
            eprint(f"{ref}: {e} — skipping")
            rows.append({"ref": ref, "skipped": str(e)})
            failed = True
            continue
        if (entry.get("active", 1) == 0):
            eprint(f"{key}: deactivated — skipping")
            rows.append({"key": key, "skipped": "deactivated"})
            failed = True
            continue
        wt = str(entry.get("worktree", ""))
        if not worktrees.is_valid_worktree(wt):
            eprint(f"{key}: invalid worktree {wt} — skipping")
            rows.append({"key": key, "skipped": "invalid worktree"})
            failed = True
            continue
        body = (prompt or "").strip()
        if (extra_prompt or "").strip():
            body = append_extra_prompt(body, extra_prompt.strip())
        if dry_run:
            rows.append({"key": key, "worktree": wt, "mode": mode,
                         "dry_run": True, "prompt_chars": len(body)})
            continue
        import sys as _sys
        if not yes and _sys.stdin.isatty():
            eprint(f"run adhoc prompt on {key} ({wt})? [y/N] ", end="")
            if (_sys.stdin.readline() or "").strip().lower() not in ("y", "yes"):
                _fail("aborted", EXIT_USAGE)
        _guard_harness(key, wt)
        harness_name = harness or store.load_config().get("default_harness", "omp")
        _run_harness(harness_name, body, wt, str(entry.get("repo", wt)),
                     True, use_launch, {"command": "adhoc",
                                        "session_metadata": {"action": "adhoc", "mode": mode}},
                     json_output, run_key=key,
                     session_file=session_file if isinstance(session_file, str) else None,
                     terminal=terminal, env_file=env_file)
        rows.append({"key": key, "worktree": wt, "mode": mode})
    _print_result({"results": rows}, json_output)
    if failed:
        import sys as _sys2
        _sys2.exit(1)
```

Register in `src/workagent/cli.py` next to line 198:
```python
from . import cli_adhoc  # noqa: F401  (registers `adhoc`)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_adhoc.py -v`
Expected: PASS (3 passed). Note: `_run_harness` with `{"command": "adhoc"}` yields NULL session_type until Task 2 lands — dry-run/usage tests don't execute the harness so they pass now.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_adhoc.py src/workagent/cli.py tests/test_cli_adhoc.py
git commit -m "feat: add adhoc command skeleton"
```

### Task 2: Session typing for `adhoc`

**Files:**
- Modify: `src/workagent/store_sessions.py:86-95` (`derive_session_type`)
- Test: `tests/test_cli_adhoc.py` (append)

**Interfaces:**
- Consumes: Task 1's `result={"command": "adhoc", "session_metadata": {...}}` shape (already emitted by `_run_harness` line 378 `metadata=result.get("session_metadata")`).
- Produces: `derive_session_type("adhoc") == "custom_action"` used by `_session_action` (`cli_harness.py:143-154`) and `_session_file_for` (`web_args.py:385-401`).

- [ ] **Step 1: Write the failing test**

```python
def test_adhoc_session_type_is_custom_action():
    from workagent import store_sessions as _ss
    assert _ss.derive_session_type("adhoc") == "custom_action"
```

Append to `tests/test_cli_adhoc.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_adhoc.py::test_adhoc_session_type_is_custom_action -v`
Expected: FAIL with `assert None == 'custom_action'`.

- [ ] **Step 3: Write minimal implementation**

In `src/workagent/store_sessions.py`, inside `derive_session_type` after the `c == "action"` branch:
```python
    if c in ("action", "adhoc"):
        return "custom_action"
```
replacing
```python
    if c == "action":
        return "custom_action"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_adhoc.py tests/test_sessions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sessions.py tests/test_cli_adhoc.py
git commit -m "feat: type adhoc sessions as custom_action"
```

### Task 3: Web passthrough + contract docs

**Files:**
- Modify: `src/workagent/web_args.py` (SPECS ~line 42, BOOL_FLAGS ~line 103, VAL_FLAGS ~line 110)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_webapp.py` (append confirm-key case)

**Interfaces:**
- Consumes: Task 1 argv shape `adhoc REF… --prompt … [--launch|--terminal] [--dry-run] [--yes] [--json] [--harness/--extra-prompt/--env-file/--session-file]`.
- Produces: `SPECS["adhoc"]`, `BOOL_FLAGS["adhoc"]`, `VAL_FLAGS["adhoc"]` consumed by `test_specs_mirror_cli_flags` and `webapp.SPECS`.

- [ ] **Step 1: Write the failing test**

```python
def test_adhoc_confirm_key_first_ref(client):
    r = client.post("/api/runs", json={"command": "adhoc", "args": ["jira:IPG-1", "--prompt", "hi", "--dry-run", "--yes"]})
    assert r.status_code in (200, 409)
```

Append to `tests/test_webapp.py` (check the file's existing `client` fixture name first and match it — the snippet above assumes `client`; adapt the fixture name to the file).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp.py -k adhoc -v`
Expected: FAIL (unknown command / 400).

- [ ] **Step 3: Write minimal implementation**

In `src/workagent/web_args.py`:
```python
    "adhoc": {"confirm": True, "force": False,
              "key": lambda args: f"adhoc:{_first_positional(args, VAL_FLAGS['adhoc'])}"},
```
BOOL_FLAGS: `"adhoc": ("--launch", "--terminal", "--dry-run", "--yes", "--json"),`
VAL_FLAGS: `"adhoc": ("--prompt", "--harness", "--extra-prompt", "--env-file", "--session-file"),`

Docs: `web/API_CONTRACT.md` — add row `adhoc REF… --prompt TEXT [--launch|--terminal] [--harness] [--extra-prompt] [--env-file] [--session-file] [--dry-run] [--yes] [--json]` (mutating, confirm:true, target key `adhoc:<first-ref>`). `web/FEATURE_INVENTORY.md` — add the CLI row with exit codes (2 usage, 1 any-row-failed).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webapp.py -q`
Expected: PASS including `test_specs_mirror_cli_flags`. Watch: `adhoc` is a
flat command (positional `refs`, no subcommand group) — if the mirror test
assumes a `SUBCOMMANDS` entry, handle `adhoc` the way flat commands like
`sync`/`cleanup` are treated; keep the change minimal and in the test data,
never in the mirror logic's core.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_webapp.py
git commit -m "feat: wire adhoc through web passthrough"
```

### Task 4: Frontend config — mode, copy, args builder

**Files:**
- Modify: `web/src/lib/launchConfig.ts` (Mode, MODES, COPY, LaunchForm, INITIAL, builder)
- Modify: `web/src/lib/launchPrefs.ts` (LaunchAdhocPrefs, load/save)
- Test: `web/src/lib/__tests__/launchConfig.test.ts` — only if that file exists; otherwise skip (throwaway-script rule, no new harness).

**Interfaces:**
- Consumes: `RunMode`, `runModeArgs`, `extraPromptArgs` (existing).
- Produces: `buildAdhocArgs(form)` consumed by Task 5's `buildArgs()` dispatch.

- [ ] **Step 1: Write the failing check**

If `web/src/lib/__tests__/launchConfig.test.ts` exists, append:
```ts
import { buildAdhocArgs } from "@/lib/launchConfig"
test("adhoc args order", () => {
  expect(buildAdhocArgs({ adhocRefs: "a\nb", adhocPrompt: "do x", runMode: "headless", extraPrompt: "" }))
    .toEqual(["adhoc", "a", "b", "--prompt", "do x", "--launch"])
})
```
Otherwise: no test file — verify by hand in Task 5 (record the manual check there).

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/lib/__tests__/launchConfig.test.ts` (or the repo's web test command)
Expected: FAIL with "buildAdhocArgs is not defined".

- [ ] **Step 3: Write minimal implementation**

```ts
export type Mode = "start" | "review" | "sync" | "flow" | "prodtest" | "adhoc"
export const MODES: readonly Mode[] = ["start", "review", "sync", "flow", "prodtest", "adhoc"]
```
COPY entry:
```ts
  adhoc: {
    label: "Adhoc",
    title: "Run an adhoc prompt",
    description: "Worktree key, issue/PR ref, or branch (one per line) — run a one-off prompt on each.",
    refHint: "Worktree refs, one per line.",
    refPlaceholder: "jira:IPG-1",
    confirmDesc: "Runs the adhoc prompt on each listed worktree in order.",
  },
```
Form fields: `LaunchForm += { adhocPrompt: string; adhocRefs: string }`, INITIAL `adhocPrompt: "", adhocRefs: ""`.
Builder:
```ts
/** CLI args for the adhoc tab: refs (one per line) + required --prompt. */
export function buildAdhocArgs(form: Pick<LaunchForm, "adhocRefs" | "adhocPrompt" | "runMode" | "extraPrompt">): string[] {
  const refs = form.adhocRefs.split("\n").map((r) => r.trim()).filter(Boolean)
  const args = ["adhoc", ...refs, "--prompt", form.adhocPrompt.trim()]
  args.push(...runModeArgs(form.runMode))
  args.push(...extraPromptArgs(form))
  return args
}
```
(The Launch page exposes no harness/session-file/env-file controls — same as
the review tab — so the builder omits them; the CLI flags remain for terminal use.)
Prefs:
```ts
export interface LaunchAdhocPrefs { runMode: RunMode }
export interface LaunchPrefs { start: …; review: …; prodtest: …; sync: LaunchSyncPrefs; adhoc: LaunchAdhocPrefs }
```
fallback `adhoc: { runMode: "headless" }`, load guard mirrors prodtest slice, save handled in Task 5.

- [ ] **Step 4: Run to verify it passes**

Run the vitest file (or `tsc --noEmit` if no test file).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/launchConfig.ts web/src/lib/launchPrefs.ts
git commit -m "feat: add adhoc launch config and prefs"
```

### Task 5: Launch tab UI

**Files:**
- Modify: `web/src/pages/Launch.tsx` (prefsForMode, LaunchOtherFields adhoc branch, RunModeSelect condition, buildArgs, flagList, persist effect deps, prefill from `ref` param)
- Test: manual — load `/launch?mode=adhoc&ref=jira:IPG-1`, submit dry-run, confirm args.

**Interfaces:**
- Consumes: Task 4's `buildAdhocArgs`, `COPY.adhoc`, `LaunchAdhocPrefs`.
- Produces: working Adhoc tab; `ref` URL param prefills `adhocRefs` (first line) for Task 6/7 navigation.

- [ ] **Step 1: Write the failing check**

Manual gate (no new harness): open `/launch?mode=adhoc` — tab missing. Record: tab absent before change.

- [ ] **Step 2: Run to verify it fails**

Load the page; confirm no Adhoc tab. (This is the "red" state.)

- [ ] **Step 3: Write minimal implementation**

1. `prefsForMode`: `if (mode === "adhoc") return { runMode: p.adhoc.runMode as LaunchForm["runMode"] }`.
2. `LaunchOtherFields`: `if (mode === "adhoc")` return refs textarea (`adhocRefs`, one per line) + prompt textarea (`adhocPrompt`) + repo select is NOT needed (refs resolve directly) + `ExtraPromptField`.
3. RunModeSelect render condition: `(mode === "review" || mode === "prodtest" || mode === "adhoc")`.
4. `buildArgs()`: `if (mode === "adhoc") return buildAdhocArgs(form)`.
5. `flagList`: adhoc shows `headless (--no-tty)` / `terminal (--terminal)` / `tty preview` entries via the shared runMode line (no per-mode special-casing needed beyond including adhoc in the non-sync branch — verify the existing ternary covers it).
6. Persist effect: `else if (mode === "adhoc") saveLaunchPrefs({ ...prev, adhoc: { runMode: form.runMode } })` + add `form.adhocPrompt`/`form.adhocRefs` to nothing (free text never persists — only runMode).
7. `ref` param prefill: where the page applies `refParam` to `form.ref`, also set `adhocRefs` when `mode === "adhoc"`.

- [ ] **Step 4: Run to verify it passes**

Manual: `/launch?mode=adhoc&ref=jira:IPG-1` shows Adhoc tab with ref prefilled, Headless selected by default, dry-run submit produces `adhoc jira:IPG-1 --prompt … --launch`. Plus `tsc --noEmit` clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/Launch.tsx
git commit -m "feat: add adhoc tab to Launch page"
```

### Task 6: Dashboard bulk entry + worktree detail button

**Files:**
- Modify: `web/src/components/DashboardBulkMenu.tsx` (props + item)
- Modify: `web/src/pages/Dashboard.tsx` (pass props, navigate to `/launch?mode=adhoc&ref=…`)
- Modify: `web/src/components/WorktreeDetail.tsx` (footer button → same deep-link)
- Test: manual — selection-aware label and navigation.

**Interfaces:**
- Consumes: Task 5's `?mode=adhoc&ref=` deep-link.
- Produces: no new interfaces.

- [ ] **Step 1: Write the failing check**

Manual gate: Bulk actions menu has no Adhoc entry; WorktreeDetail footer has no Adhoc button.

- [ ] **Step 2: Run to verify it fails**

Open Dashboard, confirm absent. (Red state.)

- [ ] **Step 3: Write minimal implementation**

`DashboardBulkMenuProps += { adhocLabel: string; adhocTitle: string; onAdhoc: () => void }`, items += `{ key: "adhoc", icon: null, label: props.adhocLabel, title: props.adhocTitle, onClick: () => run(props.onAdhoc) }` placed after the fix item.
`Dashboard.tsx`: `adhocLabel={scopeLabel("Adhoc")}` (reuses the "Adhoc (2)"/"Adhoc all" pattern), `onAdhoc={() => navigate("/launch?mode=adhoc" + (selectedKeys.length ? "&ref=" + encodeURIComponent(selectedKeys.join("\n")) : ""))}` — match the existing `handleSyncAll` selection plumbing (`selected` set at line 102; reuse its key list, all-worktrees when empty per the Sync All scope rule).
`WorktreeDetail.tsx` footer: outline button with `SquareTerminal`-adjacent icon (use `Zap` or reuse existing; check imports) labeled "Adhoc", `onClick={() => navigate("/launch?mode=adhoc&ref=" + encodeURIComponent(worktreeKey))}` — check how the footer gets `navigate` (add `useNavigate` if absent).

- [ ] **Step 4: Run to verify it passes**

Manual: select 2 rows → menu shows "Adhoc (2)" → navigates with both refs; no selection → all keys; detail footer button prefills single key. `tsc --noEmit` clean.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/DashboardBulkMenu.tsx web/src/pages/Dashboard.tsx web/src/components/WorktreeDetail.tsx
git commit -m "feat: add adhoc bulk and detail entries"
```
