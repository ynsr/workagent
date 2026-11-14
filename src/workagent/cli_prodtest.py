"""`prodtest` command group: plan + run."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from . import refs, repos, store, worktrees
from .cli_core import _catch_harness_errors, _fail, _print_result, app, eprint
from .cli_harness import _run_harness, append_extra_prompt
from .errors import HarnessError

prodtest_app = typer.Typer(help="Generate and run production test plans.", no_args_is_help=True)
app.add_typer(prodtest_app, name="prodtest")


def _plan_filename_for(issue_key: str) -> str:
    """Canonical plan filename for *issue_key* (``<KEY>.yaml``, sanitized)."""
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "-" for c in (issue_key or ""))
    return f"{safe or 'unknown'}.yaml"


def _resolve_plan_file(entry: dict, issue_key: str, stored: str) -> Path | None:
    """Plan file: stored path, else canonical plans/<KEY>.yaml on worktree/repo.

    Returns None when neither resolves to a real file. Best effort, never raises.
    """
    try:
        if stored and Path(stored).is_file():
            return Path(stored)
    except Exception:
        pass
    cands: list[Path] = []
    try:
        wt = str((entry or {}).get("worktree", "") or "")
        if wt:
            cands.append(Path(wt) / ".workagent" / "prodtest" / "plans" / _plan_filename_for(issue_key))
    except Exception:
        pass
    try:
        root = str((entry or {}).get("repo", "") or "")
        if root:
            cands.append(Path(root) / ".workagent" / "prodtest" / "plans" / _plan_filename_for(issue_key))
    except Exception:
        pass
    for cand in cands:
        try:
            if cand.is_file():
                return cand
        except Exception:
            continue
    return None


def _plan_search_dirs(entry: dict) -> list[Path]:
    """Writable plan directories: worktree first, then the repo checkout root."""
    dirs: list[Path] = []
    for base_key in ("worktree", "repo"):
        try:
            base = str((entry or {}).get(base_key, "") or "")
            if base:
                dirs.append(Path(base) / ".workagent" / "prodtest" / "plans")
        except Exception:
            continue
    return dirs


def _resolve_repo_dir(entry: dict) -> str:
    """Best repo checkout root for an entry (worktree-derived, else recorded)."""
    entry = entry or {}
    try:
        wt = str(entry.get("worktree", "") or "")
        if wt and Path(wt).expanduser().is_dir():
            try:
                resolved = worktrees.effective_repo_for_entry(dict(entry))
                if resolved is not None:
                    return str(resolved)
            except Exception:
                pass
            try:
                root = repos.main_repo_root(Path(wt).expanduser())
                if root is not None:
                    return str(root)
            except Exception:
                pass
    except Exception:
        pass
    try:
        return str(entry.get("repo", "") or "")
    except Exception:
        return ""


def _resolve_issue_context(ref: str) -> tuple[str, dict, dict | None, str | None]:
    """Resolve *ref* to (store_key, entry, parsed, link_key).

    Linked worktree first (``resolve_any``); on ``NoLinkedState`` fall
    back to issue-key-only mode: parse the ref as an issue and synthesize
    a minimal entry rooted at the repo checkout. Raises ``NoLinkedState``
    naming *ref* when neither path resolves.
    """
    links = store.load_links(include_deleted=True)
    try:
        key, entry, parsed = worktrees.resolve_any(ref, links)
        issue_number: str | None = None
        try:
            if isinstance(entry, dict) and entry.get("issue"):
                issue_number = str(entry.get("issue", "") or "") or None
        except Exception:
            issue_number = None
        if parsed is not None and parsed.get("kind") == "issue":
            issue_number = str(parsed.get("number", "") or "") or issue_number
        if not issue_number and isinstance(key, str) and key.startswith("jira:"):
            tail = key.split(":", 1)[1] or ""
            bare = tail.split("@", 1)[0]
            issue_number = bare or None
        store_key = issue_number or key
        return store_key, (dict(entry) if isinstance(entry, dict) else {}), parsed, key
    except worktrees.NoLinkedState:
        pass
    try:
        parsed = refs.parse_ref(ref)
    except HarnessError:
        raise worktrees.NoLinkedState(f"no linked worktree for {ref}")
    if not parsed or parsed.get("kind") != "issue":
        raise worktrees.NoLinkedState(f"no linked worktree for {ref}")
    store_key = str(parsed.get("number", "") or "")
    if not store_key:
        raise worktrees.NoLinkedState(f"no linked worktree for {ref}")
    link_key = ""
    try:
        if str(parsed.get("tool", "") or "") != "jira":
            link_key = refs.issue_key(parsed)
    except Exception:
        link_key = ""
    entry2: dict = {"issue": store_key, "worktree": "", "branch": "", "repo": "", "issue_url": ""}
    try:
        if link_key and isinstance(links, dict) and links.get(link_key):
            base_entry = links.get(link_key) or {}
            entry2 = dict(base_entry) if isinstance(base_entry, dict) else dict(entry2)
            entry2.setdefault("issue", store_key)
    except Exception:
        pass
    repo_dir = str(entry2.get("repo", "") or "")
    if not repo_dir:
        try:
            root = repos.repo_root()
            repo_dir = str(root) if root is not None else ""
        except Exception:
            repo_dir = ""
    if repo_dir:
        entry2["repo"] = repo_dir
    try:
        url = refs.issue_url(store_key) if hasattr(refs, "issue_url") else ""
        if url and not entry2.get("issue_url"):
            entry2["issue_url"] = url
    except Exception:
        pass
    if not str(entry2.get("repo", "") or ""):
        raise worktrees.NoLinkedState(
            f"no linked worktree for {ref} and no repo checkout is resolvable "
            f"(run from inside the repo checkout)")
    return store_key, entry2, parsed, None


def _prodtest_key_for_status(ref: str) -> str:
    """Store key for ``prodtest status <ref>`` (worktree or issue-key-only).

    Never raises: falls back to the raw ref when nothing resolves.
    """
    try:
        store_key, _entry, _parsed, _link = _resolve_issue_context(ref)
        return store_key
    except Exception:
        pass
    try:
        entry = {"key": ref}
        from . import cli_cleanup as _cc
        return _cc._prodtest_issue_key(ref, entry)
    except Exception:
        return ref


# Command bodies live in split modules (500-line cap): plan in
# cli_prodtest_plan, run in cli_prodtest_run, shared run pipeline in
# cli_prodtest_checks. Lazy re-export (no eager attribute binds: the
# submodules import this hub for `prodtest_app`/`_resolve_plan_file`).
_LAZY_EXPORTS = {
    "prodtest_plan": "cli_prodtest_plan",
    "prodtest_run": "cli_prodtest_run",
    "prodtest_list": "cli_prodtest_list",
    "prodtest_status": "cli_prodtest_list",
    "_finish_plan": "cli_prodtest_plan",
    "_plan_filename": "cli_prodtest_plan",
    "_FIX_MODES": "cli_prodtest_run",
    "_prodtest_eligibility": "cli_prodtest_run",
    "_run_all": "cli_prodtest_run",
    "_run_one": "cli_prodtest_run",
    "_run_timeout_s": "cli_prodtest_run",
    "_plan_stored_divergence": "cli_prodtest_checks",
    "_run_locked": "cli_prodtest_checks",
    "_rel_plan_candidates": "cli_prodtest_checks",
    "_plan_manifest_allows_warn": "cli_prodtest_checks",
    "_plan_release_binding": "cli_prodtest_checks",
    "_enforce_plan_release_binding": "cli_prodtest_checks",
    "_run_validated": "cli_prodtest_checks",
    "_run_harness_guarded": "cli_prodtest_checks",
    "_REPORT_POST_CAP": "cli_prodtest_checks",
    "_cap_report": "cli_prodtest_checks",
    "_validate_results_for_post": "cli_prodtest_checks",
    "_finish_run": "cli_prodtest_checks",
    "_create_fix_followup": "cli_prodtest_checks",
}


def __getattr__(name: str):
    """Lazy compat re-export for the split modules."""
    import importlib
    mod = _LAZY_EXPORTS.get(name)
    if mod is not None:
        return getattr(importlib.import_module(f"{__package__}.{mod}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from . import cli_prodtest_checks as _checks  # noqa: F401 (registers checks)
from . import cli_prodtest_plan as _plan  # noqa: F401 (registers plan)
from . import cli_prodtest_run as _run  # noqa: F401 (registers run)
from . import cli_prodtest_list as _list  # noqa: F401 (registers list/status)
