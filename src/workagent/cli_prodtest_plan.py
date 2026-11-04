"""`prodtest plan`: generate/update the production test plan."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from . import refs, repos, store, worktrees
from .cli_core import _catch_harness_errors, _fail, _print_result, eprint
from .cli_harness import _run_harness, append_extra_prompt
from .cli_prodtest import _resolve_plan_file, prodtest_app
from .errors import HarnessError

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
