"""`cleanup` command + PR/issue-close helpers (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import backend, gitwt, refs, repos, store, trackers, worktrees
from . import sync as sync_mod
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _init_completions,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .cli_harness import _guard_harness
from .cli_repo_util import _repo_tool
from .errors import HarnessError

_COMP = _init_completions()
_complete_refs = _COMP["refs"]

# ── cleanup ───────────────────────────────────────────────────────────


@app.command("cleanup")
@_catch_harness_errors
def cleanup(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="Issue ID/URL, PR/MR URL, or worktree ref (key/branch/worktree); omit with --merged."),
    force: bool = typer.Option(False, "--force", help="Skip state validation."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    merged: bool = typer.Option(False, "--merged", help="Clean every linked worktree whose PR/MR is merged/closed (requires --yes; skips live-harness and invalid worktrees)."),
    no_squash: bool = typer.Option(False, "--no-squash", help="Merge open PRs with a merge commit instead of squash."),
) -> None:
    """Close issue + remove worktree/branch/PR.

    Example:
      workagent cleanup OWNER/REPO#22 --force --yes
      workagent cleanup OWNER/REPO#22 --dry-run
      workagent cleanup --merged --yes
    """
    if merged and ref:
        _fail("--merged takes no ref; it loops every linked worktree.", EXIT_USAGE)
    if not ref and not merged:
        _fail("ref required.\n"
              "  Pass a ref, or use --merged to clean every merged/closed link.",
              EXIT_USAGE)
    if merged and not yes and not dry_run:
        _fail("--merged requires --yes (non-interactive).\n"
              "  Re-run with --yes, or add --dry-run to preview.", EXIT_USAGE)

    links = store.load_links()

    if merged:
        # _status_cells(refresh_pr=True) is the merged/closed source of truth;
        # live harnesses and invalid worktrees are skipped, never torn down.
        rows = []
        for key, entry in links.items():
            branch = entry.get("branch", "")
            from . import cli as _cli
            _sc = _cli._status_cells
            state = ((_sc(dict(entry), refresh_pr=True).get("pr_data")
                     or {}).get("state", "") or "").upper()
            if state not in ("MERGED", "CLOSED"):
                rows.append({"key": key, "branch": branch,
                             "status": f"skipped:{state.lower() or 'no-pr'}"})
                continue
            if store.active_harness(key):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:live-harness"})
                continue
            wt = entry.get("worktree", "")
            if not wt or not worktrees.is_valid_worktree(wt):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:invalid"})
                continue
            rows.append(_cleanup_one(key, dict(entry), force, yes, dry_run,
                                     json_output, squash=not no_squash,
                                     merge=False))
        if json_output:
            print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
        else:
            _print_rows(rows, json_output=False, csv_output=False,
                        columns=["key", "branch", "status"],
                        title="Cleanup (merged/closed)",
                        empty="no linked worktrees")
        return

    resolved = worktrees.resolve_worktree(ref, links)
    if resolved is None:
        _fail(
            f"no linked state for {ref}.\n"
            "  Run `workagent link list` to see linked worktrees.",
            EXIT_USAGE,
        )
    key = worktrees.pick_worktree(ref, resolved, links)
    entry = links.get(key, {})
    if entry is None or not entry:
        _fail(f"no linked state for {ref}.", EXIT_USAGE)
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    if not branch:
        _fail(f"linked entry for {ref} has no branch", EXIT_USAGE)
    if key != ref and not yes and not dry_run and sys.stdin.isatty():
        try:
            answer = input(f"ref {ref!r} matches worktree {key!r} — use it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    if dry_run:
        _print_result({"dry_run": True, "key": key, "repo": str(repo), "branch": branch,
                       "pr_url": entry.get("pr_url", ""), "force": force,
                       "merge": entry.get("pr_url", "")}, json_output)
        return

    if not yes and not force and sys.stdin.isatty():
        try:
            answer = input(f"Remove worktree + delete branch '{branch}'? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    result = _cleanup_one(key, dict(entry), force, True, False, json_output,
                          squash=not no_squash)


def _pr_missing(msg: str) -> bool:
    """True when a host-CLI error means the PR/MR is gone (404/not found)."""
    msg = (msg or "").lower()
    return ("404" in msg or "not found" in msg or "could not resolve" in msg
            or "couldn't find" in msg or "could not find" in msg)


def _pr_conflicted(fresh: dict | None) -> bool:
    """True when fresh merge state reports destination-branch conflicts."""
    if not fresh:
        return False
    vals = " ".join(str(fresh.get(k) or "") for k in
                    ("mergeable", "merge_state")).lower()
    return any(t in vals for t in ("conflict", "dirty", "cannot_be_merged",
                                   "cannot be merged", "unmergable"))


def _fresh_pr_state(pr_url: str, host_cwd: str) -> dict | None:
    """Fresh PR/MR state (never cached); None on lookup failure (warned)."""
    if not pr_url:
        return None
    try:
        return refs.fetch_pr_merge_state(pr_url, cwd=host_cwd)
    except HarnessError as e:
        eprint(f"warning: cannot read PR/MR state for {pr_url}: {e}")
        return None


def _resolve_branch_pr(branch: str, recorded_url: str, host_cwd: str,
                       tool: str | None = None) -> tuple[str, dict | None, bool]:
    """(pr_url, fresh, picked) for a source branch.

    Lists every PR/MR sourced from *branch* and picks the latest open,
    else the latest overall. Falls back to the recorded URL (fresh lookup)
    when the list lookup fails or is empty. `picked` is True
    when the URL came from the branch list rather than the recorded link.
    """
    if branch:
        tools = ([tool] if tool in ("gh", "glab") else []) + ["gh", "glab"]
        seen: set[str] = set()
        for t in tools:
            if t in seen:
                continue
            seen.add(t)
            try:
                prs = refs.fetch_pr_list_for_branch(t, branch, cwd=host_cwd)
            except HarnessError as e:
                eprint(f"warning: {t} pr lookup failed for {branch}: {e}")
                continue
            picked = refs.pick_branch_pr(prs)
            if picked and picked.get("url"):
                url = picked["url"]
                from . import cli as _cli3
                return url, _cli3._fresh_pr_state(url, host_cwd), url != recorded_url
            if prs:
                break
    from . import cli as _cli4
    return recorded_url, (_cli4._fresh_pr_state(recorded_url, host_cwd) if recorded_url else None), False


def _merge_pr(pr_url: str, squash: bool, cwd: str | None = None) -> None:
    """Merge an open PR/MR (squash default); raise HarnessError on failure.

    Already-merged/closed is tolerated (returns with a note) since callers
    may decide on cached PR state up to the 3d status TTL: the host PR can
    have gone terminal without local tip movement. Other failures raise.

    ``cwd`` must be inside the MR/PR's repo so gh/glab bind the right
    host/remote — without it they probe the server's cwd (often an
    unrelated checkout) and fail with "no git remote points to a known
    host" or act on the wrong repo.
    """
    from . import cli as _cli  # shim: tests patch workagent.cli.run_cmd
    try:
        if "github.com" in pr_url:
            # gh requires an explicit strategy non-interactively; squash
            # default, --no-squash maps to a merge commit (--merge).
            _cli.run_cmd("gh", "pr", "merge", pr_url,
                    "--squash" if squash else "--merge", cwd=cwd)
        else:
            _cli.run_cmd("glab", "mr", "merge", pr_url,
                    *([] if squash else ["--no-squash"]), cwd=cwd)
    except HarnessError as e:
        msg = str(e).lower()
        if ("already merged" in msg or "already been merged" in msg
                or "already closed" in msg or "already been closed" in msg):
            eprint(f"note: {pr_url} already merged/closed; skipping merge.")
            return
        raise
    eprint(f"merged {pr_url}")


def _cleanup_one(key: str, entry: dict, force: bool, yes: bool,
                 dry_run: bool, json_output: bool, squash: bool = True,
                 merge: bool | None = None) -> dict:
    """Clean one linked worktree: merge open PR/MR, git-wt cleanup, close + drop link.

    Shared by `cleanup <ref>` (after confirmation) and the `cleanup --merged`
    loop (after the merged/live-harness/validity gates). The caller owns ref
    resolution and prompts; this only acts. Returns a summary row with
    "status": "cleaned" or "dry-run".
    """
    from . import cli as _cli  # shim: tests patch cli.*
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    worktree = entry.get("worktree", "")
    stored_ref = entry.get("issue", "") or entry.get("pr_url", "") or key
    # Host CLIs (gh/glab) bind host+remote from the process cwd. The
    # recorded repo can be stale (e.g. a link created while cwd was an
    # unrelated checkout), so prefer the live worktree — it resolves to
    # the real repo via --git-common-dir — and repair the stored repo.
    effective = worktrees.effective_repo_for_entry(dict(entry), default=repo) or repo
    if effective != repo:
        entry["repo"] = str(effective)
        if not dry_run:
            store.record_link(key, {"repo": str(effective)})
    repo = effective
    # cwd for host-CLI calls: the worktree when it still exists (its
    # origin remote is authoritative), else the effective repo root.
    host_cwd = worktree if worktree and Path(worktree).is_dir() else str(repo)
    try:
        parsed = refs.parse_ref(key if not key.startswith("pr:") else stored_ref)
    except HarnessError:
        try:
            parsed = refs.parse_ref(stored_ref)
        except HarnessError:
            parsed = {"kind": "", "tool": "", "repo": "", "number": "",
                      "url": pr_url or stored_ref}
    if dry_run:
        return {"key": key, "branch": branch, "repo": str(repo),
                "pr_url": pr_url, "force": force, "status": "dry-run",
                "merge": pr_url or ""}
    _sc2 = _cli._status_cells
    state = (_sc2(dict(entry), refresh_pr=False).get("pr_data")
             or {}).get("state", "")
    tool = _repo_tool(str(repo))
    recorded_url = pr_url
    pr_url, fresh, picked = _cli._resolve_branch_pr(branch, recorded_url, host_cwd,
                                               tool=tool)
    if picked:
        eprint(f"note: acting on {pr_url} (latest for branch {branch}).")
    fresh_state = ((fresh or {}).get("state") or "").lower()
    # A 404 on the fresh lookup means the cached row is stale but the host
    # PR is gone; fall back to cached state unless it is explicitly open.
    if fresh is None and state.lower() not in ("open", "opened"):
        fresh = {"state": state}
        fresh_state = (state or "").lower()
    conflicted = _pr_conflicted(fresh)
    merged_now = False
    remote_deleted = False
    if merge is None:
        # Merge only on explicitly open states; unknown ("") falls back
        # to close-and-remove so offline/cache-miss cleanup still works.
        merge = bool(pr_url) and state in ("open", "opened", "OPEN", "OPENED")
    state_norm = (state or "").lower()
    # Remote close BEFORE local teardown: host-CLI calls run with cwd
    # inside the live worktree (its origin remote is authoritative). After
    # git-wt removes the worktree the cwd no longer exists and glab falls
    # back to the server cwd — an unrelated checkout — failing with
    # "no git remote points to a known host" (IPG-953). Merge failures
    # raise BEFORE teardown — nothing is torn down on a failed merge.
    open_states = ("open", "opened")
    if (not force and pr_url and fresh_state in open_states and conflicted):
        # Rule 2: conflicted PR stays open; nothing is torn down.
        _fail(f"{pr_url} conflicts with its destination branch — remote branch kept.\n"
              "  Resolve the conflict on the host, then re-run cleanup.",
              EXIT_GENERAL)
    if merge:
        try:
            _cli._merge_pr(pr_url, squash=squash, cwd=host_cwd)
        except HarnessError as e:
            if force and not _pr_missing(str(e)):
                # Rule 3: --force merge failed (e.g. conflicts) → leave the
                # PR open, keep the remote branch, continue local cleanup.
                _cli._close_issue(parsed, force)
                eprint(f"note: {pr_url} could not be merged ({e}); left open.")
                merged_now = False
            else:
                # 404 without force, or conflict without force: PR stays
                # open, user notified via the raised error; nothing torn down.
                raise
        else:
            merged_now = True
    else:
        _cli._close_issue(parsed, force)
        if state_norm in ("merged", "closed"):
            # Already terminal on the host: closing is a no-op (glab fails
            # "already been merged"), so skip straight to local teardown.
            eprint(f"note: {pr_url} already {state_norm}; skipping remote close.")
        else:
            try:
                _cli._close_pr(parsed, pr_url, force, cwd=host_cwd)
            except HarnessError as e:
                if _pr_missing(str(e)):
                    # 404: host PR is gone; keep the remote branch and the
                    # link so nothing is lost silently.
                    _fail(f"{pr_url} not found on the host (404) — remote branch kept.\n"
                          "  Re-run with the branch deleted manually once confirmed.",
                          EXIT_GENERAL)
                raise
    # git-wt only removes the local branch (delete_branch=False below):
    # remote deletion is workagent's call. Rule 1: delete the remote branch
    # iff the PR/MR merged with this branch as its source (merged here, or
    # already-merged for this branch, verified via head_ref). Unmerged PRs —
    # closed, open, conflicted, or left open after a failed --force merge —
    # keep their remote branch.
    head_ref = ""
    if pr_url and (merged_now or fresh_state == "merged"):
        try:
            head_ref = (refs.fetch_pr_info(refs.parse_ref(pr_url),
                                           cwd=host_cwd).get("head_ref") or "")
        except HarnessError as e:
            eprint(f"warning: cannot verify PR head branch for {pr_url}: {e}")
            head_ref = branch if merged_now else ""
    delete_remote = bool(branch) and (merged_now or fresh_state == "merged") \
        and (not head_ref or head_ref == branch)
    cleanup = gitwt.cleanup_worktree(repo, branch, delete_branch=False,
                                     force=force, yes=yes)
    try:
        _cli.run_cmd("git", "-C", str(repo), "branch", "-D", branch)
        cleanup.setdefault("actions", []).append(f"deleted local branch {branch}")
    except HarnessError:
        pass
    if delete_remote:
        # Remote branch goes LAST: local cleanup already succeeded.
        try:
            _cli.run_cmd("git", "-C", str(repo), "push", "origin",
                    "--delete", branch)
            remote_deleted = True
        except HarnessError as e:
            eprint(f"warning: remote branch delete failed: {e}")
    remaining = {k: v for k, v in store.load_links().items() if k != key}
    store.save_links(remaining)
    if not json_output:
        tail = " (remote branch deleted)" if remote_deleted \
            else " (remote branch kept)"
        eprint(f"{key}: cleaned{tail}")
    return {"key": key, "branch": branch, "status": "cleaned", "cleanup": cleanup,
            "remote_deleted": remote_deleted}


def _close_issue(parsed: dict, force: bool) -> None:
    if parsed["tool"] == "jira-cli":
        # Best effort: comment the resolution; transitions vary per project,
        # so leave status change to the user unless --force.
        try:
            from .errors import run_cmd as _run
            _run("jira-cli", "issue", parsed["number"], "add-comment",
                 "--body", "Resolved via workagent cleanup.")
            eprint(f"commented on {parsed['number']} (status left unchanged)")
        except HarnessError as e:
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "gh" and parsed["repo"] and parsed["kind"] in ("issue", "issue_or_pr"):
        try:
            from .errors import run_cmd as _run
            target = [parsed["number"], "--repo", parsed["repo"]] if parsed["kind"] == "issue_or_pr" \
                else [parsed["url"]]
            _run("gh", "issue", "close", *target)
            eprint(f"closed issue {parsed['url'] or parsed['number']}")
        except HarnessError as e:
            msg = str(e).lower()
            # Note: `gh issue close` on an already-closed issue exits 0
            # (stdout "! ... already closed"), so this only fires for
            # genuinely missing issues (nonzero "Could not resolve").
            if ("already closed" in msg or "already been closed" in msg
                    or "not found" in msg or "404" in msg or "could not resolve" in msg):
                eprint(f"note: issue {parsed['url'] or parsed['number']} already closed; continuing.")
                return
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "glab":
        eprint("note: glab issue close not automated in v1; close it in the UI.")


def _close_pr(parsed: dict, pr_url: str, force: bool, cwd: str | None = None) -> None:
    url = pr_url or (parsed.get("url", "") if parsed.get("kind") in ("pr", "mr") else "")
    if not url:
        return
    try:
        from .errors import run_cmd as _run
        if "github.com" in url:
            _run("gh", "pr", "close", url, cwd=cwd)
        else:
            _run("glab", "mr", "close", url, cwd=cwd)
        eprint(f"closed {url}")
    except HarnessError as e:
        msg = str(e).lower()
        if ("already closed" in msg or "already been closed" in msg
                or "already been merged" in msg or "already merged" in msg
                or "404" in msg or "not found" in msg):
            eprint(f"note: {url} already closed; continuing with local cleanup.")
            return
        if not force:
            raise
        eprint(f"warning: {e}")


