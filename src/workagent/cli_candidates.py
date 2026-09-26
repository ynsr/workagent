"""candidates command + worktree scanner (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from . import refs, repos, store, trackers, worktrees
from .errors import HarnessError, run_cmd
from .cli_core import (
    _catch_harness_errors,
    _fail,
    _print_rows,
    app,
    eprint,
)

# ── candidates ────────────────────────────────────────────────────────


def _candidates(force: bool = False) -> dict:
    """Unlinked open PR/MRs + my recent issues; shared by the `candidates`
    command and the webapp /api/candidates endpoint.

    PRs are collected from every registered repo (per-repo failures →
    warning) minus any candidate already present in the worktrees table:
    a PR/MR is dropped when its URL, branch name, or worktree path matches
    a linked row. Issues are `trackers.list_my_issues` filtered to created
    within the last 7 days, minus issues whose key/URL already has a linked
    worktree. Scanned worktrees come from `_scan_worktrees`, which already
    excludes linked paths.
    """
    from . import cli as _cli  # shim: tests patch cli._repo_tool
    warnings: list[str] = []
    links = store.load_links()
    linked_urls = set()
    linked_issue_urls = set()
    linked_branches = set()
    linked_paths = set()
    for v in links.values():
        if not isinstance(v, dict):
            continue
        pr_url = str(v.get("pr_url", "") or "")
        if pr_url:
            linked_urls.add(pr_url.rstrip("/"))
        issue_url = str(v.get("issue_url", "") or "")
        if issue_url:
            linked_urls.add(issue_url.rstrip("/"))
            linked_issue_urls.add(issue_url.rstrip("/"))
        branch = str(v.get("branch", "") or "")
        if branch:
            linked_branches.add(branch)
        wt = str(v.get("worktree", "") or "")
        if wt:
            try:
                linked_paths.add(str(Path(wt).expanduser().resolve()))
            except OSError:
                linked_paths.add(wt)
    prs: list[dict] = []
    for name, entry in store.load_repos().items():
        path = str(entry.get("path", ""))
        if not path or not Path(path).exists():
            continue
        try:
            tool = _cli._repo_tool(path)
            if tool not in ("gh", "glab"):
                continue
            for p in refs.fetch_open_prs(tool, path):
                url = str(p.get("url", ""))
                if url.rstrip("/") in linked_urls:
                    continue
                branch = str(p.get("branch", "") or "")
                if branch and branch in linked_branches:
                    continue
                key = refs.pr_key(url)
                prs.append({**p, "url": url, "key": key,
                            "repo": key.split(":", 1)[-1].rsplit("#", 1)[0]})
        except HarnessError as e:
            warnings.append(f"{name}: {e}")
        except Exception as e:  # one bad repo must not kill the listing
            warnings.append(f"{name}: {e}")
    prs.sort(key=lambda p: p.get("updated", ""), reverse=True)
    issues = trackers.list_my_issues(warnings, force=force)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent: list[dict] = []
    for i in issues:
        dt = trackers.parse_created(str(i.get("created", "")))
        if dt is None or dt < cutoff:
            continue
        key = str(i.get("key", "") or "")
        if key and key in links:
            continue
        url = str(i.get("url", "") or "")
        if url and url.rstrip("/") in linked_issue_urls:
            continue
        # Repo hint: the Launch default for this ref — linked worktree's
        # repo, else the tracker's single linked repo, else "".
        repo_hint = trackers.default_repo_for_ref(key or url) if (key or url) else ""
        recent.append({**i, "repo_hint": repo_hint})
    recent.sort(key=lambda i: str(i.get("created", "")), reverse=True)
    return {"prs": prs, "issues": recent, "worktrees": _scan_worktrees(),
            "warnings": warnings}


def _scan_root() -> Path:
    """Scan root for unregistered worktrees; configurable, defaults to
    ~/dev/worktrees (issue #8 names ~/dev/worktress, which is a typo —
    that spelling does not exist on disk)."""
    raw = str(store.load_config().get("scan_root", "") or "")
    return Path(raw).expanduser() if raw else Path.home() / "dev" / "worktrees"


def _scan_worktrees() -> list[dict]:
    """Unregistered on-disk worktrees under <scan_root>/<repo>/: two shapes —
    `<branch>` and `<issue-type>/<branch>` (recombined with a `/`). Paths
    already linked in links.json are excluded; non-worktree dirs are
    skipped via worktrees.is_valid_worktree. Read-only."""
    links = store.load_links()
    known = set()
    for v in links.values():
        p = v.get("worktree", "")
        if p:
            try:
                known.add(str(Path(p).expanduser().resolve()))
            except OSError:
                continue
    root = _scan_root()
    found: list[dict] = []
    if not root.is_dir():
        return found
    try:
        repos = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for repo_dir in repos:
        try:
            children = sorted(p for p in repo_dir.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            # A leaf dir holding a worktree is pattern 1; a dir whose
            # children are worktrees is the pattern-2 issue-type prefix.
            try:
                grandchildren = sorted(
                    p for p in child.iterdir() if p.is_dir())
            except OSError:
                grandchildren = []
            if grandchildren and not worktrees.is_valid_worktree(str(child)):
                cands = grandchildren
            else:
                cands = [child]
            for wt in cands:
                branch = ""
                if worktrees.is_valid_worktree(str(wt)):
                    try:
                        from . import cli as _cli2
                        branch = (_cli2.run_cmd("git", "-C", str(wt), "branch",
                                         "--show-current") or "").strip()
                    except HarnessError:
                        continue
                if not branch:
                    continue
                try:
                    resolved = str(wt.resolve())
                except OSError:
                    continue
                if resolved in known:
                    continue
                m = re.match(r"(?:.*/)?([A-Z][A-Z0-9]+-\d+.*)", branch)
                key = f"jira:{m.group(1)}" if m else f"branch:{branch}"
                found.append({"path": str(wt), "repo": repo_dir.name,
                              "branch": branch, "key_guess": key})
    found.sort(key=lambda r: r["path"])
    return found


@app.command("candidates")
@_catch_harness_errors
def candidates_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output the PR/MR table as CSV (stdout; logs go to stderr)."),
    reset_cache: bool = typer.Option(False, "--reset-cache", help="Clear the cached issue rows and re-fetch live."),
) -> None:
    """List unlinked open PR/MRs and my recent issues (last 7 days).

    Example:
      workagent candidates
      workagent candidates --json
    """
    if reset_cache:
        from . import store_sqlite as _sq
        _sq.clear_issue_cache(_sq.db_path())
    out = _candidates(force=reset_cache)
    for w in out["warnings"]:
        eprint(f"warning: {w}")
    if json_output:
        print(json.dumps({"prs": out["prs"], "issues": out["issues"],
                          "worktrees": out["worktrees"]},
                         indent=2, ensure_ascii=False))
        return
    from rich.markup import escape


    # Escape user content ONLY for the Rich table path; --csv/--json
    # carry raw titles (escaping would corrupt machine-readable cells).
    def _esc(rows: list[dict]) -> list[dict]:
        return [{**r, "title": escape(str(r.get("title", "")))} for r in rows]

    _print_rows(_esc(out["prs"]) if not csv_output else out["prs"],
                False, csv_output,
                ["url", "title", "repo", "updated"],
                "Unlinked PR/MRs", "(no unlinked open PR/MRs)")
    _print_rows(_esc(out["issues"]), False, False,
                ["key", "title", "status", "created", "repo_hint"],
                "Recent issues (reported by me, last 7 days)",
                "(no recent issues)")
    _print_rows(out["worktrees"], False, False,
                ["path", "repo", "branch", "key_guess"],
                "Unregistered worktrees (scan root)",
                "(no unregistered worktrees)")

