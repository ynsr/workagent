"""GC helpers for soft-deleted worktrees (moved verbatim from cli_cleanup)."""
from __future__ import annotations

from pathlib import Path

from . import gitwt, store
from .cli_core import eprint
from .errors import HarnessError


def gc_soft_deleted(retention_days: int = 7, dry_run: bool = False) -> list[dict]:
    """Purge worktree rows soft-deleted longer than *retention_days* ago.

    For each expired row: git-wt cleanup (worktree dir + local branch),
    delete the local branch, then drop the row (already gone from the
    store's view — gc_soft_deleted removed it; sessions/runs CASCADE).
    Remote branches are NOT touched: the merge-or-close at cleanup time
    already decided their fate. Returns per-row status dicts.
    Rows blocked by a pending/running prodtest item are never deleted
    (the store-level GC skips them) and are reported here as
    `skipped:prodtest-pending` (row and files survive).
    """
    from . import cli as _cli
    from . import cli_cleanup as _cc
    links = store.load_links(include_inactive=True, include_deleted=True)
    blocked = {k for k, e in links.items()
               if e.get("deleted_at") and _cc._prodtest_blocked(k, e)}
    rows = []
    if dry_run:
        for key in sorted(blocked):
            rows.append({"key": key,
                         "branch": str(links[key].get("branch", "")),
                         "status": "skipped:prodtest-pending"})
    expired = store.gc_soft_deleted(retention_days=retention_days)
    for r in expired:
        key, branch, repo = r["ref_key"], r.get("branch", ""), r.get("repo", "")
        status = "purged"
        if key in blocked or _cc._prodtest_blocked(key):
            rows.append({"key": key, "branch": branch,
                         "status": "skipped:prodtest-pending"})
            continue
        if dry_run:
            rows.append({"key": key, "branch": branch, "status": "gc-dry-run"})
            continue
        if branch and repo:
            try:
                gitwt.cleanup_worktree(Path(repo), branch, delete_branch=False,
                                       force=True, yes=True)
            except HarnessError as e:
                eprint(f"warning: git-wt cleanup failed for {key}: {e}")
            try:
                _cli.run_cmd("git", "-C", str(repo), "branch", "-D", branch)
            except HarnessError:
                pass
        rows.append({"key": key, "branch": branch, "status": status})
    return rows


def destroy_deleted(keys: list[str], dry_run: bool = False) -> list[dict]:
    """Permanently destroy soft-deleted rows: worktree dir + branches + row.

    Tears down surviving local state: git-wt cleanup (worktree dir), local
    branch delete, then row drop. Sessions/runs CASCADE with the row.
    Remote branches are NOT touched. Returns per-key status dicts.
    Keys blocked by a pending/running prodtest item are refused with
    `skipped:prodtest-pending`. Never raises for unknown keys.
    """
    from . import cli as _cli
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
        if dry_run:
            rows.append({"key": key, "branch": branch, "status": "destroy-dry-run"})
            continue
        if branch and repo:
            try:
                gitwt.cleanup_worktree(Path(repo), branch, delete_branch=False,
                                       force=True, yes=True)
            except HarnessError as e:
                eprint(f"warning: git-wt cleanup failed for {key}: {e}")
            try:
                _cli.run_cmd("git", "-C", str(repo), "branch", "-D", branch)
            except HarnessError:
                pass
        store.delete_worktree_row(key)
        rows.append({"key": key, "branch": branch, "status": "destroyed"})
    return rows
