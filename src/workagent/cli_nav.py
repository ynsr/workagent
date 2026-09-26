"""cd/open/register commands (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

from . import backend, refs, repos, store, trackers, worktrees
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
from .errors import HarnessError, run_cmd

_COMP = _init_completions()
_complete_refs = _COMP["refs"]

# ── cd ────────────────────────────────────────────────────────────────


@app.command("cd")
@_catch_harness_errors
def cd_cmd(
    ref: str = typer.Argument(..., autocompletion=_complete_refs,
                              help="Issue/PR ref or worktree key."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Print the worktree root for a ref: cd "$(workagent cd <ref>)".

    A child process cannot change the shell's cwd, so the command prints
    the path; the `completions install` shell wrapper (installed since
    0.4.0) makes bare `workagent cd <ref>` change directory directly.
    """
    links = store.load_links()
    try:
        key, entry, _ = worktrees.resolve_any(ref, links)
    except worktrees.NoLinkedState as e:
        _fail(str(e), EXIT_USAGE)
    if not key:
        _fail(f"no linked state for {ref}", EXIT_USAGE)
    wt = entry.get("worktree", "")
    if not wt or not Path(wt).exists():
        _fail(f"worktree missing for {key}: {wt or '?'}", EXIT_GENERAL)
    if json_output:
        print(json.dumps({"key": key, "worktree": wt}))
        return
    print(wt)


# ── open ──────────────────────────────────────────────────────────────


@app.command("open")
@_catch_harness_errors
def open_cmd(
    ref: str = typer.Argument(..., autocompletion=_complete_refs,
                              help="Issue/PR ref or worktree key."),
) -> None:
    """Open the linked worktree directory with the OS file manager.

    Prints the worktree path on stdout. Launches no harness (nothing to
    guard — issue #6 covers harness launches only); refuses missing or
    invalid worktrees.

    Example:
      workagent open IPG-929
    """
    links = store.load_links()
    try:
        key, entry, _ = worktrees.resolve_any(ref, links)
    except worktrees.NoLinkedState:
        _fail(f"no linked state for {ref}.\n"
              "  Run `workagent link list` to see linked worktrees.", EXIT_USAGE)
    if not key:
        _fail(f"no linked state for {ref}.\n"
              "  Run `workagent link list` to see linked worktrees.", EXIT_USAGE)
    wt = entry.get("worktree", "")
    if not wt or not worktrees.is_valid_worktree(wt):
        _fail(f"no valid worktree for {key}: {wt or '?'}", EXIT_GENERAL)
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform,
                                                         "xdg-open")
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([opener, wt], **kwargs)
    except FileNotFoundError:
        _fail(f"no opener available ({opener})", EXIT_GENERAL)
    print(wt)


@app.command("register")
@_catch_harness_errors
def register(
    path: str = typer.Argument(..., help="Path to an existing worktree (not the main checkout)."),
    key: Optional[str] = typer.Option(None, "--key", help="Worktree key to register under (default: derived from the branch — 'jira:<KEY>' for issue branches, else 'branch:<branch>')."),
    issue: Optional[str] = typer.Option(None, "--issue", help="Issue/PR ref to attach (Jira key/URL, OWNER/REPO#NUM, PR/MR URL); sets the default key when --key is omitted."),
    repo_opt: Optional[str] = typer.Option(None, "--repo", help="Main checkout of the repository (default: derived from the worktree's git metadata)."),
    yes: bool = typer.Option(False, "--yes", "-y", "--force", help="Overwrite an existing link for the same key."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Register an existing (unregistered) worktree as a link.

    Example: workagent link-wt ~/dev/worktrees/projectx/feat/IPG-999--x

    The worktree must be a linked git worktree (not the main checkout).
    After registration the path works with status, sync, cd, and cleanup.
    """
    wt = Path(path).expanduser().resolve()
    if not wt.is_dir():
        _fail(f"not a directory: {wt}", EXIT_USAGE)
    # Main checkouts have commondir == gitdir; linked worktrees differ.
    try:
        gitdir = (run_cmd("git", "-C", str(wt), "rev-parse", "--absolute-git-dir")
                  or "").strip()
        commondir = (run_cmd("git", "-C", str(wt), "rev-parse",
                             "--path-format=absolute", "--git-common-dir")
                     or "").strip()
    except HarnessError as e:
        _fail(f"not a git repository: {wt} ({e})", EXIT_USAGE)
    if not gitdir or not commondir or \
            Path(commondir).resolve() == Path(gitdir).resolve():
        _fail(f"{wt} is a main checkout, not a linked worktree", EXIT_USAGE)
    branch = (run_cmd("git", "-C", str(wt), "branch", "--show-current")
              or "").strip()
    if not branch:
        _fail(f"cannot determine branch for {wt} (detached HEAD?)", EXIT_USAGE)
    main_repo = str(Path(commondir).parent.resolve())
    if repo_opt:
        r = Path(repo_opt).expanduser().resolve()
        if not r.is_dir():
            _fail(f"--repo is not a directory: {r}", EXIT_USAGE)
        main_repo = str(r)

    issue_ref = issue
    issue_url: str | None = None
    parsed = None
    if issue_ref:
        try:
            parsed = refs.parse_ref(issue_ref)
        except HarnessError as e:
            _fail(str(e), EXIT_USAGE)
        if parsed["tool"] == "gh" and parsed["repo"] and parsed["kind"] != "pr":
            key_for_url = refs.issue_key(parsed)
            issue_url = refs.issue_url(key_for_url) or None
        elif parsed["url"].startswith("http"):
            issue_url = parsed["url"]
        elif parsed["tool"] == "jira-cli" and (site := refs.jira_site()):
            issue_url = f"{site}/browse/{parsed['number']}"
        if key is None:
            key = refs.issue_key(parsed)
    if key is None:
        m = re.match(r"(?:feat/)?([A-Z][A-Z0-9]+)-(\d+)", branch)
        key = f"jira:{m.group(0).split('/')[-1]}" if m else f"branch:{branch}"

    if parsed is not None and main_repo:
        # The user named this worktree path explicitly — that IS the
        # confirmation for the tracker↔repo relation; persist it.
        trackers.check_or_record(trackers.tracker_id(parsed), main_repo,
                                 yes=True, persist=True)

    rec: dict = {"worktree": str(wt), "branch": branch, "repo": main_repo}
    if issue_ref:
        rec["issue"] = issue_ref
    if issue_url:
        rec["issue_url"] = issue_url
    cur = store.lookup_link(key)
    if cur is not None:
        cur_wt = str(Path(str(cur.get("worktree", ""))).resolve())
        if cur_wt == str(wt):
            rec = {**cur, **rec}
            eprint(f"{key}: already registered — nothing to do")
        elif not yes:
            _fail(f"{key} is already linked to {cur_wt} — re-run with "
                  "--force to overwrite", EXIT_GENERAL)
        else:
            eprint(f"{key}: overwriting previous link to {cur_wt}")
    store.record_link(key, rec)
    if cur is None:
        try:
            repos.register_repo(repos.repo_name(Path(main_repo)), main_repo)
        except HarnessError:
            pass
    result = {"key": key, "worktree": rec["worktree"], "branch": rec["branch"],
              "repo": rec["repo"], "issue": rec.get("issue", "")}
    if rec.get("issue_url"):
        result["issue_url"] = rec["issue_url"]
    if json_output:
        print(json.dumps(result))
    else:
        print(f"registered {key} → {rec['worktree']} (branch {rec['branch']})")


