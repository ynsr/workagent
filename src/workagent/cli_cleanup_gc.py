"""GC helpers for soft-deleted worktrees (moved verbatim from cli_cleanup)."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import gitwt, store
from .cli_core import eprint
from .errors import HarnessError


def _resolve_repo(worktree_path: str, repo_str: str) -> Path | None:
    """Best-effort main repo for teardown: the live worktree wins over the recorded repo."""
    from . import worktrees as _wt
    try:
        repo = _wt.effective_repo_for_entry(
            {"worktree": worktree_path or "", "repo": repo_str or ""}, default=None)
    except HarnessError:
        return None
    if repo is not None and Path(repo).expanduser().is_dir():
        return Path(repo).expanduser()
    return None


def _teardown_local(key: str, branch: str, repo_str: str, worktree_path: str = "") -> None:
    """Remove the worktree dir + git worktree entry + local branch (best effort, never raises)."""
    from . import cli as _cli
    repo = _resolve_repo(worktree_path or "", repo_str or "")
    branch = str(branch or "")
    if repo is not None and branch:
        try:
            gitwt.cleanup_worktree(repo, branch, delete_branch=False,
                                   force=True, yes=True)
        except HarnessError as e:
            eprint(f"warning: git-wt cleanup failed for {key}: {e}")
        try:
            _cli.run_cmd("git", "-C", str(repo), "branch", "-D", branch)
        except HarnessError:
            pass
    # Fallback: the recorded worktree dir may survive a stale-repo cleanup.
    wt = Path(str(worktree_path or "")).expanduser() if worktree_path else None
    if wt is not None and wt.exists():
        if repo is not None:
            try:
                _cli.run_cmd("git", "-C", str(repo), "worktree", "remove",
                             "--force", str(wt))
            except HarnessError:
                pass
            try:
                _cli.run_cmd("git", "-C", str(repo), "worktree", "prune")
            except HarnessError:
                pass
        if wt.exists():
            try:
                shutil.rmtree(wt)
            except OSError as e:
                eprint(f"warning: worktree dir removal failed for {key}: {e}")
    if repo is None and (branch or worktree_path):
        eprint(f"warning: partial purge for {key}: unknown repo — row dropped, leftovers may remain")

def gc_soft_deleted(retention_days: int = 7, dry_run: bool = False) -> list[dict]:
    """Purge worktree rows soft-deleted longer than *retention_days* ago.

    For each expired row: git-wt cleanup (worktree dir + git worktree entry
    + local branch via `branch -D`), plus a recorded-path fallback (direct
    `worktree remove --force` + rmtree) when the repo is stale, then drop
    the row (already gone from the store's view — gc_soft_deleted removes
    it; sessions/runs CASCADE).
    Remote branches are NOT touched: the merge-or-close at cleanup time
    already decided their fate. Returns per-row status dicts.
    Rows blocked by a pending/running prodtest item are never deleted
    (the store-level GC skips them) and are reported here as
    `skipped:prodtest-pending` (row and files survive).
    Dry-run reports without deleting rows or touching disk.
    """
    from . import cli_cleanup as _cc
    links = store.load_links(include_inactive=True, include_deleted=True)
    blocked = {k for k, e in links.items()
               if e.get("deleted_at") and _cc._prodtest_blocked(k, e)}
    rows = []
    if dry_run:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        for key in sorted(links):
            e = links[key]
            if not e.get("deleted_at") or not e["deleted_at"] < cutoff:
                continue
            if key in blocked:
                rows.append({"key": key,
                             "branch": str(e.get("branch", "")),
                             "status": "skipped:prodtest-pending"})
            else:
                rows.append({"key": key, "branch": str(e.get("branch", "")),
                             "status": "gc-dry-run"})
        return rows
    expired = store.gc_soft_deleted(retention_days=retention_days)
    expired_keys = {r["ref_key"] for r in expired}
    # Blocked-but-expired rows: the store GC skips them, so they never
    # appear in `expired` — re-emit here so live output matches dry-run.
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    for key in sorted(links):
        e = links[key] or {}
        if not e.get("deleted_at") or not e["deleted_at"] < cutoff:
            continue
        if key in blocked and key not in expired_keys:
            rows.append({"key": key, "branch": str(e.get("branch", "")),
                         "status": "skipped:prodtest-pending"})
    for r in expired:
        key = r["ref_key"]
        branch = str(r.get("branch", "") or "")
        repo = str(r.get("repo", "") or "")
        worktree_path = str(r.get("path", "") or "")
        if key in blocked:
            # Store-level GC already skips prodtest-blocked rows, but a
            # same-run plan change can slip one through — report, don't drop.
            rows.append({"key": key, "branch": branch,
                         "status": "skipped:prodtest-pending"})
            continue
        _teardown_local(key, branch, repo, worktree_path)
        rows.append({"key": key, "branch": branch, "status": "purged"})
    return rows


def destroy_deleted(keys: list[str], dry_run: bool = False) -> list[dict]:
    """Permanently destroy soft-deleted rows: worktree dir + branches + row.

    Tears down surviving local state: git-wt cleanup (worktree dir), local
    branch delete, then row drop. Sessions/runs CASCADE with the row.
    Remote branches are NOT touched. Returns per-key status dicts.
    Keys blocked by a pending/running prodtest item are refused with
    `skipped:prodtest-pending`. Never raises for unknown keys.
    """
    from . import cli_cleanup as _cc
    links = store.load_links(include_inactive=True, include_deleted=True)
    rows = []
    for key in keys:
        entry = links.get(key, {})
        if not entry or not entry.get("deleted_at"):
            rows.append({"key": key, "branch": str(entry.get("branch", "")),
                         "status": "skipped:not-deleted"})
            continue
        if _cc._prodtest_blocked(key, entry):
            rows.append({"key": key, "branch": str(entry.get("branch", "")),
                         "status": "skipped:prodtest-pending"})
            continue
        branch = str(entry.get("branch", "") or "")
        repo = str(entry.get("repo", "") or "")
        worktree_path = str(entry.get("worktree", "") or "")
        if dry_run:
            rows.append({"key": key, "branch": branch, "status": "destroy-dry-run"})
            continue
        _teardown_local(key, branch, repo, worktree_path)
        store.delete_worktree_row(key)
        rows.append({"key": key, "branch": branch, "status": "destroyed"})
    return rows
