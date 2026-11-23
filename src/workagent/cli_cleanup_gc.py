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


def _harness_run_info(key: str) -> tuple[set[str], set[str]]:
    """Return (targets, scoped_session_dirs) recorded for *key*.

    Read this BEFORE the store purge — ``runs_for_worktree`` returns nothing
    once the runs rows go. *scoped_session_dirs* holds ``metadata.session_dir``
    paths that have a non-empty ``branch_path`` (dedicated to that branch,
    safe to ``shutil.rmtree`` even if unrecorded session files remain).
    """
    try:
        from . import store_sqlite as _sq
        rows = _sq.runs_for_worktree(_sq.db_path(), key,
                                     include_deleted_worktrees=True)
    except Exception:
        return set(), set()
    targets: set[str] = set()
    scoped_dirs: set[str] = set()
    for r in rows:
        fp = str(r.get("file_path", "") or "")
        if fp:
            targets.add(fp)
        meta = r.get("metadata") or {}
        sd = str(meta.get("session_dir", "") or "")
        if sd:
            targets.add(sd)
            if meta.get("branch_path"):
                scoped_dirs.add(sd)
    return targets, scoped_dirs


def _harness_run_targets(key: str) -> set[str]:
    """Transcript paths recorded for *key*: run file_path + metadata session_dir.

    Read this BEFORE the store purge — ``runs_for_worktree`` returns nothing
    once the runs rows go.
    """
    targets, _ = _harness_run_info(key)
    return targets


def _remove_transcripts(targets: set[str], key: str = "",
                        scoped_dirs: set[str] | None = None) -> None:
    """Delete the transcript files/dirs in *targets* + prune emptied parents.

    Files live under ~/.config/workagent/runs/<harness>/<branch-path>/ (or
    the legacy ``sessions/`` tree) — prune empty parents up to (not incl.)
    either transcript root. Targets outside those roots are skipped with a
    warning: ``file_path`` can name any file (``--session-file <existing
    file>`` is stored verbatim), and the orphan sweep runs unattended at
    serve startup. Best effort, never raises (issue #102).
    """
    if not targets:
        return
    scoped_resolved: set[Path] = set()
    for sd in (scoped_dirs or ()):
        sp = Path(sd).expanduser()
        try:
            scoped_resolved.add(sp.resolve())
        except OSError:
            scoped_resolved.add(sp)
    try:
        from . import store_sqlite as _sq
        config = store.config_dir()
        roots: list[Path] = []
        for name in (_sq.RUNS_DIRNAME, _sq.LEGACY_SESSIONS_DIRNAME):
            root = config / name
            try:
                roots.append(root.resolve())
            except OSError:
                roots.append(root)
        for raw in sorted(targets):
            try:
                p = Path(raw).expanduser()
                try:
                    resolved = p.resolve()
                except OSError:
                    resolved = p
                under = next((r for r in roots
                              if resolved == r or r in resolved.parents), None)
                if under is None:
                    eprint(f"warning: skipping transcript cleanup outside the"
                           f" transcript tree for {key}: {raw}")
                    continue
                if p.is_file():
                    p.unlink()
                    p = p.parent
                elif p.is_dir() and resolved in scoped_resolved and resolved != under:
                    shutil.rmtree(p)
                    p = p.parent
                if not p.is_dir():
                    continue
                cur = p.resolve() if p.exists() else p
                while cur != under:
                    try:
                        cur.rmdir()
                    except OSError:
                        break
                    cur = cur.parent
            except Exception as e:
                eprint(f"warning: harness transcript cleanup failed for {key}: {e}")
    except Exception as e:
        eprint(f"warning: harness transcript cleanup failed for {key}: {e}")


def _remove_harness_runs(key: str) -> None:
    """Delete harness transcript files + emptied branch dirs for one worktree."""
    targets, scoped_dirs = _harness_run_info(key)
    _remove_transcripts(targets, key, scoped_dirs=scoped_dirs)


def purge_orphans(dry_run: bool = False) -> dict:
    """Delete records whose worktree row no longer exists, plus transcripts.

    ``store.purge_orphans`` drops the DB rows (dangling runs, flows, message
    worktree links, child parent labels) and returns the orphan runs'
    transcript targets; the .jsonl files/dirs are deleted here. *dry_run*
    reports the same counts without deleting anything.
    """
    out = store.purge_orphans(dry_run=dry_run)
    if dry_run:
        return out
    _remove_transcripts(
        set(out.get("transcripts") or []),
        "orphans",
        scoped_dirs=set(out.get("scoped_dirs") or []),
    )
    return out


def _worktree_for_branch(repo: Path | None, branch: str) -> Path | None:
    """Return the worktree path where *branch* is currently checked out, or None."""
    if repo is None or not branch:
        return None
    from . import cli as _cli
    target = f"refs/heads/{branch}"
    try:
        out = _cli.run_cmd("git", "-C", str(repo), "worktree", "list",
                           "--porcelain", check=False) or ""
    except Exception:
        return None
    cur_wt: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur_wt = Path(line[len("worktree "):])
        elif line == f"branch {target}" and cur_wt is not None:
            return cur_wt
        elif not line.strip():
            cur_wt = None
    return None


def _is_main_repo_path(wt: Path, repo: Path | None) -> bool:
    """True when *wt* is a main repo folder (or ancestor/root) that must never be deleted."""
    try:
        wt_res = wt.resolve()
    except OSError:
        wt_res = wt
    for guard in (Path("/"), Path.home()):
        try:
            g_res = guard.resolve()
        except OSError:
            g_res = guard
        if wt_res == g_res or wt_res in g_res.parents:
            return True
    if (wt / ".git").is_dir():
        return True
    if repo is not None:
        try:
            repo_res = repo.resolve()
        except OSError:
            repo_res = repo
        if wt_res == repo_res or wt_res in repo_res.parents:
            return True
        from . import cli as _cli
        try:
            out = _cli.run_cmd("git", "-C", str(repo), "worktree", "list",
                               "--porcelain", check=False) or ""
            for line in out.splitlines():
                if line.startswith("worktree "):
                    main_wt = Path(line[len("worktree "):])
                    try:
                        main_res = main_wt.resolve()
                    except OSError:
                        main_res = main_wt
                    if wt_res == main_res or wt_res in main_res.parents:
                        return True
                    break
        except Exception:
            pass
    try:
        reg = store.load_repos() or {}
    except Exception:
        reg = {}
    for info in reg.values():
        rp = str((info or {}).get("path", "") or "")
        if not rp:
            continue
        rp_path = Path(rp).expanduser()
        try:
            rp_res = rp_path.resolve()
        except OSError:
            rp_res = rp_path
        if wt_res == rp_res or wt_res in rp_res.parents:
            return True
    return False


def _teardown_local(key: str, branch: str, repo_str: str, worktree_path: str = "") -> None:
    """Remove the worktree dir + git worktree entry + local branch (best effort, never raises)."""
    from . import cli as _cli
    repo = _resolve_repo(worktree_path or "", repo_str or "")
    branch = str(branch or "")
    wt = Path(str(worktree_path or "")).expanduser() if worktree_path else None
    guard_path = wt if wt is not None else _worktree_for_branch(repo, branch)
    if guard_path is not None and _is_main_repo_path(guard_path, repo):
        eprint(f"warning: refusing to delete main repo folder {guard_path} for {key}")
        return
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
            if _is_main_repo_path(wt, repo):
                eprint(f"warning: refusing to delete main repo folder {wt} for {key}")
                return
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
    the row (the store-level GC removes it and purges its runs rows) and
    delete the transcript files the runs named (targets are snapshotted
    before that purge — the rows are gone afterwards).
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
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    if dry_run:
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
    # Snapshot the transcript targets before the store GC purges the runs
    # rows (runs_for_worktree then returns nothing) — the .jsonl files are
    # ours to delete.
    run_info = {key: _harness_run_info(key) for key in links
                if key not in blocked and links[key].get("deleted_at")
                and links[key]["deleted_at"] < cutoff}
    expired = store.gc_soft_deleted(retention_days=retention_days)
    expired_keys = {r["ref_key"] for r in expired}
    # Blocked-but-expired rows: the store GC skips them, so they never
    # appear in `expired` — re-emit here so live output matches dry-run.
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
        t_set, s_set = run_info.get(key, (set(), set()))
        _remove_transcripts(t_set, key, scoped_dirs=s_set)
        rows.append({"key": key, "branch": branch, "status": "purged"})
    return rows


def destroy_deleted(keys: list[str], dry_run: bool = False) -> list[dict]:
    """Permanently destroy soft-deleted rows: worktree dir + branches + row.

    Tears down surviving local state: git-wt cleanup (worktree dir), local
    branch delete, then row drop. The worktree's runs rows are purged with
    the row and the harness transcripts are deleted from disk.
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
        _remove_harness_runs(key)
        store.delete_worktree_row(key)
        rows.append({"key": key, "branch": branch, "status": "destroyed"})
    return rows

