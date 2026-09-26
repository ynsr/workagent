"""`start` command + _start_from_pr helper (moved verbatim from cli.py).

Registered on the shared `cli_core.app`; cli.py imports this module
for its side effect. Autocompletion callbacks come from cli_core.
"""
from __future__ import annotations

import sys

from pathlib import Path
from typing import Optional

import typer

from . import backend, gitwt, refs, repos, store, trackers
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _init_completions,
    _print_result,
    app,
    eprint,
)
from .cli_harness import _guard_harness, _launch_in_worktree, _run_harness
from .cli_review import _ensure_branch_worktree
from .errors import HarnessError

_COMP = _init_completions()
_complete_repos = _COMP["repos"]
_complete_branches = _COMP["branches"]

from .cli_core import _DEFAULT_BRANCH_NAMES as _CORE_DEFAULTS

@app.command("start")
@_catch_harness_errors
def start(
    ref: str = typer.Argument(..., help="Issue key/URL, OWNER/REPO#NUM, or bare number."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    base: Optional[str] = typer.Option(None, "--base", autocompletion=_complete_branches, help="Base branch (default: repo default). A non-default branch runs on that branch instead of creating a new one."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively (auto commit/push/MR prompt suffix)."),
    no_runtime: bool = typer.Option(False, "-N", "--no-runtime", help="Skip launching the runtime: print the runtime command and land in an interactive shell inside the worktree."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path passed to the runtime (omp --resume)."),
) -> None:
    """Create worktree from issue and launch harness.

    Example:
      workagent start https://github.com/OWNER/REPO/issues/22
      workagent start OWNER/REPO#22 --repo my-checkout --no-tty
      workagent start OWNER/REPO#22 --dry-run
      workagent start OWNER/REPO#22 --base feat/22--add-login
      workagent start OWNER/REPO#22 --no-runtime
    """
    parsed = refs.parse_ref(ref)
    pr_mode = parsed["kind"] in ("pr", "mr")
    if pr_mode:
        return _start_from_pr(ref, parsed, repo, depth, harness, no_tty,
                              no_runtime, dry_run, yes, json_output,
                              session_file)
    key = refs.issue_key(parsed)
    links = store.load_links()
    existing = links.get(key, {})
    if existing.get("worktree") and Path(str(existing["worktree"])).is_dir():
        # Rule 1: the issue is already linked — reuse that worktree (never
        # create a second one, never touch the CWD). Its repo wins; an
        # explicit --repo that disagrees is a usage error, not a silent drop.
        if repo and str(existing.get("repo", "")) and str(repo) != str(existing.get("repo", "")):
            _fail(f"--repo {repo} disagrees with the linked worktree repo"
                  f" {existing.get('repo', '')} for {key}", EXIT_USAGE)
        eprint(f"note: reusing linked worktree {existing['worktree']} for {key}")
        if dry_run:
            _print_result({"dry_run": True, "repo": str(existing.get("repo", "")),
                           "worktree": str(existing.get("worktree", "")),
                           "branch": str(existing.get("branch", "")),
                           "key": key, "reused": True,
                           "tracker": trackers.tracker_id(parsed),
                           "tracker_link": "reused"}, json_output)
            return
        return _launch_in_worktree(key, existing, harness, no_tty, no_runtime,
                                   session_file, json_output)
    tid = trackers.tracker_id(parsed)
    r, outcome = trackers.resolve_for_tracker(
        tid, repo, Path.cwd(), depth=depth,
        yes=yes, persist=not dry_run)
    detected_default = repos.default_branch(r)
    # --base naming a non-default branch: run on that existing branch (worktree
    # checked out at it, upstream origin/<branch>); no new branch is created.
    branch_mode = base is not None and base != detected_default and base not in _CORE_DEFAULTS
    base_branch = base or detected_default
    cwd_root = repos.repo_root(Path.cwd())
    cur = repos.current_branch(r) if cwd_root == r else None
    # Running from inside a linked worktree on a non-protected branch
    # continues on that branch: the worktree exists, no new branch is created.
    cwd_branch = repos.worktree_branch(Path.cwd()) if cwd_root is not None else None
    cwd_mode = (cwd_branch is not None
                and cwd_branch not in _CORE_DEFAULTS
                and cwd_branch != detected_default)
    if cur and cur != base_branch and not yes and not dry_run and sys.stdin.isatty() and not cwd_mode:
        if branch_mode:
            eprint(f"note: repo is on '{cur}', worktree will check out existing branch '{base}'.")
        else:
            eprint(f"note: repo is on '{cur}', worktree will branch from '{base_branch}'.")
    elif cwd_mode and not yes and not dry_run and sys.stdin.isatty():
        eprint(f"note: continuing on existing branch '{cwd_branch}' (current worktree); no new branch created.")

    issue = refs.fetch_issue(parsed)
    key = refs.issue_key(parsed)
    harness_name = harness or store.load_config().get("default_harness", "omp")

    # git-wt detects the tracker from a URL (--link must be a URL, never a
    # shorthand like `OWNER/REPO#N`); build full issue URLs from parsed refs.
    link_url: str | None = None
    if parsed["tool"] == "jira-cli":
        if parsed["url"].startswith("http"):
            link_url = parsed["url"]
        else:
            site = refs.jira_site()
            if site:
                link_url = f"{site}/browse/{parsed['number']}"
    elif parsed["tool"] == "gh" and parsed["repo"]:
        link_url = refs.issue_url(key) or None
    elif parsed["tool"] == "glab" and parsed["repo"]:
        # parse_ref only yields full GitLab issue URLs today; keep the guard
        # explicit so a future shorthand still can't leak a non-URL --link.
        link_url = parsed["url"] if parsed["url"].startswith("http") else None
    elif parsed["repo"]:
        link_url = parsed["url"] if parsed["url"].startswith("http") else None
    issue_id, slug = gitwt.build_branch_for_issue(parsed, issue["title"])
    if parsed["tool"] == "jira-cli" and not issue_id:
        issue_id = parsed["number"]

    if dry_run:
        result = {"dry_run": True, "repo": str(r),
                  "base": detected_default if branch_mode else base_branch,
                  "issue": {"title": issue["title"]}, "harness": harness_name,
                  "key": key, "tracker": tid, "tracker_link": outcome,
                  "link": None if (branch_mode or cwd_mode) else link_url}
        if branch_mode:
            result["branch"] = base
        elif cwd_mode:
            result["branch"] = cwd_branch
        _print_result(result, json_output)
        return
    if branch_mode:
        wt = gitwt.start_worktree(r, branch=base, base=detected_default)
    elif cwd_mode:
        wt = gitwt.start_worktree(r, branch=cwd_branch, base=detected_default)
    else:
        wt = gitwt.start_worktree(r, issue=issue_id, slug=slug, link=link_url, base=base_branch)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", "")
    rec = {"issue": ref, "worktree": worktree, "branch": branch, "repo": str(r)}
    if link_url and link_url.startswith("http"):
        rec["issue_url"] = link_url
    store.record_link(key, rec)
    # Remember the repo under a derived name for future --repo picks.
    try:
        repos.register_repo(repos.repo_name(r), r)
    except HarnessError:
        pass
    prompt = backend.prompt_for_issue(issue["title"], issue["body"], ref,
                                      worktree=worktree, branch=branch)
    if no_tty:
        prompt += "\n\nAfter task done, commit, push and create an MR/PR to the default branch"
    result = {"worktree_path": worktree, "branch": branch,
              "base": detected_default if branch_mode else base_branch,
              "key": key, "harness": harness_name}
    eprint(f"worktree: {worktree}  branch: {branch}")
    if not no_runtime:
        _guard_harness(key, worktree)
    _run_harness(harness_name, prompt, worktree, str(r), no_tty, no_runtime,
                 result, json_output, run_key=key, session_file=session_file)


def _start_from_pr(ref: str, parsed: dict, repo: str | None, depth: int,
                   harness: str | None, no_tty: bool, no_runtime: bool,
                   dry_run: bool, yes: bool, json_output: bool,
                   session_file: str | None) -> None:
    """Start a coding session on a PR/MR source branch (branch-keyed row)."""
    tid = trackers.tracker_id(parsed)
    repo_dir, outcome = trackers.resolve_for_tracker(
        tid, repo, Path.cwd(), depth=depth, yes=yes, persist=not dry_run)
    base_branch = repos.default_branch(repo_dir)
    harness_name = harness or store.load_config().get("default_harness", "omp")
    pr_url = parsed["url"]
    try:
        info, head_ref = refs.require_head_ref(parsed, repo_dir, ref)
    except HarnessError as e:
        _fail(str(e), EXIT_USAGE)
    if dry_run:
        _print_result({"dry_run": True, "repo": str(repo_dir), "pr_url": pr_url,
                       "key": head_ref, "branch": head_ref,
                       "harness": harness_name,
                       "tracker": tid, "tracker_link": outcome}, json_output)
        return
    key, worktree, branch = _ensure_branch_worktree(
        repo_dir, head_ref, base_branch, pr_url)
    prompt = backend.prompt_for_issue(info.get("title", ""), info.get("body", ""),
                                      pr_url, worktree=worktree, branch=branch)
    if no_tty:
        prompt += "\n\nAfter task done, commit and push to the PR/MR source branch"
    result = {"worktree_path": worktree, "branch": branch,
              "base": base_branch, "key": key, "pr_url": pr_url,
              "harness": harness_name}
    eprint(f"worktree: {worktree}  branch: {branch}")
    if not no_runtime:
        _guard_harness(key, worktree)
    _run_harness(harness_name, prompt, worktree, str(repo_dir), no_tty,
                 no_runtime, result, json_output, run_key=key,
                 session_file=session_file)


