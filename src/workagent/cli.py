"""workagent CLI — launch AI agent harnesses in git-wt worktrees from issue/PR links.

stdout carries ONLY command output; every log/progress/confirmation line
goes to stderr.
"""

from __future__ import annotations
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from . import __version__, backend, gitwt, refs, repos, store, trackers, worktrees
from . import completions as _completions
from . import doctor as _doctor
from . import sync as sync_mod
from .cli_core import (
    EXIT_GENERAL,
    EXIT_OK,
    EXIT_USAGE,
    _HARNESS_ARGS,
    _catch_harness_errors,
    _fail,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .errors import HarnessError, run_cmd

# --base values treated as "create a new branch off it": the repo's detected
# default or one of these common defaults. Any other --base names an existing
# branch to run on directly (no new branch).
_DEFAULT_BRANCH_NAMES = {"main", "master", "develop"}

_complete_repos = _completions.complete_names(repos.repo_names)
_complete_branches = _completions.complete_names(_completions._base_branch_candidates)
_complete_refs = _completions.complete_names(_completions._ref_candidates)


def _version_callback(value: bool) -> None:
    if value:
        print(f"workagent {__version__}")
        raise typer.Exit(0)


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose output (extra detail on stderr)."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress non-essential output on stderr."),
) -> None:
    """Global options."""






from .cli_harness import _guard_harness, _harness_cell, _launch_in_worktree, _run_harness
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



def _split_harness_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first bare `--`; everything after feeds the harness."""
    if "--" in argv and argv.index("--") > 0:
        cut = argv.index("--")
        return argv[:cut], [a for a in argv[cut + 1:] if a != "--"]
    return argv, []


import os as _os
import shlex as _shlex
import subprocess as _subprocess

# Re-exported for tests that patch stdlib via the cli namespace
# (monkeypatch.setattr(cli.os/subprocess/sys/shlex, ...)).
os = _os
shlex = _shlex
subprocess = _subprocess

from . import cli_start  # noqa: F401  (registers `start`)
from .cli_start import _start_from_pr, start  # noqa: F401

from . import cli_review  # noqa: F401  (registers `review`)
from .cli_review import (  # noqa: F401  (test compatibility: cli._is_reviewed etc.)
    _clear_reviewed,
    _ensure_branch_worktree,
    _has_unresolved_comments,
    _is_reviewed,
    _mark_reviewed,
    _review_key_for,
    _reviewable_keys,
    review,
)


from . import cli_cleanup  # noqa: F401  (registers `cleanup`)
from .cli_cleanup import (  # noqa: F401  (test compatibility)
    _cleanup_one,
    _close_issue,
    _close_pr,
    _fresh_pr_state,
    _merge_pr,
    _pr_conflicted,
    _pr_missing,
    _resolve_branch_pr,
    cleanup,
)

from . import cli_manage  # noqa: F401  (registers repo/tracker/link)
from .cli_manage import (  # noqa: F401
    link_list,
    link_remove,
    link_set,
    repo_add,
    repo_list,
    repo_remove,
    tracker_add,
    tracker_list,
    tracker_remove,
)

from . import cli_status  # noqa: F401  (registers `status`)
from .cli_status import (  # noqa: F401  (test/webapp compatibility)
    _fmt_pr,
    _recorded_pr,
    _seed_recorded_pr,
    _fmt_counts,
    _git_tip,
    _cache_fresh,
    _fetch_origins,
    _ci_fresh,
    _query_pr,
    _pr_cells,
    _status_cells,
    _ci_cell,
    _reviews_fresh,
    _reviews_cell,
    _fmt_reviews,
    _session_rows,
    _colorize_session,
    _enrich_entry,
    _session_detail,
    _print_detail,
    _create_hint,
    status,
)

from . import cli_candidates  # noqa: F401  (registers `candidates`)
from .cli_candidates import (  # noqa: F401  (test/webapp compatibility)
    _candidates,
    _scan_root,
    _scan_worktrees,
    candidates_cmd,
)


from . import cli_nav  # noqa: F401  (registers cd/open/register)
from .cli_nav import (  # noqa: F401
    cd_cmd,
    open_cmd,
    register,
)

from . import cli_sync  # noqa: F401  (registers `sync`)
from .cli_sync import (  # noqa: F401  (test compatibility)
    sync_cmd,
    _sync_create_pr_worktree,
    result_failed,
    _sync_one,
    _sync_local_merge,
)


@app.command("doctor")
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Check install sync + tool availability.

    Example: workagent doctor

    Exit codes: 0 in sync · 1 stale/missing receipt (fix: ./install.sh).
    """
    result = _doctor.check(json_output=json_output)
    if json_output:
        print(json.dumps(result, indent=2))
    if result["status"] != "ok":
        raise typer.Exit(EXIT_GENERAL)


@app.command("migrate")
@_catch_harness_errors
def migrate_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """One-shot migration: links/pr_cache/harnesses JSON → state.db (issue #9).

    Also backfills config.json {trackers, repos} into the SQLite tracker
    tables (issue #26): every repos row gets a mandatory tracker_key
    (explicit mapping wins, else origin-remote derivation). Verifies row
    counts, then deletes the JSON files (config.json kept — its
    repos/trackers sections stay as a pre-migration backup). Idempotent:
    re-run is a no-op.

    Example: workagent migrate --json
    """
    from . import store_sqlite as sq
    # Backfill first: migrate_json auto-creates repo rows keyed by path
    # (tracker unknown at that point); the backfill then fills tracker_key
    # from config.json explicit mappings / origin-remote derivation.
    back = sq.backfill_trackers_repos(
        sq.db_path(), store.load_config(),
        derive_tracker=trackers.default_tracker_for_repo)
    out = sq.migrate_json(store.config_dir(), sq.db_path())
    out.update(back)
    _print_result({"migrated": out}, json_output)


# ── shell completion --------------------------------------------------

completions_app = typer.Typer(help="Shell completion: print the init script or install it into your rc file.", no_args_is_help=True)
app.add_typer(completions_app, name="completions")


# ── serve (web UI) ────────────────────────────────────────────────────


def _default_static_dir() -> Path:
    """Built web UI directory: source tree, else the install receipt's source_dir."""
    source = Path(__file__).resolve().parent.parent.parent
    if (source / "web" / "dist" / "index.html").exists():
        return source / "web" / "dist"
    receipt = Path.home() / ".local" / "share" / "workagent" / "install-receipt.json"
    try:
        src: Path | None = Path(
            json.loads(receipt.read_text()).get("source_dir", ""))
    except (OSError, ValueError):
        src = None
    if src is not None and (src / "web" / "dist" / "index.html").exists():
        return src / "web" / "dist"
    return source / "web" / "dist"


@app.command("serve")
@_catch_harness_errors
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (default: loopback only; a non-loopback bind is unauthenticated — see the README security note)."),
    port: int = typer.Option(None, "--port", help="TCP port (default: $PORT, else 3344)."),
    static_dir: Path = typer.Option(None, "--static-dir", help="Built web UI directory (default: <repo>/web/dist)."),
    allowed_host: list[str] = typer.Option(None, "--allowed-host", help="Extra Host header values to accept (repeatable)."),
) -> None:
    """Start the local web UI server (feature parity with the CLI).

    Example:
      workagent serve
      workagent serve --port 3345 --allowed-host devbox.local

    Requires the web extra (fastapi, uvicorn) and a built UI:
      cd web && npm ci && npm run build
    """
    try:
        resolved_port = port or int(os.environ.get("PORT", "3344"))
    except ValueError:
        _fail(f"invalid PORT: {os.environ.get('PORT')!r}", EXIT_GENERAL)
    resolved_static = static_dir or _default_static_dir()
    try:
        from .webapp import run_server
    except ImportError:
        _fail("the web extra is required — install fastapi and uvicorn "
              "(e.g. pip install 'workagent[web]')", EXIT_GENERAL)
    run_server(host, resolved_port, Path(resolved_static), list(allowed_host or []))





@completions_app.command("show")
def completions_show(
    shell: str = typer.Argument(..., help="Shell to print the init script for (bash, zsh, fish)."),
) -> None:
    """Print the shell init script — source it via eval in your rc file.

    Example:
      eval "$(workagent completions show bash)"   # ~/.bashrc
      eval "$(workagent completions show zsh)"    # ~/.zshrc
      workagent completions show fish | source    # fish config
    """
    import typer.main as _typer_main

    try:
        script = _completions.get_completion_script("workagent", shell, click_cmd=_typer_main.get_command(app))
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    wrapper = _completions.cd_wrapper(prog="workagent", shell=shell)
    print(wrapper + script, end="" if script.endswith("\n") else "\n")


@completions_app.command("install")
def completions_install(
    shell: Optional[str] = typer.Argument(None, help="Shell to install for (bash, zsh, fish). Omit: detect from $SHELL."),
    rcfile: Optional[str] = typer.Option(None, "--rcfile", help="Rc file to edit (default: ~/.bashrc, ~/.zshrc, fish config)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation (needed for non-interactive/agent use)."),
) -> None:
    """Install the eval line into your rc file (idempotent; keeps a .bak backup).

    Example:
      workagent completions install          # detect shell from $SHELL
      workagent completions install bash     # explicit shell
      workagent completions install zsh --rcfile ~/.zshrc --yes
    """
    resolved = shell or _completions.detect_shell()
    if resolved is None:
        _fail(f"cannot detect shell from $SHELL={os.environ.get('SHELL', '')!r}; pass bash, zsh, or fish explicitly", EXIT_USAGE)
    if not yes and sys.stdin.isatty() and not typer.confirm(f"Add workagent completion to your {resolved} rc file?"):
        raise typer.Exit(EXIT_USAGE)
    try:
        rc, changed = _completions.install_completion("workagent", resolved, Path(rcfile) if rcfile else None)
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    if changed:
        _completions.print_install_hint("workagent", resolved, rc)
    else:
        print(f"already installed in {rc}", file=sys.stderr)


def main() -> None:
    _completions.ensure_completion_classes()  # typer 0.27: harness server needs registered classes
    argv, harness_args = _split_harness_args(sys.argv[1:])
    _HARNESS_ARGS[:] = harness_args
    warn = _doctor.dev_warning()
    if warn:
        eprint(warn)
    try:
        app(args=argv)
    except HarnessError as e:
        eprint(f"error: {e}")
        sys.exit(e.exit_code)


if __name__ == "__main__":
    main()
