"""`prodtest run`: execute the production test plan at the release SHA."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

from . import store, worktrees
from .cli_core import _catch_harness_errors, _fail, _print_result, eprint
from .cli_prodtest import _resolve_plan_file, prodtest_app
from .cli_prodtest_checks import _run_locked
from .errors import HarnessError

_FIX_MODES = ("report-only", "issue", "issue+mr")


@prodtest_app.command("run")
@_catch_harness_errors
def prodtest_run(
    ref: Optional[str] = typer.Argument(None, help="Issue ref or worktree ref of the released work (key/branch/path). Required without --all."),
    fix_mode: str = typer.Option("report-only", "--fix-mode", help="Follow-up policy: report-only, issue, or issue+mr."),
    skip_version_gate: bool = typer.Option(False, "--skip-version-gate", help="Proceed when the plan has no version_check (records skipped, low confidence)."),
    run_dir: Optional[str] = typer.Option(None, "--run-dir", help="Directory for run outputs (default: worktree .workagent/prodtest/runs/<ts>-<issue>/)."),
    launch: bool = typer.Option(False, "-L", "--launch", help="Launch the harness in the worktree (default: print the harness command)."),
    terminal: bool = typer.Option(False, "--terminal", help="Open the harness command in the OS terminal (mutually exclusive with --launch)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print run plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before harness commands."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the harness prompt."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path for the harness session."),
    all_wts: bool = typer.Option(False, "--all", help="Run every released/stored plan sequentially, continuing past per-key failures."),
) -> None:
    """Execute the production test plan for a released issue.

    Example:
      workagent prodtest run IPG-9 --no-tty
      workagent prodtest run --all --dry-run
    """
    if terminal and launch:
        _fail("--terminal cannot be used with --launch (pick one run mode)", 2)
    if fix_mode not in _FIX_MODES:
        _fail(f"bad --fix-mode {fix_mode!r}: use report-only, issue, or issue+mr", 2)
    if all_wts and ref is not None:
        _fail("ref is not accepted with --all (runs every eligible key)", 2)
    if all_wts and run_dir:
        _fail("--run-dir cannot be used with --all (one run dir per key)", 2)
    if all_wts and session_file:
        _fail("--session-file cannot be used with --all (one transcript per key)", 2)
    if not all_wts and ref is None:
        _fail("missing ref\n"
              "  Pass an issue/worktree ref, or use --all to run every released plan.", 2)
    if all_wts:
        _run_all(fix_mode, skip_version_gate, launch, terminal, no_tty,
                 dry_run, json_output, harness, env_file, extra_prompt)
        return
    assert ref is not None
    _run_one(ref, fix_mode, skip_version_gate, run_dir, launch, terminal,
             no_tty, dry_run, yes, json_output, harness, env_file,
             extra_prompt, session_file)


def _prodtest_eligibility(links: dict) -> tuple[list[tuple[str, dict, str, Path | None]], list[dict]]:
    """Eligible (key, entry, store_key, plan_file) runs + skip rows for ``--all``.

    Eligible = active link + prodtest row with non-empty ``release_sha`` and a
    plan file that resolves — the stored ``plan_path`` when it exists, else
    the canonical on-disk plan under the linked worktree. Everything else
    becomes a ``skipped:<reason>`` row — never torn down, never failed.
    Never raises on store errors.
    """
    from . import prodtest_lock as _lock
    eligible: list[tuple[str, dict, str, Path | None]] = []
    skipped: list[dict] = []
    lock_base = str(store.config_dir())
    for key in sorted(links):
        entry = links.get(key) or {}
        if entry.get("active", 1) == 0:
            skipped.append({"key": key, "status": "skipped:deactivated"})
            continue
        store_key = str(entry.get("issue", "") or "") or key
        try:
            item = store.get_prodtest(store_key)
        except Exception:
            item = None
        if not item or not str((item or {}).get("release_sha", "") or ""):
            skipped.append({"key": key, "status": "skipped:no-release"})
            continue
        plan_file = _resolve_plan_file(entry, store_key, str((item or {}).get("plan_path", "") or ""))
        if plan_file is None:
            skipped.append({"key": key, "status": "skipped:no-plan"})
            continue
        wt = str(entry.get("worktree", "") or "")
        if wt and not Path(wt).is_dir():
            skipped.append({"key": key, "status": "skipped:invalid-worktree"})
            continue
        try:
            if _lock.is_locked(lock_base, store_key):
                skipped.append({"key": key, "status": "skipped:in-progress"})
                continue
        except Exception:
            pass
        eligible.append((key, dict(entry), store_key, plan_file))
    return eligible, skipped


def _run_all(fix_mode: str, skip_version_gate: bool, launch: bool, terminal: bool,
             no_tty: bool, dry_run: bool, json_output: bool,
             harness: Optional[str], env_file: str | None,
             extra_prompt: Optional[str]) -> None:
    """Sequential in-process ``--all`` loop: one key at a time, past failures."""
    from . import cli_harness as _h
    links = store.load_links(include_deleted=True)
    eligible, skipped = _prodtest_eligibility(links)
    rows: list[dict] = list(skipped)
    if dry_run:
        for key, entry, store_key, _plan in eligible:
            cmd = f"prodtest run {store_key} --no-tty"
            if launch:
                cmd += " --launch"
            rows.append({"key": key, "issue": store_key, "command": cmd,
                         "status": "dry-run"})
        _print_result({"results": rows, "dry_run": True}, json_output)
        return
    if not eligible:
        eprint("nothing to run")
        _print_result({"results": rows}, json_output)
        return
    if not launch:
        for key, entry, store_key, _plan in eligible:
            argv = [sys.executable, "-m", "workagent", "prodtest", "run",
                    store_key, "--no-tty"]
            _h.record_headless_attempt(
                key, "prodtest run", argv,
                f"harness command: prodtest run {store_key} --no-tty",
                exit_code=0, metadata={"mode": "all-preview"})
            rows.append({"key": key, "issue": store_key, "status": "preview",
                         "command": f"prodtest run {store_key} --no-tty"})
        _print_result({"results": rows}, json_output)
        return
    failures = 0
    for key, entry, store_key, _plan in eligible:
        try:
            _run_one(store_key, fix_mode, skip_version_gate, None, launch,
                     terminal, no_tty, False, True, json_output, harness,
                     env_file, extra_prompt, None)
            rows.append({"key": key, "issue": store_key, "status": "ok"})
        except SystemExit as e:
            failures += 1
            rows.append({"key": key, "issue": store_key, "status": "failed",
                         "exit_code": getattr(e, "code", 1)})
            eprint(f"{key}: run failed (exit {getattr(e, 'code', 1)}); continuing")
        except Exception as e:
            failures += 1
            rows.append({"key": key, "issue": store_key, "status": "failed",
                         "error": str(e)})
            eprint(f"{key}: run failed ({e}); continuing")
    if not json_output:
        for row in rows:
            eprint(f"{row.get('key')}: {row.get('status')}")
    _print_result({"results": rows,
                   "failed": failures}, json_output)
    if failures:
        raise SystemExit(1)


def _run_one(ref: str,
             fix_mode: str, skip_version_gate: bool, run_dir: str | None,
             launch: bool, terminal: bool, no_tty: bool, dry_run: bool, yes: bool,
             json_output: bool, harness: Optional[str], env_file: str | None,
             extra_prompt: Optional[str], session_file: str | None) -> None:
    """Single-ref run body (``resolve -> out_dir -> lock -> _run_locked``)."""
    if terminal and launch:
        _fail("--terminal cannot be used with --launch (pick one run mode)", 2)
    if fix_mode not in _FIX_MODES:
        _fail(f"bad --fix-mode {fix_mode!r}: use report-only, issue, or issue+mr", 2)
    if not isinstance(session_file, str):
        session_file = None
    if not isinstance(env_file, str):
        env_file = None
    if not isinstance(run_dir, str):
        run_dir = None

    key, entry, parsed = worktrees.resolve_any(ref, store.load_links(include_deleted=True))
    issue_number: str | None = None
    if entry.get("issue"):
        issue_number = str(entry.get("issue", "") or "") or None
    if parsed is not None and parsed.get("kind") == "issue":
        issue_number = str(parsed.get("number", "") or "") or issue_number
    if not issue_number and key.startswith("jira:"):
        issue_number = key.split(":", 1)[1] or None
    store_key = issue_number or key
    item = store.get_prodtest(store_key)
    release_sha = str((item or {}).get("release_sha", "") or "")
    if not item or not release_sha:
        _fail(f"no release recorded for {store_key}; release first", 2)

    plan_path = str((item or {}).get("plan_path", "") or "")
    plan_file = _resolve_plan_file(entry, store_key, plan_path)
    issue_url = str(entry.get("issue_url", "") or "")

    if run_dir:
        out_dir = Path(run_dir)
    elif entry.get("worktree"):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "-" for c in store_key)
        out_dir = Path(str(entry["worktree"])) / ".workagent" / "prodtest" / "runs" / f"{ts}-{safe}"
    else:
        out_dir = Path.cwd() / ".workagent" / "prodtest" / "runs" / store_key

    if dry_run:
        _print_result({"key": key, "issue": store_key, "run_dir": str(out_dir),
                       "release_sha": release_sha,
                       "plan_file": str(plan_file) if plan_file else plan_path,
                       "fix_mode": fix_mode, "skip_version_gate": skip_version_gate,
                       "dry_run": True}, json_output)
        return

    from . import prodtest_lock as _lock
    from . import prodtest_scripts as _ps
    from .release_util import worktree_base

    lock_base = str(store.config_dir())
    # No is_locked pre-check here: held_issue_lock below is the single guard.
    # A pre-check would race (two runners can both pass before either acquires).
    timeout_s = _run_timeout_s(plan_file, skip_version_gate)

    try:
        with _lock.held_issue_lock(lock_base, store_key):
            # Re-fetch under the lock: the row may have changed between the
            # pre-lock read (release_sha check) and acquiring the lock.
            fresh_item = store.get_prodtest(store_key)
            _run_locked(store_key, entry, plan_file, release_sha, issue_url,
                        fix_mode, skip_version_gate, out_dir, timeout_s,
                        launch, terminal, no_tty, json_output,
                        harness, env_file, extra_prompt, session_file,
                        item=fresh_item if fresh_item is not None else item)
    except KeyboardInterrupt:
        _fail("interrupted; run checkout kept for inspection.", 1)


def _run_timeout_s(plan_file: Path | None, skip_version_gate: bool) -> int:
    """Budget + version-gate + slack timeout (seconds) from the plan file."""
    budget_min, gate_s = 15, 300
    if plan_file is not None and plan_file.exists():
        try:
            import yaml
            plan = yaml.safe_load(plan_file.read_text(encoding="utf-8")) or {}
            budget = plan.get("budget") or {}
            if isinstance(budget.get("max_minutes"), int):
                budget_min = budget["max_minutes"]
            gate = ((plan.get("version_gate") or {}) if isinstance(plan.get("version_gate"), dict) else {})
            if isinstance(gate.get("timeout_s"), int):
                gate_s = gate["timeout_s"]
            else:
                vc = plan.get("version_check") or {}
                if isinstance(vc, dict) and isinstance(vc.get("timeout_s"), int):
                    gate_s = vc["timeout_s"]
        except Exception:
            pass
    if skip_version_gate:
        gate_s = 0
    return budget_min * 60 + gate_s + 60




