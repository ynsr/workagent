"""`release` command: ship default -> release via FF-only auto-merge MR."""
from __future__ import annotations

import sys
from typing import Optional

import typer

from . import repos
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _print_result,
    app,
    eprint,
)
from .cli_repo_util import _repo_tool
from .errors import HarnessError, run_cmd


@app.command("release")
@_catch_harness_errors
def release(
    repo: str = typer.Argument(..., help="Registered repo name or path."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Open a default->release MR with FF-only auto-merge (one host rebase retry)."""
    result = run_release(repo, yes=yes, dry_run=dry_run, json_output=json_output)
    _print_result(result, json_output)


def _host_tool(path: str) -> str:
    """Host CLI for *path*: stored repo tool, else glab/gh probe, else glab."""
    try:
        tool = _repo_tool(path)
        if tool in ("gh", "glab"):
            return tool
    except Exception:
        pass
    return "glab"


def _existing_open_mr(tool: str, path: str, default_branch: str, release_branch: str) -> str:
    """URL of an existing open default->release MR, else ''."""
    from . import refs as _refs
    try:
        prs = _refs.fetch_open_prs(tool, cwd=path)
    except Exception:
        return ""
    for pr in prs:
        if str(pr.get("branch", "")) != default_branch:
            continue
        target = str(pr.get("target_branch", "") or pr.get("base", "") or "")
        if target and target != release_branch:
            continue
        url = str(pr.get("url", "") or "")
        if url:
            return url
    return ""


def _open_mr(tool: str, path: str, default_branch: str, release_branch: str, title: str) -> str:
    """Open the release MR; return its URL."""
    if tool == "gh":
        out = run_cmd("gh", "pr", "create", "--base", release_branch,
                      "--head", default_branch, "--title", title, cwd=path)
    else:
        out = run_cmd("glab", "mr", "create", "--source-branch", default_branch,
                      "--target-branch", release_branch, "--title", title, cwd=path)
    url = (out or "").strip().splitlines()[-1] if out else ""
    if not url:
        raise HarnessError("host CLI opened the MR but returned no URL", exit_code=1)
    return url


def _set_ff_auto_merge(tool: str, mr_url: str, cwd: str) -> None:
    """Enable FF-only auto-merge on *mr_url*; raise HarnessError on refusal."""
    if tool == "gh":
        run_cmd("gh", "pr", "merge", "--auto", "--ff-only", mr_url, cwd=cwd)
    else:
        run_cmd("glab", "mr", "merge", "--ff-only", "--auto-merge", mr_url, cwd=cwd)


def _rebase_retry(tool: str, mr_url: str, cwd: str) -> None:
    """One host-side rebase retry (glab only); raise when unsupported/failed."""
    if tool != "glab":
        raise HarnessError("gh has no host-side rebase; resolve conflicts on the host",
                           exit_code=1)
    run_cmd("glab", "mr", "rebase", mr_url, cwd=cwd)


def _merge_state(mr_url: str, path: str) -> dict:
    """Fresh merge state for *mr_url*; {} when unreadable."""
    from . import refs as _refs
    try:
        return _refs.fetch_pr_merge_state(mr_url, cwd=path) or {}
    except HarnessError as e:
        eprint(f"warning: cannot read MR state for {mr_url}: {e}")
        return {}


def run_release(repo: str, *, yes: bool, dry_run: bool, json_output: bool) -> dict:
    """Release flow returning a JSON-able result dict (raises via _fail)."""
    from . import store as _store
    registry = _store.load_repos()
    name = repo if repo in registry else ""
    if not name:
        from pathlib import Path as _P
        try:
            want = str(_P(repo).expanduser().resolve())
        except Exception:
            want = ""
        for n, info in registry.items():
            try:
                got = str(_P(str(info.get("path", ""))).expanduser().resolve())
            except Exception:
                continue
            if got and got == want:
                name = n
                break
    if not name:
        _fail(f"unknown repo: {repo}", EXIT_USAGE)
    assert name
    st = repos.release_status(name)
    if not st["release_branch"]:
        _fail(f"{name}: no release branch configured (repo update {name} --release-branch MASTER).",
              EXIT_USAGE)
    if st["default_branch"] == st["release_branch"]:
        _fail(f"{name}: default and release branch are the same.", EXIT_USAGE)
    if not st["releasable"]:
        return {"repo": name, "merged": False, "mr_url": "",
                "note": "already in sync, nothing to release"}
    plan = {"repo": name, "default_branch": st["default_branch"],
            "release_branch": st["release_branch"], "diff_count": st["diff_count"],
            "mr_title": f"Release {st['default_branch']} -> {st['release_branch']}"}
    if dry_run:
        eprint(f"dry-run: would open MR {plan['mr_title']} ({plan['diff_count']} commits)")
        return {**plan, "dry_run": True, "merged": False, "mr_url": ""}
    if not yes and sys.stdin.isatty():
        try:
            print(f"Open release MR {plan['mr_title']}? [y/N] ", file=sys.stderr, end="")
            answer = sys.stdin.readline().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    path = st["path"]
    tool = _host_tool(path)
    mr_url = _existing_open_mr(tool, path, st["default_branch"], st["release_branch"])
    if not mr_url:
        try:
            mr_url = _open_mr(tool, path, st["default_branch"], st["release_branch"],
                              plan["mr_title"])
        except HarnessError as e:
            _fail(f"release MR not opened: {e}", EXIT_GENERAL)
    try:
        _set_ff_auto_merge(tool, mr_url, cwd=path)
    except HarnessError as first:
        try:
            _rebase_retry(tool, mr_url, cwd=path)
        except HarnessError as re:
            _fail(f"release MR left open: {mr_url}: {re}", EXIT_GENERAL)
        try:
            _set_ff_auto_merge(tool, mr_url, cwd=path)
        except HarnessError as second:
            _fail(f"release MR left open: {mr_url}: {second} (after rebase: {first})",
                  EXIT_GENERAL)
    state = _merge_state(mr_url, path)
    merged = (state.get("state") or "").lower() == "merged"
    return {**plan, "merged": merged, "mr_url": mr_url}
