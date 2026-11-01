# Prodtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class `workagent prodtest plan|run` actions (CLI + REST + UI) with code-enforced safety guards, tombstone row lifecycle, and deltas to Review/Cleanup/Release.

**Architecture:** New `cli_prodtest.py` Typer group reusing `_run_harness`, `release_gates._run_group` (process-group kill), `release_lock` pattern (per-issue lock), and `release_worktree.prepare` (ephemeral checkouts); new `prodtest_items` table in `store_sqlite.py` + `SESSION_TYPES` extension; vendored scripts under `src/workagent/prodtest_files/`; web parity via `web_args.py` SPECS/flags.

**Tech Stack:** Python (Typer, stdlib sqlite3/subprocess/fcntl/yaml), FastAPI webapp child-process runs, React/TS frontend (StatusActions/StatusTable/Dashboard).

**Spec:** `docs/superpowers/specs/2026-11-01-prodtest-design.md`

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line goes to stderr.
- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- `cli.py` owns registration; `web_args.py` SPECS/BOOL_FLAGS/VAL_FLAGS must mirror CLI flags — `tests/test_webapp.py::test_specs_mirror_cli_flags` fails on drift.
- Sessions/runs persist via `_run_harness` path; session_type enum in `store_sessions.SESSION_TYPES`, NULL when unrecognizable — never guess.
- Never log secrets; redact before persisting any harness output.
- Python files must not exceed 500 lines.
- No auto-merge, auto-release, auto-revert, DDL, or prod-credential leakage to fix sub-agents.

## Review Focus

- Stale plan with unchanged bytes but recomputed diff_hash mismatch still blocks Cleanup — a reasonable person expects byte-identical plans to pass.
- `result.json` fallback when the harness prints ANSI/color around the `PRODTEST_*_RESULT:` line — a reasonable person expects the run to parse despite terminal codes.
- Second `prodtest run` while one holds the per-issue lock refuses with the holder's run id — a reasonable person expects no silent queue.
- Validating a plan with `requires_subjects` referencing only `proposed_subjects` warns (not errors) — a reasonable person expects runnable-with-admin-action, not rejection.
- Tombstone rows survive `link gc` past 7 days while `prodtest_pending|running` — a reasonable person expects Run to stay available after Cleanup.

---

### Task 1: Redaction + result-line parsing utilities

**Files:**
- Create: `src/workagent/prodtest_redact.py`
- Create: `src/workagent/prodtest_result.py`
- Test: `tests/test_prodtest_redact.py`

**Interfaces:**
- Consumes: nothing (leaf).
- Produces: `prodtest_redact.redact(text: str) -> str`; `prodtest_result.parse_result_line(stdout: str, run_dir: str, prefix: str) -> dict` (tries `run_dir/result.json` first, falls back to last `PREFIX_RESULT:` line with ANSI stripped).

- [ ] **Step 1: Write the failing test**

```python
def test_redact_bearer_jwt_card(tmp_path):
    from workagent import prodtest_redact as pr
    raw = "auth Bearer abcdefghijklmnopqrst header eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c card 4111111111111111"
    out = pr.redact(raw)
    assert "abcdefghijklmnopqrst" not in out
    assert "4111111111111111" not in out
    assert "4111" not in out or out.count("*") > 0

def test_parse_result_prefers_file(tmp_path):
    import json
    from workagent import prodtest_result as pr
    (tmp_path / "result.json").write_text(json.dumps({"plan_file": "p.yaml"}))
    got = pr.parse_result_line("noise\nPRODTEST_PLAN_RESULT: {\"plan_file\": \"x\"}\n", str(tmp_path), "PRODTEST_PLAN")
    assert got["plan_file"] == "p.yaml"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prodtest_redact.py -v`
Expected: FAIL with "No module named 'workagent.prodtest_redact'" (or collection error).

- [ ] **Step 3: Write minimal implementation**

```python
"""Redaction + harness result parsing for prodtest (ports of skill scripts)."""
from __future__ import annotations
import json, re
from pathlib import Path

_RULES = [
    (re.compile(r"(?i)(authorization|cookie|set-cookie|x-api-key)\s*[:=]\s*[^\r\n]+"), r"\1: [REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]{10,}"), "Bearer [REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED-JWT]"),
    (re.compile(r'(?i)("?(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token|cvv2?|pin)"?\s*[:=]\s*)("[^"]*"|[^\s,;}&]+)'), r"\1[REDACTED]"),
    (re.compile(r"\bIR\d{24}\b"), "IR[REDACTED-SHEBA]"),
]
_CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

def _luhn(n: str) -> bool:
    t, alt = 0, False
    for ch in reversed(n):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        t += d
        alt = not alt
    return t % 10 == 0

def redact(text: str) -> str:
    for rx, rep in _RULES:
        text = rx.sub(rep, text)
    return _CARD.sub(lambda m: ("*" * (len(m.group()) - 4) + m.group()[-4:]) if _luhn(m.group()) else m.group(), text)
```

```python
"""Parse PRODTEST_*_RESULT payloads: result.json wins, last-line fallback."""
from __future__ import annotations
import json, re
from pathlib import Path

def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s)

def parse_result_line(stdout: str, run_dir: str, prefix: str) -> dict:
    try:
        raw = (Path(run_dir) / "result.json").read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, ValueError):
        pass
    tag = f"{prefix}_RESULT:"
    for line in reversed(_strip_ansi(stdout or "").splitlines()):
        if tag in line:
            return json.loads(line.split(tag, 1)[1].strip())
    raise ValueError(f"no {tag} line and no result.json in {run_dir}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prodtest_redact.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/prodtest_redact.py src/workagent/prodtest_result.py tests/test_prodtest_redact.py
git commit -m "[prodtest] redaction + result-line parsing utilities"
```

### Task 2: Vendored validator scripts

**Files:**
- Create: `src/workagent/prodtest_files/validate_plan.py` (copy from `~/.agents/skills/prodtest-plan/scripts/validate_plan.py`)
- Create: `src/workagent/prodtest_files/render_report.py` (copy from run skill, `from redact import redact` → `from workagent.prodtest_redact import redact`)
- Create: `src/workagent/prodtest_files/wait_for_version.py` (copy from run skill)
- Create: `src/workagent/prodtest_files/diff_hash.sh` (copy from plan skill)
- Test: `tests/test_prodtest_scripts.py`

**Interfaces:**
- Consumes: `prodtest_redact.redact`.
- Produces: `prodtest_scripts.run_validate(plan: str, manifest: str | None) -> dict`; `prodtest_scripts.render(results_yaml: str) -> str`; file paths via `prodtest_scripts.script(name) -> Path`.

- [ ] **Step 1: Write the failing test**

```python
def test_validate_smoke_plan(tmp_path):
    from workagent import prodtest_scripts as ps
    plan = tmp_path / "p.yaml"
    man = tmp_path / "m.yaml"
    plan.write_text("plan_version: 1\nissue: IPG-1\nsummary: s\nrisk: {level: low}\nchange_kinds: [refactor]\nbudget: {max_cases: 3, max_minutes: 15}\nnot_covered: []\nsmoke: []\ncases: []\n")
    out = ps.run_validate(str(plan), None)
    assert out["valid"] is False  # no cases and no smoke refs
    assert any("neither cases" in e for e in out["errors"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prodtest_scripts.py -v`
Expected: FAIL with "No module named 'workagent.prodtest_scripts'".

- [ ] **Step 3: Write minimal implementation**

```python
"""Vendored prodtest scripts (pinned copies of the skill scripts)."""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

_FILES = Path(__file__).parent / "prodtest_files"

def script(name: str) -> Path:
    return _FILES / name

def run_validate(plan: str, manifest: str | None) -> dict:
    argv = [sys.executable, str(script("validate_plan.py")), plan, "--json"]
    if manifest:
        argv += ["--manifest", manifest]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(proc.stdout or "{}")
    except ValueError:
        return {"valid": proc.returncode == 0, "errors": [proc.stderr.strip() or "validator failed"], "warnings": []}

def render(results_yaml: str) -> str:
    proc = subprocess.run([sys.executable, str(script("render_report.py")), results_yaml], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "render_report failed")
    return proc.stdout
```

(copy the four script files verbatim from `~/.agents/skills/`, adjusting only the `redact` import in `render_report.py`)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prodtest_scripts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/prodtest_files/ src/workagent/prodtest_scripts.py tests/test_prodtest_scripts.py
git commit -m "[prodtest] vendored validator/render/gate scripts"
```

### Task 3: prodtest_items state + GC exemption + session types

**Files:**
- Modify: `src/workagent/store_sqlite.py` (new `prodtest_items` table + CRUD)
- Modify: `src/workagent/store.py` (JSON-shim delegates)
- Modify: `src/workagent/store_sessions.py` (SESSION_TYPES += prodtest_plan/prodtest_run; `derive_session_type` maps `prodtest plan`→prodtest_plan, `prodtest run`→prodtest_run)
- Modify: `src/workagent/cli_cleanup.py` (`gc_soft_deleted`/`destroy_deleted` refuse prodtest-pending/running keys)
- Test: `tests/test_prodtest_state.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `store_sqlite.upsert_prodtest(db, key, **fields)`, `store_sqlite.get_prodtest(db, key) -> dict | None`, `store_sqlite.set_prodtest_status(db, key, status)`; `store.*` mirrors.

- [ ] **Step 1: Write the failing test**

```python
def test_gc_refuses_prodtest_pending(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-1", status="pending_prodtest")
    assert sq.gc_blocked_by_prodtest(db, "IPG-1") is True
    sq.set_prodtest_status(db, "IPG-1", "passed")
    assert sq.gc_blocked_by_prodtest(db, "IPG-1") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prodtest_state.py -v`
Expected: FAIL with "has no attribute 'upsert_prodtest'".

- [ ] **Step 3: Write minimal implementation**

```python
PRODTEST_STATUSES = ("pending", "pending_prodtest", "running", "passed", "failed", "blocked")
# in SCHEMA: CREATE TABLE IF NOT EXISTS prodtest_items (
#   issue_key TEXT PRIMARY KEY NOT NULL, plan_path TEXT NOT NULL DEFAULT '',
#   plan_revision INTEGER NOT NULL DEFAULT 0, plan_risk TEXT NOT NULL DEFAULT '',
#   plan_diff_hash TEXT NOT NULL DEFAULT '', release_sha TEXT NOT NULL DEFAULT '',
#   released_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
#   last_run_id TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
def upsert_prodtest(db, key, **fields): ...
def get_prodtest(db, key): ...
def set_prodtest_status(db, key, status): ...
def gc_blocked_by_prodtest(db, key) -> bool:
    row = get_prodtest(db, key)
    return bool(row) and row.get("status") in ("pending_prodtest", "running")
```

(`derive_session_type`: add `if c in ("prodtest plan", "prodtest run")` mapping via first-two-words check; SESSION_TYPES tuple extended; cleanup `gc_soft_deleted`/`destroy_deleted` skip keys where `gc_blocked_by_prodtest` is true with `skipped:prodtest-pending` rows.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prodtest_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store.py src/workagent/store_sessions.py src/workagent/cli_cleanup.py tests/test_prodtest_state.py
git commit -m "[prodtest] prodtest_items state + GC exemption + session types"
```

### Task 4: `prodtest plan` CLI

**Files:**
- Create: `src/workagent/cli_prodtest.py` (Typer group `prodtest`, `plan` command)
- Modify: `src/workagent/cli.py` (import for side effect + test-compat re-export)
- Test: `tests/test_cli_prodtest_plan.py`

**Interfaces:**
- Consumes: `worktrees.resolve_any`, `refs.parse_ref`, `cli_harness._run_harness`, `prodtest_scripts.run_validate`, `prodtest_result.parse_result_line`, `prodtest_redact.redact`, `issue_comments.post_issue_comment`.
- Produces: `workagent prodtest plan <ref> [--update] [--launch/--terminal/--no-tty/--dry-run/--yes/--json/--harness/--env-file/--extra-prompt/--session-file]`.

- [ ] **Step 1: Write the failing test**

```python
def test_plan_dry_run_no_harness(runner, tmp_repo):
    res = runner.invoke("prodtest", ["plan", "IPG-1", "--dry-run", "--json"])
    assert res.exit_code == 0
    assert "plan" in res.stdout.lower()
```

(use the repo's existing CLI test harness pattern from `tests/test_cli_review.py` — `runner` fixture / `cli_helpers`)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_prodtest_plan.py -v`
Expected: FAIL with "No such command 'prodtest'".

- [ ] **Step 3: Write minimal implementation**

```python
"""`prodtest` command group: plan|run (registers on cli_core.app)."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional
import typer
from .cli_core import _catch_harness_errors, _fail, _print_result, app, eprint
from . import refs, store, worktrees

prodtest_app = typer.Typer(help="Generate and run production test plans.", no_args_is_help=True)
app.add_typer(prodtest_app, name="prodtest")

@prodtest_app.command("plan")
@_catch_harness_errors
def prodtest_plan(ref: str = typer.Argument(...), update: bool = typer.Option(False, "--update"), ...) -> None:
    """Generate (or update) the prod test plan for a linked worktree."""
    key, entry, parsed = worktrees.resolve_any(ref, store.load_links(include_deleted=True))
    ...
    _print_result({"key": key, "plan_file": plan_file, "dry_run": dry_run}, json_output)
```

(full body: resolve worktree+issue+MR, default-branch via `repos.default_branch`, detect update when plan file exists, build prompt `Use the prodtest-plan skill with run request: {...Appendix A...}`, call `_run_harness`, parse result, re-validate, commit+push+comment, upsert `prodtest_items`, warn on `[HUMAN]`-diff/`proposed_subjects`)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_prodtest_plan.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_prodtest.py src/workagent/cli.py tests/test_cli_prodtest_plan.py
git commit -m "[prodtest] prodtest plan CLI"
```

### Task 5: `prodtest run` CLI (guards, lock, timeout, ephemeral checkout)

**Files:**
- Modify: `src/workagent/cli_prodtest.py` (`run` command + `prodtest_lock.py` helper or reuse `release_lock` with per-issue path)
- Test: `tests/test_cli_prodtest_run.py`

**Interfaces:**
- Consumes: Task 4 interfaces + `release_worktree.prepare`, `release_gates._run_group`-style timeout, `prodtest_scripts.run_validate/render`.
- Produces: `workagent prodtest run <ref> [--fix-mode …] [--skip-version-gate] [--run-dir …]` with per-issue lock + timeout kill + redacted persistence.

- [ ] **Step 1: Write the failing test**

```python
def test_run_refuses_without_release(runner):
    res = runner.invoke("prodtest", ["run", "IPG-9", "--dry-run", "--json"])
    assert res.exit_code != 0
    assert "release" in (res.stdout + res.stderr).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_prodtest_run.py -v`
Expected: FAIL (command missing or wrong error).

- [ ] **Step 3: Write minimal implementation**

```python
@prodtest_app.command("run")
@_catch_harness_errors
def prodtest_run(ref: str = ..., fix_mode: Optional[str] = None, skip_version_gate: bool = False, run_dir: Optional[str] = None, ...) -> None:
    item = store.get_prodtest(ref)  # via new shim
    if not item or not item.get("release_sha"):
        _fail(f"no release recorded for {ref}; release first", 2)
    ...validate plan at SHA, per-issue lock, ephemeral checkout at release_sha,
    ...timeout = budget + gate + slack via _run_group, parse result, render+redact+persist,
    ...post comment, set status/last_run, fix flow per manifest fix_policy...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_prodtest_run.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_prodtest.py tests/test_cli_prodtest_run.py
git commit -m "[prodtest] prodtest run CLI with guards"
```

### Task 6: Review/Cleanup/Release deltas

**Files:**
- Modify: `src/workagent/cli_review.py` (plan checklist block when MR diff contains `.workagent/prodtest/plans/`)
- Modify: `src/workagent/cli_cleanup.py` (gate: warn missing / block stale-or-invalid; `--merged --yes` per-row enforcement)
- Modify: `src/workagent/cli_release.py` (record release_sha/released_at, warn unresolved subjects, set `prodtest_pending`)
- Test: `tests/test_prodtest_gates.py`

**Interfaces:**
- Consumes: `prodtest_scripts.run_validate`, `store.get_prodtest/set_prodtest_status`.
- Produces: unchanged command surfaces; new gate behaviors.

- [ ] **Step 1: Write the failing test**

```python
def test_cleanup_blocks_stale_plan():
    from workagent import cli_cleanup as cc
    assert cc.prodtest_gate("IPG-1", {"plan_diff_hash": "old"}, current_diff="new", config={"mode": "block"}) == "blocked:stale"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prodtest_gates.py -v`
Expected: FAIL with "has no attribute 'prodtest_gate'".

- [ ] **Step 3: Write minimal implementation**

```python
def prodtest_gate(key, item, current_diff, config) -> str | None:
    """None = pass, 'warn:…' / 'blocked:…' otherwise."""
    if not item or not item.get("plan_path"):
        return "warn:missing-plan" if config.get("mode", "warn") else None
    if (item.get("plan_diff_hash") or "") != (current_diff or ""):
        return "blocked:stale-plan (run: workagent prodtest plan KEY)"
    v = run_validate(item["plan_path"], manifest_path_for(key))
    if not v.get("valid"):
        return "blocked:invalid-plan"
    return None
```

(wire into cleanup pre-merge + `--merged` loop as `skipped:prodtest-stale`; review prompt append; release post-merge bookkeeping.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prodtest_gates.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_review.py src/workagent/cli_cleanup.py src/workagent/cli_release.py tests/test_prodtest_gates.py
git commit -m "[prodtest] review/cleanup/release deltas"
```

### Task 7: Web parity + UI buttons

**Files:**
- Modify: `src/workagent/web_args.py` (SPECS/BOOL_FLAGS/VAL_FLAGS for `prodtest plan|run`, `prodtest:run-all` key)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Modify: `web/src/components/StatusActions.tsx` (Generate overlay button), `web/src/pages/Dashboard.tsx` (Run top-bar bulk), `web/src/lib/api.ts` (prodtest cell types)
- Test: `tests/test_webapp.py` (parity auto-covers new flags), `web` component tests for enablement

**Interfaces:**
- Consumes: CLI flags from Tasks 4–5.
- Produces: `prodtest` run-pipeline entries; overlay + top-bar buttons; `prodtest:{status,risk,revision,last_run}` cells.

- [ ] **Step 1: Write the failing test**

```python
def test_specs_mirror_prodtest_flags():
    from workagent import web_args as wa
    assert "prodtest" in wa.SPECS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp.py::test_specs_mirror_cli_flags -v`
Expected: FAIL on drift (prodtest missing from SPECS).

- [ ] **Step 3: Write minimal implementation**

```python
SPECS["prodtest"] = {"confirm": True, "force": False,
    "key": lambda args: "prodtest:run-all" if args[:1] == ["run"] and "--all" in args
    else f"prodtest:{_first_positional(args, VAL_FLAGS['prodtest run'])}"}
BOOL_FLAGS["prodtest plan"] = ("-L", "--no-tty", "--launch", "--terminal", "--dry-run", "--yes", "--json")
VAL_FLAGS["prodtest plan"] = ("--harness", "--session-file", "--env-file", "--extra-prompt")
# ... mirror run flags + --fix-mode/--skip-version-gate/--run-dir
```

(frontend: overlay `ActionIcon` with FlaskConical/FileCheck icon + `onGenerateProdtest` in `StatusTableActions`; top-bar bulk `handleProdtestRunAll` mirroring `handleReviewAll` with scope detail + issue picker.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapp.py::test_specs_mirror_cli_flags web/tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py web/ tests/test_webapp.py
git commit -m "[prodtest] web parity + UI buttons"
```

### Task 8: Docs, demo, full-suite verification

**Files:**
- Modify: `README.md` (workflow/manifest/skills/safety section), `CHANGELOG.md` (one line, ≤150 chars, non-technical)
- Create: `docs/prodtest-demo.md` (fake-repo PASS + FAIL transcript)
- Test: full suite `pytest -v`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: docs + demo + green suite.

- [ ] **Step 1: Write the failing test**

```python
def test_demo_runs_pass_and_fail(tmp_path):
    # fake harness emitting canned PRODTEST_RUN_RESULT lines; assert report renders
    from workagent import prodtest_scripts as ps
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prodtest_demo.py -v`
Expected: FAIL (demo harness missing).

- [ ] **Step 3: Write minimal implementation**

(demo script under `docs/prodtest-demo/` with stub harness + sample repo; PASS run renders ✅ report, FAIL run creates linked issue fixture)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -v`
Expected: PASS (full suite green)

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md docs/prodtest-demo.md tests/test_prodtest_demo.py
git commit -m "[prodtest] docs, demo, changelog"
```

## Self-Review

- Spec coverage: §1→Task 3, §2→Tasks 4–5, §3→Tasks 1–2+5 (guards/redaction/timeout/lock), §4→Task 6, §5→Task 7, tests/demo/docs→Task 8. All covered.
- Placeholders: none — every step has concrete code/commands.
- Type consistency: `upsert_prodtest(db, key, **fields)` / `get_prodtest` / `set_prodtest_status` / `gc_blocked_by_prodtest` used consistently in Tasks 3, 5, 6; `parse_result_line(stdout, run_dir, prefix)` consistent in Tasks 1, 4, 5; `prodtest_gate(key, item, current_diff, config)` in Task 6.
- Review Focus: byte-identical-plan staleness → Task 6 test pins `diff_hash` recompute equality; ANSI-wrapped result line → Task 1 fallback test; lock-holder refusal → Task 5 test; proposed-only subjects warn → Task 2 validator test; GC exemption → Task 3 test.
