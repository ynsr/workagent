"""repo/tracker/link management commands (moved verbatim from cli.py)."""
from __future__ import annotations

import json
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
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .cli_core import _init_completions
from .errors import HarnessError

_COMP = _init_completions()
_complete_repos = _COMP["repos"]

# ── repo ──────────────────────────────────────────────────────────────

repo_app = typer.Typer(help="Manage registered offline repos.", no_args_is_help=True)
app.add_typer(repo_app, name="repo")


@repo_app.command("add")
@_catch_harness_errors
def repo_add(
    name: str = typer.Option(..., "--name", help="Name to register the repo under."),
    path: Path = typer.Option(..., "--path", help="Local path of the repo."),
    tracker: Optional[str] = typer.Option(None, "--tracker", help="Issue tracker project key/URL to map."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Register an offline repo.

    Example:
      workagent repo add --name projectx --path ~/projects/projectx
      workagent repo add --name projectx --path ~/projects/projectx --tracker IPG
    """
    p = path.expanduser()
    if not (p / ".git").exists() and not p.is_dir():
        _fail(f"not a repo path: {p}", EXIT_USAGE)
    # --tracker omitted: derive it from the repo's origin remote (GitHub
    # O/R, GitLab host/group/repo). Unknowable remote → no mapping, exit 2
    # (before register_repo: no half-registered repo on the failure path).
    tid = trackers.normalize_id(tracker) if tracker else trackers.default_tracker_for_repo(p)
    if not tid:
        _fail("cannot derive tracker from origin remote: pass --tracker IPG|github:O/R|GitLab URL", EXIT_USAGE)
    repos.register_repo(name, p, tracker_key=tid)
    _print_result({"registered": name, "path": str(p), "tracker": tid}, json_output)


def _repo_tracker_map(cfg: dict) -> dict:
    """Reverse-lookup {resolved repo path: tracker id} from the mapping.

    A repo listed under several trackers (e.g. ``jira:IPG`` recorded first,
    ``github:o/r`` derived later) shows the remote-derived host-scoped id:
    it names the repo's own remote, while a Jira prefix legitimately spans
    many repos/hosts. First-write still wins among equal-preference ids.
    """
    out: dict = {}
    for tid, entry in (cfg.get("trackers", {}) or {}).items():
        for r in (entry or {}).get("repos", []):
            try:
                key = str(Path(r).expanduser().resolve())
            except Exception:
                continue
            cur = out.get(key, "")
            if not cur or (cur.startswith("jira:") and tid.startswith(("github:", "gitlab:"))):
                out[key] = tid
    return out


@repo_app.command("list")
def repo_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List registered repos (Rich table by default).

    Example:
      workagent repo list
      workagent repo list --csv
      workagent repo list --json | jq '.[].name'
    """
    items = [{"name": n, "path": v.get("path", ""),
              "trackers": ",".join(v.get("trackers", []) or ([v["tracker"]] if v.get("tracker") else []))}
             for n, v in store.load_repos().items()]
    _print_rows(items, json_output, csv_output, ["name", "path", "trackers"],
                "Registered repos", "(no repos registered)")


@repo_app.command("remove")
@_catch_harness_errors
def repo_remove(
    name: str = typer.Argument(..., autocompletion=_complete_repos, help="Registered repo name."),
    force: bool = typer.Option(False, "--force", help="Remove even when linked worktrees exist (cascades them)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Unregister a repo.

    Example: workagent repo remove projectx
    """
    if store._sqlite_path() is not None:
        from . import store_sqlite as sq
        n = sq.worktree_count_for_repo(sq.db_path(), name)
        if n and not force:
            _fail(f"repo {name} still has {n} linked worktree(s) — clean them "
                  "up first (or pass --force to remove them with the repo)",
                  EXIT_USAGE)
    if not repos.unregister_repo(name):
        _fail(f"unknown repo: {name}", EXIT_USAGE)
    _print_result({"removed": name}, json_output)

# ── tracker ───────────────────────────────────────────────────────────

tracker_app = typer.Typer(help="Manage issue trackers (CRUD over the trackers table).",
                           no_args_is_help=True)
app.add_typer(tracker_app, name="tracker")

# ── link ──────────────────────────────────────────────────────────────

link_app = typer.Typer(help="View and manage tracker↔repo relations and worktree links.", no_args_is_help=True)
app.add_typer(link_app, name="link")


@tracker_app.command("add")
@_catch_harness_errors
def tracker_add(
    tracker: str = typer.Argument(..., help="Tracker id (e.g. jira:IPG, github:OWNER/REPO, gitlab:host/group/repo)."),
    vendor: str = typer.Option("", "--vendor", help="Tracker vendor: jira or github (default: derived from the id)."),
    remote_url: str = typer.Option("", "--remote-url", help="Tracker web URL (required; e.g. https://github.com/OWNER/REPO)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Create (or update) an issue tracker.

    --remote-url is required and must be a real URL — it is stored as-is
    (no derivation from the key). Example:

      workagent tracker add jira:IPG --remote-url https://tribe.jibit.cloud/browse/IPG
    """
    from . import store_sqlite as sq
    tid = trackers.normalize_id(tracker)
    try:
        vendor = sq.normalize_vendor(vendor)
    except ValueError as e:
        raise HarnessError(str(e), exit_code=2) from e
    remote_url = remote_url.strip()
    if not remote_url:
        raise HarnessError("--remote-url is required (a real tracker web URL, "
                           "e.g. https://tribe.jibit.cloud/browse/IPG)", exit_code=2)
    if store._sqlite_path() is None:
        from . import store_sqlite as _sqm
        v, _u = _sqm._tracker_meta(tid)
        cfg = store.load_config()
        entry = cfg.setdefault("trackers", {}).setdefault(tid, {"repos": []})
        # config.json has no vendor columns: keep explicit flags in the
        # entry so pre/post-migration runs of the same command agree.
        if vendor:
            entry["vendor"] = vendor
        else:
            entry.setdefault("vendor", v)
        entry["remote_url"] = remote_url
        store.save_config(cfg)
        _print_result({"tracker": tid, "repos": entry.get("repos", [])}, json_output)
        return
    row = sq.upsert_tracker(sq.db_path(), tid, vendor=vendor, remote_url=remote_url)
    _print_result(row, json_output)


@tracker_app.command("list")
def tracker_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List issue trackers with their linked repo counts.

    Example: workagent tracker list --json
    """
    from . import store_sqlite as sq
    if store._sqlite_path() is None:
        items = [{"key": t, "vendor": "", "remote_url": "",
                  "repos": len((v or {}).get("repos", []))}
                 for t, v in store.load_trackers().items()]
    else:
        items = sq.load_tracker_rows(sq.db_path())
    _print_rows(items, json_output, csv_output, ["key", "vendor", "remote_url", "repos"],
                "Issue trackers", "(no trackers)")


@tracker_app.command("remove")
@_catch_harness_errors
def tracker_remove(
    tracker: str = typer.Argument(..., help="Tracker id to delete (links cascade)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Delete an issue tracker (its repo links cascade).

    Example: workagent tracker remove jira:IPG
    """
    from . import store_sqlite as sq
    tid = trackers.normalize_id(tracker)
    if store._sqlite_path() is None:
        if not store.remove_tracker_repo(tid):
            _fail(f"unknown tracker: {tid}", EXIT_USAGE)
    elif not sq.delete_tracker(sq.db_path(), tid):
        _fail(f"unknown tracker: {tid}", EXIT_USAGE)
    _print_result({"removed": tid}, json_output)


@link_app.command("list")
def link_list(
    worktree: bool = typer.Option(False, "--worktree", help="Show the worktree column in worktree links."),
    refresh_pr: bool = typer.Option(False, "--refresh-pr", help="Fetch origin, then re-query PR status instead of using the cache."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    from . import cli as _cli  # shim: status fns stay in cli.py
    """List tracker↔repo relations and worktree links.

    Example:
      workagent link list
      workagent link list --json
    """
    trackers_map = store.load_trackers()
    links = store.load_links()
    _cli._fetch_origins(links, refresh_pr)
    if json_output:
        print(json.dumps({"trackers": trackers_map,
                          "worktrees": {k: _cli._enrich_entry(k, v, refresh_pr)
                                        for k, v in links.items()}},
                         indent=2, ensure_ascii=False))
        return
    rows = [{"tracker": t, "vendor": v.get("vendor", ""), "remote_url": v.get("remote_url", ""),
             "repos": ", ".join(v.get("repos", []))}
            for t, v in trackers_map.items()]
    _print_rows(rows, json_output, csv_output, ["tracker", "vendor", "remote_url", "repos"],
                "Tracker links", "(no tracker links)")
    srows, scolumns = _session_rows(links, worktree, refresh_pr)
    _print_rows(srows, json_output, csv_output, scolumns,
                "Worktree links", "(no worktree links)",
                colorize=_colorize_session)


@link_app.command("set")
@_catch_harness_errors
def link_set(
    tracker: str = typer.Argument(..., help="Tracker id (e.g. jira:IPG, github:OWNER/REPO) or bare Jira prefix / OWNER/REPO."),
    repo: str = typer.Argument(..., help="Repo path or registered name to link."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Link a tracker to a repo (persists the relation).

    Example:
      workagent link set jira:IPG ~/projects/projectx
      workagent link set github:OWNER/REPO my-checkout
    """
    tid = trackers.normalize_id(tracker)
    target = repos.resolve_repo(repo, Path.cwd())
    recorded = store.add_tracker_repo(tid, str(target.expanduser().resolve()))
    current = store.load_trackers().get(tid, {"repos": []})
    if not recorded:
        eprint(f"note: {target} already linked to {tid}")
    _print_result({"tracker": tid, "repos": current["repos"]}, json_output)


@link_app.command("remove")
@_catch_harness_errors
def link_remove(
    ref: str = typer.Argument(..., help="Tracker id or worktree key (issue/PR ref, branch, or worktree path)."),
    repo: Optional[str] = typer.Option(None, "--repo", help="Only remove this repo from the tracker mapping."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Remove a tracker mapping or a worktree link.

    Example:
      workagent link remove jira:IPG
      workagent link remove jira:IPG --repo ~/projects/other
      workagent link remove o/r#22
    """
    tid = trackers.normalize_id(ref)
    if tid in store.load_trackers():
        if repo:
            target = str(repos.resolve_repo(repo, Path.cwd()).expanduser().resolve())
            if not store.remove_tracker_repo(tid, target):
                _fail(f"repo {repo} not linked to tracker {tid}", EXIT_USAGE)
        else:
            store.remove_tracker_repo(tid)
        _print_result({"removed": tid}, json_output)
        return
    resolved = worktrees.resolve_worktree(ref, store.load_links())
    if resolved is None:
        _fail(f"no tracker mapping or worktree link for {ref}", EXIT_USAGE)
    links = store.load_links()
    del links[resolved]
    store.save_links(links)
    _print_result({"removed": resolved}, json_output)


from .cli_repo_util import (  # noqa: F401,E402
    _CI_STYLES,
    _CI_SYMBOLS,
    _CI_TTL_SECONDS,
    _DB_CACHE,
    _NEGATIVE_TTL_SECONDS,
    _PR_STYLES,
    _STATUS_TTL_SECONDS,
    _repo_default_branch,
    _repo_tool,
)



