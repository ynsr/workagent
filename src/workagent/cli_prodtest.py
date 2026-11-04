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


def _resolve_plan_file(entry: dict, issue_key: str, stored: str) -> Path | None:
    """Plan file for *issue_key*: stored path when it exists, else the

    canonical on-disk ``.workagent/prodtest/plans/<KEY>.yaml`` under the
    linked worktree. Returns None when neither resolves to a real file.
    Best effort, never raises.
    """
    try:
        if stored and Path(stored).is_file():
            return Path(stored)
    except Exception:
        pass
    try:
        wt = str((entry or {}).get("worktree", "") or "")
        safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "-" for c in (issue_key or ""))
        cand = Path(wt) / ".workagent" / "prodtest" / "plans" / f"{safe}.yaml"
        if safe and cand.is_file():
            return cand
    except Exception:
        pass
    return None
# Command bodies live in split modules (500-line cap): plan in
# cli_prodtest_plan, run in cli_prodtest_run, shared run pipeline in
# cli_prodtest_checks. Lazy re-export (no eager attribute binds: the
# submodules import this hub for `prodtest_app`/`_resolve_plan_file`).
_LAZY_EXPORTS = {
    "prodtest_plan": "cli_prodtest_plan",
    "prodtest_run": "cli_prodtest_run",
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
