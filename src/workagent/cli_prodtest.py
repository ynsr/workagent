"""`prodtest` command group: plan + run."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from . import refs, repos, store, worktrees
from .cli_core import _catch_harness_errors, _fail, _print_result, app, eprint
from .cli_harness import _run_harness, append_extra_prompt
from .errors import HarnessError

prodtest_app = typer.Typer(help="Generate and run production test plans.", no_args_is_help=True)
app.add_typer(prodtest_app, name="prodtest")


def _plan_filename(issue_number: str | None, mr_number: str | None) -> str:
    if issue_number:
        safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "-" for c in issue_number)
        return f"{safe}.yaml"
    return f"mr-{mr_number or 'unknown'}.yaml"


@prodtest_app.command("plan")
@_catch_harness_errors
def prodtest_plan(
    ref: str = typer.Argument(..., help="Issue ref, MR/PR URL, or worktree ref (key/branch/path)."),
    update: bool = typer.Option(False, "--update", help="Update the existing plan instead of creating it."),
    launch: bool = typer.Option(False, "-L", "--launch", help="Launch the harness in the worktree (default: print the harness command)."),
    terminal: bool = typer.Option(False, "--terminal", help="Open the harness command in the OS terminal (mutually exclusive with --launch)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before harness commands."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the harness prompt."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path for the harness session."),
) -> None:
    """Generate (or update) the prod test plan for a linked worktree."""
    if terminal and launch:
        _fail("--terminal cannot be used with --launch (pick one run mode)", 2)
    if not isinstance(session_file, str):
        session_file = None
    if not isinstance(env_file, str):
        env_file = None

    key, entry, parsed = worktrees.resolve_any(ref, store.load_links(include_deleted=True))
    if not entry or not entry.get("worktree"):
        _fail(f"no linked worktree for {ref}", 2)
    worktree = str(entry.get("worktree", ""))
    branch = str(entry.get("branch", "") or "")
    repo = str(entry.get("repo", "") or "")

    try:
        default_branch = repos.default_branch(Path(repo)) if repo and Path(repo).is_dir() else "main"
    except HarnessError:
        default_branch = "main"

    issue_number: str | None = None
    issue_url: str = str(entry.get("issue_url", "") or "")
    mr_url: str | None = None
    if parsed is not None:
        if parsed.get("kind") == "issue":
            issue_number = str(parsed.get("number", "") or "") or None
            if not issue_url and parsed.get("url"):
                issue_url = str(parsed.get("url", ""))
        elif parsed.get("kind") in ("pr", "mr"):
            mr_url = str(parsed.get("url", "") or "") or None
    if not issue_number and entry.get("issue"):
        issue_number = str(entry.get("issue", "") or "") or None
    if not mr_url:
        try:
            mr_url = worktrees.worktree_pr_url(key, entry)
        except HarnessError:
            mr_url = None
    mr_number: str | None = None
    if mr_url:
        try:
            mr_parsed = refs.parse_ref(mr_url)
            if mr_parsed.get("kind") in ("pr", "mr"):
                mr_number = str(mr_parsed.get("number", "") or "") or None
        except HarnessError:
            mr_number = None

    plan_file = Path(worktree) / ".workagent" / "prodtest" / "plans" / _plan_filename(issue_number, mr_number)
    auto_update = plan_file.exists()
    update_mode = bool(update or auto_update)

    harness_name = harness or store.load_config().get("default_harness", "omp")

    if dry_run:
        _print_result({"key": key, "plan_file": str(plan_file), "update": update_mode,
                       "dry_run": True, "worktree": worktree, "branch": branch,
                       "default_branch": default_branch, "harness": harness_name}, json_output)
        return

    run_request = {"worktree": worktree, "branch": branch,
                   "issue": {"key": issue_number, "url": issue_url or None},
                   "mr": {"url": mr_url} if mr_url else None,
                   "default_branch": default_branch, "update": update_mode,
                   "plan_file": str(plan_file)}
    prompt = ("Use the prodtest-plan skill with run request: "
              + json.dumps(run_request, indent=2))
    prompt = append_extra_prompt(prompt, extra_prompt)
    result: dict = {"worktree_path": worktree, "branch": branch, "command": "prodtest plan",
                    "key": key, "plan_file": str(plan_file), "update": update_mode}
    eprint(f"worktree: {worktree}  branch: {branch}")
    _run_harness(harness_name, prompt, worktree, repo or worktree,
                 no_tty, launch, result, json_output,
                 run_key=key, session_file=session_file,
                 terminal=terminal, env_file=env_file)
    if launch and no_tty:
        _finish_plan(key, issue_number, worktree, repo, branch, plan_file,
                     issue_url, result)


def _finish_plan(key: str, issue_number: str | None, worktree: str, repo: str,
                 branch: str, plan_file: Path, issue_url: str, result: dict) -> None:
    """Best-effort post-harness: parse result, validate, commit+push, comment, upsert."""
    from . import harness_summary as _hs
    from . import issue_comments as _ic
    from . import prodtest_result as _pr
    from . import prodtest_scripts as _ps
    from .errors import run_cmd

    session_file = str(result.get("session_file", "") or "")
    stdout_text = ""
    if session_file:
        try:
            stdout_text = _hs.last_assistant_text(session_file)
        except Exception as e:
            eprint(f"warning: summary parse failed: {e}")
    payload: dict = {}
    try:
        payload = _pr.parse_result_line(stdout_text, worktree, "PRODTEST_PLAN")
    except Exception as e:
        eprint(f"warning: no PRODTEST_PLAN_RESULT found ({e})")
    revision = payload.get("revision", 1)
    try:
        revision = int(revision)
    except (TypeError, ValueError):
        revision = 1
    risk = str(payload.get("risk", "") or "")
    if isinstance(payload.get("risk"), dict):
        risk = str(payload["risk"].get("level", "") or "")

    manifest = Path(worktree) / ".workagent" / "prodtest.yaml"
    validation: dict = {"valid": True, "errors": [], "warnings": []}
    if plan_file.exists():
        try:
            validation = _ps.run_validate(str(plan_file), str(manifest) if manifest.exists() else None)
        except Exception as e:
            validation = {"valid": False, "errors": [str(e)], "warnings": []}
            eprint(f"warning: plan validation failed: {e}")
        if not validation.get("valid"):
            for err in validation.get("errors", []):
                eprint(f"warning: plan invalid: {err}")

    try:
        rels = []
        if plan_file.exists():
            rels.append(str(plan_file.relative_to(worktree)))
        else:
            eprint(f"warning: harness did not write {plan_file}")
        if manifest.exists():
            rels.append(str(manifest.relative_to(worktree)))
        if rels:
            run_cmd("git", "-C", worktree, "add", "--", *rels)
            run_cmd("git", "-C", worktree, "commit", "-m",
                    f"prodtest: plan r{revision} for {issue_number or key}")
            run_cmd("git", "-C", worktree, "push")
    except HarnessError as e:
        eprint(f"warning: plan commit/push failed: {e}")

    summary = str(payload.get("summary_markdown", "") or "").strip()
    if summary and issue_url:
        try:
            from . import prodtest_redact as _red
            _ic.post_issue_comment(issue_url, _red.redact(summary))
        except Exception as e:
            eprint(f"warning: summary comment post failed: {e}")

    proposed = payload.get("proposed_subjects", []) or []
    if proposed:
        eprint(f"warning: plan proposes subjects needing admin setup: {proposed}")
    try:
        text = plan_file.read_text(encoding="utf-8") if plan_file.exists() else ""
        if "[HUMAN]" in text:
            eprint("warning: plan contains [HUMAN] placeholders needing manual fill-in")
    except OSError:
        pass

    # P0: persist the plan diff hash from the dirty-tree tip so byte-identical
    # plans pass the cleanup gate. Preserve the release-recorded status only
    # while it means "released, awaiting run" (pending_prodtest / running):
    # a re-plan after a completed run (passed/failed/blocked) resets to
    # pending so the new revision/hash goes through release + run again.
    diff_hash = ""
    try:
        from . import cli_cleanup as _cc
        try:
            default_branch = repos.default_branch(Path(repo)) if repo else "main"
        except Exception:
            default_branch = "main"
        diff_hash = _cc.current_prodtest_diff(worktree, default_branch or "main")
    except Exception as e:
        eprint(f"warning: plan diff hash failed: {e}")
    status = "pending"
    try:
        existing = store.get_prodtest(issue_number or key)
        if existing and (existing.get("release_sha") or ""):
            prev = str(existing.get("status", "") or "")
            if prev in ("pending_prodtest", "running"):
                status = prev
    except Exception:
        pass
    try:
        store.upsert_prodtest(issue_number or key, plan_path=str(plan_file),
                              plan_revision=revision, plan_risk=risk,
                              plan_diff_hash=diff_hash,
                              status=status)
    except Exception as e:
        eprint(f"warning: prodtest state upsert failed: {e}")

_FIX_MODES = ("report-only", "issue", "issue+mr")


@prodtest_app.command("run")
@_catch_harness_errors
def prodtest_run(
    ref: str = typer.Argument(..., help="Issue ref or worktree ref of the released work (key/branch/path)."),
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
) -> None:
    """Execute the production test plan for a released issue."""
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
    plan_file = Path(plan_path) if plan_path else None
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
                       "release_sha": release_sha, "plan_file": plan_path,
                       "fix_mode": fix_mode, "skip_version_gate": skip_version_gate,
                       "dry_run": True}, json_output)
        return

    from . import prodtest_lock as _lock
    from . import prodtest_scripts as _ps
    from .release_util import worktree_base

    lock_base = str(store.config_dir())
    if _lock.is_locked(lock_base, store_key):
        _fail(f"prodtest run already in progress for {store_key}", 1)

    timeout_s = _run_timeout_s(plan_file, skip_version_gate)

    try:
        with _lock.held_issue_lock(lock_base, store_key):
            _run_locked(store_key, entry, plan_file, release_sha, issue_url,
                        fix_mode, skip_version_gate, out_dir, timeout_s,
                        launch, terminal, no_tty, json_output,
                        harness, env_file, extra_prompt, session_file)
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


def _run_locked(store_key: str, entry: dict, plan_file: Path | None, release_sha: str,
                issue_url: str, fix_mode: str, skip_version_gate: bool,
                out_dir: Path, timeout_s: int,
                launch: bool, terminal: bool, no_tty: bool, json_output: bool,
                harness: Optional[str], env_file: str | None,
                extra_prompt: Optional[str], session_file: str | None) -> None:
    """Run body inside the per-issue lock: checkout, validate, harness, persist."""
    from . import release_worktree as _wt
    from . import prodtest_scripts as _ps
    from .release_util import worktree_base

    repo = str(entry.get("repo", "") or "")
    checkout: Path | None = None
    if repo and Path(repo).is_dir():
        dest = Path(str(worktree_base())) / f"prodtest-{store_key.replace('/', '-').replace(':', '-')}"
        try:
            checkout = _wt.prepare(repo, dest, release_sha)
        except HarnessError as e:
            _fail(f"checkout at {release_sha} failed: {e}", 1)
    else:
        eprint(f"warning: no repo checkout for {store_key}; skipping ephemeral checkout")

    _enforce_plan_release_binding(store_key, item=None, plan_file=plan_file, release_sha=release_sha, repo=repo, checkout=checkout)
    try:
        _run_validated(store_key, entry, plan_file, release_sha, issue_url,
                        fix_mode, skip_version_gate, out_dir, timeout_s,
                        launch, terminal, no_tty, json_output,
                        harness, env_file, extra_prompt, session_file,
                        repo, checkout)
    finally:
        if checkout is not None:
            try:
                _wt.finish(checkout, True)
            except Exception as e:
                eprint(f"warning: run checkout cleanup failed: {e}")




def _rel_plan_candidates(plan_file: Path) -> list[str]:
    """Repo-relative candidate path for a plan file.

    Production layout stores the plan worktree-local at
    ``.workagent/prodtest/plans/<KEY>.yaml`` (committed in the MR branch but
    not necessarily under the release checkout root). Derive the canonical
    repo-relative path from the filename so ``git show
    <release_sha>:<path>`` can resolve the plan bytes at release time.
    """
    name = Path(str(plan_file)).name
    if not name:
        return []
    return [f".workagent/prodtest/plans/{name}"]


def _plan_manifest_allows_warn(checkout: Path | None, repo: str) -> bool:
    """True when the manifest fix_policy.plan_binding selects warn mode."""
    try:
        import yaml as _yaml
        base = checkout if checkout is not None and checkout.is_dir() else (Path(repo) if repo else None)
        man = (base / ".workagent" / "prodtest.yaml") if base is not None else None
        if man is not None and man.exists():
            policy = (_yaml.safe_load(man.read_text(encoding="utf-8")) or {}).get("fix_policy") or {}
            if isinstance(policy, dict) and str(policy.get("plan_binding", "") or "").lower() == "warn":
                return True
    except Exception:
        pass
    return False


def _plan_release_binding(store_key: str, plan_file: Path | None, release_sha: str,
                          repo: str, checkout: Path | None) -> str | None:
    """Plan-at-release-SHA binding: None = pass, 'blocked:…' / 'warn:…' otherwise.

    Compares the plan bytes at the release SHA (via the ephemeral checkout,
    falling back to ``repo``) against the working-tree plan: a mismatch means
    the stored plan is not what was reviewed at release time. Worktree-local
    plans (the production layout) are resolved to their canonical
    repo-relative ``.workagent/prodtest/plans/<KEY>.yaml`` path and read via
    ``git show <release_sha>:<path>``. Never raises.
    """
    if plan_file is None or not str(plan_file):
        return "warn:missing-plan"
    try:
        current = Path(str(plan_file)).read_bytes()
    except OSError:
        return "warn:missing-plan"
    base: Path | None = checkout if checkout is not None and checkout.is_dir() else None
    if base is None and repo and Path(repo).is_dir():
        base = Path(repo)
    if base is None:
        return None  # no checkout and no repo: per-skill preflight cannot run.
    rel: Path | None = None
    try:
        rel = Path(str(plan_file)).resolve().relative_to(Path(str(base)).resolve())
    except Exception:
        rel = None
    rels: list[str] = [rel.as_posix()] if rel is not None else []
    if not rels:
        # Worktree-local plan (production layout): resolve via the canonical
        # repo-relative plan path so the gate actually fires on mismatch.
        rels = _rel_plan_candidates(Path(str(plan_file)))
    if not rels:
        return "warn:plan-unverifiable-at-release"
    at_release: str | None = None
    try:
        from .errors import run_cmd as _run
        for cand in rels:
            try:
                at_release = _run("git", "-C", str(base), "show", f"{release_sha}:{cand}",
                                  echo=False)
                break
            except Exception:
                continue
    except Exception:
        return "warn:plan-unverifiable-at-release"
    if at_release is None:
        # Cannot prove the plan existed at the release SHA: block by default
        # so the gate fires; warn only when the manifest opts into warn mode.
        if _plan_manifest_allows_warn(checkout, repo):
            return "warn:plan-unverifiable-at-release"
        return "blocked:plan-unverifiable-at-release"
    if (at_release or "").encode() != current:
        return "blocked:plan-changed-since-release"
    return None


def _enforce_plan_release_binding(store_key: str, item: dict | None, plan_file: Path | None,
                                  release_sha: str, repo: str, checkout: Path | None) -> None:
    """Fail or warn loudly on plan/release-SHA mismatch (manifest policy).

    ``manifest.fix_policy.plan_binding`` selects the mode: ``"fail"``
    (default) aborts the run, ``"warn"`` prints a loud warning and proceeds.
    Missing plans only warn — validation below owns that path.
    """
    binding = _plan_release_binding(store_key, plan_file, release_sha, repo, checkout)
    if binding is None:
        return
    mode = "fail"
    try:
        import yaml as _yaml
        base = checkout if checkout is not None and checkout.is_dir() else (Path(repo) if repo else None)
        man = (base / ".workagent" / "prodtest.yaml") if base is not None else None
        if man is not None and man.exists():
            policy = (_yaml.safe_load(man.read_text(encoding="utf-8")) or {}).get("fix_policy") or {}
            if isinstance(policy, dict) and str(policy.get("plan_binding", "") or "").lower() == "warn":
                mode = "warn"
    except Exception:
        mode = "fail"
    msg = (f"plan/release binding {binding} for {store_key} at {release_sha}: "
           "the working-tree plan differs from the plan at the release SHA "
           "(re-run `prodtest plan`, re-review, and release again)")
    if binding.startswith("blocked:") and mode == "fail":
        _fail(msg, 1)
    eprint(f"WARNING: {msg}")


def _run_validated(store_key: str, entry: dict, plan_file: Path | None, release_sha: str,
                   issue_url: str, fix_mode: str, skip_version_gate: bool,
                   out_dir: Path, timeout_s: int,
                   launch: bool, terminal: bool, no_tty: bool, json_output: bool,
                   harness: Optional[str], env_file: str | None,
                   extra_prompt: Optional[str], session_file: str | None,
                   repo: str, checkout: Path | None) -> None:
    """Validated run body: plan validation, harness dispatch, persistence."""
    from . import release_worktree as _wt
    from . import prodtest_scripts as _ps
    try:
        manifest = (Path(repo) / ".workagent" / "prodtest.yaml") if repo else None
        if plan_file is not None and plan_file.exists():
            try:
                validation = _ps.run_validate(
                    str(plan_file), str(manifest) if manifest and manifest.exists() else None)
            except Exception as e:
                _fail(f"plan validation failed: {e}", 1)
            if not validation.get("valid"):
                for err in validation.get("errors", []):
                    eprint(f"plan invalid: {err}")
                _fail(f"plan {plan_file} invalid; fix the plan first", 1)
        else:
            eprint(f"warning: plan file {plan_file} missing; running without validation")

        run_request = {
            "checkout": str(checkout) if checkout else (repo or ""),
            "release_sha": release_sha,
            "plan_file": str(plan_file) if plan_file else "",
            "issue": {"key": store_key, "url": issue_url or None},
            "run_dir": str(out_dir),
            "fix_mode": fix_mode,
            "notifications_handled_by_harness": False,
            "skip_version_gate": skip_version_gate,
        }
        prompt = ("Use the prodtest-run skill with run request: "
                  + json.dumps(run_request, indent=2))
        prompt = append_extra_prompt(prompt, extra_prompt)
        harness_name = harness or store.load_config().get("default_harness", "omp")
        worktree = str(entry.get("worktree", "") or repo or ".")
        branch = str(entry.get("branch", "") or "")
        result: dict = {"worktree_path": worktree, "branch": branch,
                        "command": "prodtest run", "key": store_key,
                        "run_dir": str(out_dir), "release_sha": release_sha,
                        "fix_mode": fix_mode}
        eprint(f"run dir: {out_dir}  release: {release_sha}")
        from .errors import HarnessTimeout as _HarnessTimeout
        try:
            _run_harness_guarded(harness_name, prompt, worktree, repo or worktree,
                                 no_tty, launch, result, json_output, terminal,
                                 env_file, session_file, timeout_s,
                                 store_key if entry else None)
        except _HarnessTimeout as e:
            result["timeout"] = True
            if launch and no_tty:
                _finish_run(store_key, entry, plan_file, release_sha, issue_url,
                            fix_mode, out_dir, timeout_s, result, timed_out=True)
            _fail(f"prodtest run timed out after {timeout_s}s (budget + gate + slack); "
                  f"marked BLOCKED", 1)
        if launch and no_tty:
            _finish_run(store_key, entry, plan_file, release_sha, issue_url,
                        fix_mode, out_dir, timeout_s, result)
    finally:
        if checkout is not None:
            try:
                _wt.finish(checkout, True)
            except Exception as e:
                eprint(f"warning: run checkout cleanup failed: {e}")


def _run_harness_guarded(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                         no_tty: bool, launch: bool, result: dict, json_output: bool,
                         terminal: bool, env_file: str | None, session_file: str | None,
                         timeout_s: int, run_key: str | None) -> None:
    """Headless harness call with the run timeout; overrun kills the group.

    The harness child runs in its own process group (``start_new_session``,
    mirroring ``release_gates._run_group``); on overrun the group gets
    SIGTERM then SIGKILL synchronously, so no orphan survives. The lock and
    the ephemeral checkout stay held until the kill completes — the caller
    persists BLOCKED only after this raises.
    """
    if not (launch and no_tty):
        _run_harness(harness_name, prompt, worktree, fallback_dir,
                     no_tty, launch, result, json_output,
                     run_key=run_key, session_file=session_file,
                     terminal=terminal, env_file=env_file)
        return
    _run_harness(harness_name, prompt, worktree, fallback_dir,
                 no_tty, launch, result, json_output,
                 run_key=run_key, session_file=session_file,
                 terminal=terminal, env_file=env_file,
                 timeout_s=timeout_s)


def _finish_run(store_key: str, entry: dict, plan_file: Path | None, release_sha: str,
                issue_url: str, fix_mode: str, out_dir: Path, timeout_s: int,
                result: dict, timed_out: bool = False) -> None:
    """Best-effort post-harness: parse result, render/redact/persist, comment, status.

    On *timed_out* (harness group already killed) the run is BLOCKED with a
    redacted partial artifact — never left unpersisted.
    """
    from . import harness_summary as _hs
    from . import issue_comments as _ic
    from . import prodtest_result as _pr
    from . import prodtest_scripts as _ps

    session_file = str(result.get("session_file", "") or "")
    stdout_text = ""
    if session_file:
        try:
            stdout_text = _hs.last_assistant_text(session_file)
        except Exception as e:
            eprint(f"warning: summary parse failed: {e}")
    payload: dict = {}
    try:
        payload = _pr.parse_result_line(stdout_text, str(out_dir), "PRODTEST_RUN")
    except Exception as e:
        eprint(f"warning: no PRODTEST_RUN_RESULT found ({e})")
    overall = str(payload.get("overall", "") or "").upper()
    status = {"PASS": "passed", "FAIL": "failed", "BLOCKED": "blocked"}.get(overall, "blocked")
    if timed_out:
        status = "blocked"
        overall = overall or "BLOCKED"
        payload = dict(payload) if isinstance(payload, dict) else {}
        payload.setdefault("overall", "BLOCKED")
        payload.setdefault("notes", f"timed out after {timeout_s}s; harness killed")
    run_id = str(payload.get("run_id", "") or result.get("session_id", "") or "")

    report = ""
    results_file = str(out_dir / "results.yaml")
    report_file = str(out_dir / "report.md")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        report = _ps.render(results_file)
        Path(report_file).write_text(report, encoding="utf-8")
    except Exception as e:
        eprint(f"warning: report render failed: {e}")

    try:
        from . import prodtest_redact as _red
        redacted = _red.redact(report) if report else ""
        if redacted and issue_url:
            _ic.post_issue_comment(issue_url, redacted)
    except Exception as e:
        eprint(f"warning: report comment post failed: {e}")

    try:
        store.upsert_prodtest(store_key, status=status,
                              last_run_id=run_id or report_file)
    except Exception as e:
        eprint(f"warning: prodtest state upsert failed: {e}")

    fix = payload.get("fix") or {}
    _create_fix_followup(store_key, entry, issue_url, fix_mode, fix,
                         overall, out_dir, report_file, release_sha)


def _create_fix_followup(store_key: str, entry: dict, issue_url: str, fix_mode: str,
                         fix: dict, overall: str, out_dir: Path,
                         report_file: str, release_sha: str) -> None:
    """File a follow-up issue for FAIL/BLOCKED runs (best effort, never raises).

    Skill `prodtest-run` §8 assigns follow-up to the harness, but a harness
    that only reports `fix.decision` without an `issue_url` leaves the loop
    open. When ``fix_mode`` is ``issue``/``issue+mr`` and the verdict is not
    PASS, create the linked follow-up issue here (jira/gh/glab by the source
    issue URL) instead of merely logging. ``report-only`` keeps the old
    log-only behavior; existing ``fix.issue_url`` values are never duplicated.
    """
    if not isinstance(fix, dict) or fix.get("decision") in (None, "", "none"):
        return
    decision = str(fix.get("decision", "") or "")
    existing_url = str(fix.get("issue_url", "") or "")
    if fix_mode == "report-only":
        eprint(f"fix follow-up per manifest fix_policy: {decision} "
               f"(mode {fix_mode})")
        return
    if existing_url:
        eprint(f"fix follow-up per manifest fix_policy: {decision} "
               f"(mode {fix_mode}): {existing_url}")
        return
    verdict = str(overall or "").upper()
    if verdict == "PASS":
        eprint(f"fix follow-up per manifest fix_policy: {decision} "
               f"(mode {fix_mode})")
        return
    summary = (f"[prodtest] {store_key}: {verdict} at "
               f"{str(release_sha)[:12]} needs triage")
    body_lines = [summary, "",
                  f"Prodtest run for {store_key} finished {verdict} "
                  f"(release {release_sha}).",
                  f"Report: {report_file}",
                  f"Run dir: {out_dir}"]
    if decision:
        body_lines.append(f"Harness fix decision: {decision}")
    body_lines += ["", f"Original issue: {issue_url or store_key}"]
    body = "\n".join(body_lines)
    try:
        from . import refs as _refs
        from .errors import run_cmd as _run, HarnessError as _HE
        target = (issue_url or "").strip()
        try:
            parsed = _refs.parse_ref(target) if target else None
        except _HE:
            parsed = None
        tool = (parsed or {}).get("tool", "") if parsed else ""
        number = str((parsed or {}).get("number", "") or "") if parsed else ""
        if tool == "jira" and number:
            out = _run("jira", "issue", "create", "--no-input",
                       "-s", summary, "-b", body, echo=False)
            eprint(f"filed follow-up issue for {store_key} ({(out or '').strip()})")
        elif tool == "gh":
            repo = str((parsed or {}).get("repo", "") or "")
            args = ["gh", "issue", "create", "--title", summary, "--body", body]
            if repo:
                args += ["--repo", repo]
            out = _run(*args, echo=False)
            eprint(f"filed follow-up issue for {store_key} ({(out or '').strip()})")
        elif tool == "glab":
            repo = str((parsed or {}).get("repo", "") or "")
            # glab resolves the project from cwd; without a repo path or a
            # numeric project id the create call has nowhere to land.
            if repo and not repo[0].isdigit():
                eprint(f"warning: cannot file follow-up issue for {store_key}: "
                       "glab needs a project path/id context (harness owns follow-up here)")
            else:
                out = _run("glab", "issue", "create", "--title", summary,
                           "--description", body, echo=False)
                eprint(f"filed follow-up issue for {store_key} ({(out or '').strip()})")
        else:
            eprint(f"warning: cannot file follow-up issue for {store_key}: "
                   f"unparseable issue ref {target or store_key!r} "
                   f"(mode {fix_mode})")
    except Exception as e:
        eprint(f"warning: follow-up issue create failed for {store_key}: {e}")
