"""Repo/tool display helpers shared by status/cleanup/sync (from cli.py)."""
from __future__ import annotations

from pathlib import Path

from . import repos, store
from .errors import HarnessError

_PR_STYLES = {"open": "bright_green", "merged": "bright_magenta", "closed": "bright_red"}
_CI_TTL_SECONDS = 600
_CI_SYMBOLS = {"success": "✓", "failure": "✗", "running": "●"}
_CI_STYLES = {"success": "bright_green", "failure": "bright_red", "running": "cyan"}
_DB_CACHE: dict[str, str] = {}
_STATUS_TTL_SECONDS = 3 * 24 * 3600
_NEGATIVE_TTL_SECONDS = 30 * 60


def _repo_default_branch(repo: str) -> str | None:
    if repo not in _DB_CACHE:
        try:
            _DB_CACHE[repo] = repos.default_branch(Path(repo))
        except HarnessError:
            _DB_CACHE[repo] = ""
    return _DB_CACHE[repo] or None


def _repo_tool(repo: str) -> str | None:
    """Host CLI (gh/glab) for *repo*, persisted in the repo registry.

    A stored tool is reused while the registered remote URL still matches
    the repo's current origin; a changed/missing remote re-detects and
    updates the entry. Unregistered repos are detected fresh (no store).
    """
    registry = store.load_repos()
    hit = next(((n, e) for n, e in registry.items() if e.get("path") == repo), (None, None))
    entry = hit[1]
    remote = repos.remote_url(Path(repo))
    if (entry and entry.get("tool") in ("gh", "glab")
            and entry.get("remote") == remote):
        return entry["tool"]
    tool = (repos._detect_host_cli(Path(repo))
            if Path(repo).exists() else None)
    if entry is not None and (tool != entry.get("tool")
                              or remote != entry.get("remote")):
        from . import store_sqlite as sq
        if store._sqlite_path() is not None:
            row = sq.load_repos(sq.db_path()).get(hit[0], {})
            tids = row.get("trackers", []) or ([row["tracker"]] if row.get("tracker") else [])
            if tids:
                sq.register_repo_row(sq.db_path(), hit[0], repo,
                                     tids[0], remote=remote, tool=tool)
            else:
                # Repo survived a `tracker remove` cascade unlinked: refresh
                # tool/remote only (derivation may need network), never crash
                # on the mandatory-tracker guard.
                cur = row.get("remote", "")
                sq.register_repo_row_unlinked(sq.db_path(), hit[0], repo,
                                              remote=remote or cur, tool=tool)
        else:
            cfg = store.load_config()
            cfg["repos"][hit[0]]["tool"] = tool
            cfg["repos"][hit[0]]["remote"] = remote
            store.save_config(cfg)
    return tool
