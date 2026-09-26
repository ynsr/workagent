"""Shared CLI kernel: app, exit codes, error/output helpers.

cli.py re-exports this module's names so existing `cli._fail`,
`cli.EXIT_USAGE`, and `cli.app` references keep working during the
god-file split.
"""
from __future__ import annotations

import csv
import functools
import json
import sys
from collections.abc import Callable

import typer

EXIT_OK, EXIT_GENERAL, EXIT_USAGE = 0, 1, 2

app = typer.Typer(
    name="workagent",
    help="Launch AI agent harnesses in git-wt worktrees from issue/PR links.",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    epilog=("Examples:\n"
            "  workagent start https://github.com/OWNER/REPO/issues/22\n"
            "  workagent start OWNER/REPO#22 --repo my-checkout\n"
            "  workagent review https://github.com/OWNER/REPO/pull/33\n"
            "  workagent cleanup OWNER/REPO#22 --force --yes\n"
            "  workagent repo add --name projectx --path ~/projects/projectx\n"
            "  workagent status\n\n"
            "Exit codes: 0=success, 1=error, 2=needs human input / usage error"),
)

# Extra harness args collected in main() by splitting argv on the first
# bare `--` (argparse.REMAINDER-style flags would swallow --repo etc.).
_HARNESS_ARGS: list[str] = []

from .errors import HarnessError


def _fail(msg: str, code: int) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise typer.Exit(code)


def _catch_harness_errors(fn: Callable) -> Callable:
    """Translate HarnessError/KeyboardInterrupt into documented exit codes."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except HarnessError as e:
            _fail(str(e), e.exit_code)
        except KeyboardInterrupt:
            eprint("interrupted")
            raise typer.Exit(130)
    return wrapper


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def _print_result(result, json_output: bool) -> None:
    """Command result: key: value lines by default, JSON with --json."""
    if json_output:
        print(json.dumps(result, indent=2))
        return
    if isinstance(result, dict):
        for k, v in result.items():
            print(f"{k}: {v}")
    else:
        print(result)


def _print_rows(rows: list[dict], json_output: bool, csv_output: bool,
               columns: list[str], title: str, empty: str,
               caption: str | None = None, colorize=None) -> None:
    """Human-first list output: Rich table by default, --csv/--json opt-in.

    Keys starting with "_" are internal (coloring/state hints) and are
    stripped from --json/--csv. ``colorize`` applies Rich markup for the
    table path only.
    """
    plain = [{k: v for k, v in r.items() if not k.startswith("_")}
             for r in rows]
    if json_output:
        print(json.dumps(plain, indent=2, ensure_ascii=False))
        return
    if csv_output:
        w = csv.writer(sys.stdout)
        w.writerow(columns)
        for r in plain:
            w.writerow([r.get(k, "") for k in columns])
        return
    if not rows:
        print(empty)
        return
    from rich.console import Console
    from rich.table import Table
    table = Table(title=title, caption=caption)
    for col in columns:
        table.add_column(col)
    for r in rows:
        shown = colorize(r) if colorize else r
        table.add_row(*[str(shown.get(k, "")) for k in columns])
    Console().print(table)


# --base values treated as "create a new branch off it" (moved from cli.py).
_DEFAULT_BRANCH_NAMES = {"main", "master", "develop"}


def _init_completions() -> dict:
    """Build autocompletion callbacks (deferred: imports repos/completions)."""
    from . import completions as _completions
    from . import repos as _repos

    return {
        "repos": _completions.complete_names(_repos.repo_names),
        "branches": _completions.complete_names(
            _completions._base_branch_candidates),
        "refs": _completions.complete_names(
            _completions._ref_candidates),
    }
