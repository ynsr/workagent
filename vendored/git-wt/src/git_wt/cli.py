"""git-wt — create/finish/cleanup git worktrees for task isolation.

Typer + Rich CLI. stdout carries ONLY command output; every progress,
warning, and confirmation line goes to stderr. Human-first: `list` renders
a Rich table by default; --csv/--json are opt-in for scripting and agents.

Exit codes: 0=success, 1=general error, 2=usage error, 130=interrupted.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from . import completions as _completions
from . import doctor
from .git_utils import GitRepo, GitWtError
from .worktree import cleanup_task, finish_task, start_task

PROG = _completions.PROG

EXIT_OK, EXIT_GENERAL, EXIT_USAGE = 0, 1, 2

# Sentinel value for a value-less --resume (argparse's nargs="?" behavior;
# Click/Typer has no optional-value options, see _normalize_bare_resume).
_RESUME_PICK = "__pick__"


def _version_callback(value: bool) -> None:
    if value:
        print(f"{PROG} {__version__}")
        raise typer.Exit(EXIT_OK)


def _stdin_isatty() -> bool:
    """TTY check (module-level so tests/agents can reason about prompts)."""
    return sys.stdin.isatty()


class OnDirty(str, Enum):
    """How to handle a dirty base branch checkout."""

    stash = "stash"
    commit = "commit"
    push = "push"
    ignore = "ignore"


# Shared option infos: the same flags are accepted before the subcommand
# (app callback) and after it (each command); commands merge both.
VersionOpt = typer.Option(
    None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
)
VerboseOpt = typer.Option(False, "-v", "--verbose", help="Verbose output (extra detail on stderr).")
QuietOpt = typer.Option(False, "-q", "--quiet", help="Suppress non-essential stderr output.")
JsonOpt = typer.Option(False, "--json", help="Structured JSON output on stdout.")
CsvOpt = typer.Option(False, "--csv", help="CSV output (header row) instead of the pretty table.")

_complete_branches = _completions.complete_branch_names
_complete_worktrees = _completions.complete_worktree_paths

app = typer.Typer(
    name=PROG,
    no_args_is_help=True,
    add_completion=False,  # single completion system: `completions show|install`
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
)




@app.callback()
def _main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."
    ),
    verbose: bool = VerboseOpt,
    quiet: bool = QuietOpt,
    json_output: bool = JsonOpt,
) -> None:
    """git-wt — create/finish/cleanup git worktrees for AI agent task isolation.

    Human-first output: `list` renders a Rich table; --csv/--json are
    opt-in for scripts and agents. stdout carries ONLY command output —
    progress, warnings, and confirmations go to stderr. Interactive
    prompts never block a non-TTY run: missing flags fail with an
    actionable message, and --yes/--force/--on-dirty bypass them.

    Example:
      git-wt start --type feat --issue 123 --slug add-login
      git-wt start --resume feat/123--add-login
      git-wt finish --worktree ~/dev/worktrees/projectx/feat/123--add-login
      git-wt cleanup --branch feat/123--add-login --delete-branch
      git-wt list --csv
      git-wt completions show bash

    Exit codes: 0=success, 1=general error (git failure, open PR, stale
    install), 2=usage error (bad flags, missing input, unknown shell),
    130=interrupted (Ctrl-C).
    """
    # Dev-run staleness warning (stderr only; the installed copy never warns).
    warn = doctor.dev_warning()
    if warn:
        print(warn, file=sys.stderr)


def _fail(msg: str, code: int = EXIT_GENERAL):
    """Print an error to stderr and exit with the given code."""
    print(f"error: {msg}", file=sys.stderr)
    raise typer.Exit(code)


def _flags(
    ctx: typer.Context,
    *,
    verbose: bool,
    quiet: bool,
    json_output: bool,
    csv_output: bool = False,
) -> dict[str, bool]:
    """Merge global flags given before the subcommand with per-command ones."""
    parent = ctx.parent.params if ctx.parent is not None else {}
    return {
        "verbose": bool(verbose or parent.get("verbose")),
        "quiet": bool(quiet or parent.get("quiet")),
        "json": bool(json_output or parent.get("json_output")),
        "csv": bool(csv_output),
    }


def _confirm_tty(prompt: str) -> bool:
    """Ask a yes/no question on a TTY. Prompt text goes to stderr."""
    print(prompt, file=sys.stderr)
    try:
        answer = input("[y/N] ")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    return answer.strip().lower() in ("y", "yes")


def _emit_result(data: dict, *, verbose: bool, json_output: bool) -> None:
    """Print a command result: JSON on stdout, or message (+ cd line) only."""
    if json_output:
        print(json.dumps(data, indent=2, default=str))
        return
    msg = data.get("message", "")
    if msg:
        print(msg)
    cd_command = data.get("cd_command")
    if cd_command:
        print(cd_command)
    if verbose:
        for key, value in data.items():
            if key not in ("message", "cd_command") and value is not None:
                print(f"  {key}: {value}", file=sys.stderr)


# ── start ────────────────────────────────────────────────────────────


@app.command("start")
def start(
    ctx: typer.Context,
    branch_name: Optional[str] = typer.Argument(
        None, help="Full branch name (positional, e.g. chor/IPG-806--slug)."
    ),
    branch: Optional[str] = typer.Option(
        None, "--branch", autocompletion=_complete_branches,
        help="Explicit branch name (alternative to positional).",
    ),
    type_: Optional[str] = typer.Option(
        None, "--type", help="Branch type prefix (feat, fix, chore, docs, refactor)."
    ),
    issue: Optional[str] = typer.Option(
        None, "--issue", help="Issue/ticket id (folded into branch name)."
    ),
    slug: Optional[str] = typer.Option(
        None, "--slug", help="Short description (folded into branch name)."
    ),
    link: Optional[str] = typer.Option(
        None, "--link",
        help="GitHub/GitLab/Jira/Todoist issue URL — auto-detects tool and fetches title.",
    ),
    base: Optional[str] = typer.Option(None, "--base", help="Base branch (default: repo default)."),
    repo: Optional[Path] = typer.Option(None, "--repo", help="Repo path (default: current directory)."),
    ephemeral: bool = typer.Option(
        False, "--ephemeral", help="Create worktree in a temp dir instead of ~/dev/worktrees/."
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", autocompletion=_complete_branches,
        help="Resume an existing branch/worktree (omit the value to pick interactively).",
    ),
    on_dirty: Optional[OnDirty] = typer.Option(
        None, "--on-dirty", help="How to handle a dirty base branch checkout."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-create the branch if it already exists locally."
    ),
    version: Optional[bool] = VersionOpt,
    verbose: bool = VerboseOpt,
    quiet: bool = QuietOpt,
    json_output: bool = JsonOpt,
) -> None:
    """Create or resume a worktree for a task.

    Example:
      git-wt start --type feat --issue 123 --slug add-login
      git-wt start --branch feat/123--add-login --ephemeral
      git-wt start --resume feat/123--add-login
      git-wt start --link https://tribe.jibit.cloud/browse/IPG-930
      git-wt start --link https://github.com/owner/repo/issues/22
      git-wt start chor/IPG-806--my-slug
    """
    flags = _flags(ctx, verbose=verbose, quiet=quiet, json_output=json_output)
    try:
        resume_branch: Optional[str]
        if resume == _RESUME_PICK:
            # Bare --resume: pick the branch interactively (TTY only).
            selected = _interactive_select_worktree(repo, hint="--resume <branch>")
            resume_branch = selected["branch"]
        else:
            resume_branch = resume
        result = start_task(
            repo_path=repo,
            branch=branch_name or branch,
            type_=type_,
            issue=issue,
            slug=slug,
            link=link,
            base=base,
            ephemeral=ephemeral,
            resume=resume_branch,
            on_dirty=on_dirty.value if on_dirty else None,
            force=force,
        )
    except GitWtError as e:
        _fail(str(e), e.exit_code)
    wt = result.get("worktree_path", "")
    if not isinstance(wt, str):
        wt = str(wt) if wt else ""
    branch_out = result.get("branch", "")
    action = result.get("action", "created")
    msg = f"Created worktree at: {wt}"
    if action == "resumed":
        msg = f"Resumed worktree at: {wt}"
    elif action == "recreated":
        msg = f"Recreated worktree at: {wt}"
    _emit_result(
        {
            "message": msg,
            "worktree_path": wt,
            "cd_command": f"cd {wt}",
            "branch": branch_out,
            "base": result.get("base", ""),
            "action": action,
        },
        verbose=flags["verbose"],
        json_output=flags["json"],
    )


# ── finish ───────────────────────────────────────────────────────────


@app.command("finish")
def finish(
    ctx: typer.Context,
    worktree: Optional[Path] = typer.Option(
        None, "--worktree", autocompletion=_complete_worktrees,
        help="Worktree path (default: interactive picker if omitted).",
    ),
    base: Optional[str] = typer.Option(None, "--base", help="PR target branch (default: repo default)."),
    title: Optional[str] = typer.Option(None, "--title", help="PR title (default: derived from branch name)."),
    skip_tests: bool = typer.Option(False, "--skip-tests", help="Skip the test run."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    version: Optional[bool] = VersionOpt,
    verbose: bool = VerboseOpt,
    quiet: bool = QuietOpt,
    json_output: bool = JsonOpt,
) -> None:
    """Push branch and create a draft PR/MR.

    Example:
      git-wt finish --worktree ~/dev/worktrees/projectx/feat/123--add-login --skip-tests
      git-wt finish
    """
    flags = _flags(ctx, verbose=verbose, quiet=quiet, json_output=json_output)
    try:
        if worktree is None:
            selected = _interactive_select_worktree(None, hint="--worktree <path>")
            worktree = Path(selected["path"])
        result = finish_task(worktree, base=base, title=title, skip_tests=skip_tests)
    except GitWtError as e:
        _fail(str(e), e.exit_code)
    pr_url = result.get("pr_url", "none")
    _emit_result(
        {
            "message": f"Pushed {result.get('branch', '')}, PR: {pr_url}",
            "branch": result.get("branch", ""),
            "pr_url": pr_url,
            "tests_ran": result.get("tests_ran"),
        },
        verbose=flags["verbose"],
        json_output=flags["json"],
    )


# ── cleanup ──────────────────────────────────────────────────────────


@app.command("cleanup")
def cleanup(
    ctx: typer.Context,
    branch: Optional[str] = typer.Option(
        None, "--branch", autocompletion=_complete_branches,
        help="Branch to clean up (omit to pick interactively).",
    ),
    repo: Optional[Path] = typer.Option(None, "--repo", help="Repo path (default: current directory)."),
    force: bool = typer.Option(
        False, "--force", help="Remove even if PR is open or state unknown."
    ),
    delete_branch: bool = typer.Option(False, "--delete-branch", help="Also delete local and remote branch."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    version: Optional[bool] = VersionOpt,
    verbose: bool = VerboseOpt,
    quiet: bool = QuietOpt,
    json_output: bool = JsonOpt,
) -> None:
    """Remove a worktree and optionally its branch.

    Example:
      git-wt cleanup --branch feat/123--add-login --delete-branch
      git-wt cleanup --branch feat/123--add-login --yes --json
    """
    flags = _flags(ctx, verbose=verbose, quiet=quiet, json_output=json_output)
    try:
        if branch is None:
            selected = _interactive_select_worktree(repo, hint="--branch <name>")
            branch = selected["branch"]
        # PR-state gate: --force/--yes bypass; on a TTY a confirmation is
        # offered; non-TTY without a bypass fails with an actionable message.
        if force or yes:
            confirmed: bool | Callable[[str], bool] | None = True
        elif _stdin_isatty():
            confirmed = _confirm_tty
        else:
            confirmed = None
        result = cleanup_task(
            repo_path=repo,
            branch=branch,
            force=force,
            delete_branch=delete_branch,
            confirm=confirmed,
        )
    except GitWtError as e:
        _fail(str(e), e.exit_code)
    actions = result.get("actions", [])
    _emit_result(
        {
            "message": "; ".join(actions) if actions else f"No worktree found for branch '{branch}'",
            "actions": actions,
        },
        verbose=flags["verbose"],
        json_output=flags["json"],
    )


# ── list ─────────────────────────────────────────────────────────────


@app.command("list")
def list_cmd(
    ctx: typer.Context,
    repo: Optional[Path] = typer.Option(None, "--repo", help="Repo path (default: current directory)."),
    version: Optional[bool] = VersionOpt,
    verbose: bool = VerboseOpt,
    quiet: bool = QuietOpt,
    json_output: bool = JsonOpt,
    csv_output: bool = CsvOpt,
) -> None:
    """List worktrees for a repo (Rich table; --csv/--json for machines).

    Example:
      git-wt list
      git-wt list --csv
      git-wt list --json | jq '.[].branch'
    """
    flags = _flags(ctx, verbose=verbose, quiet=quiet, json_output=json_output, csv_output=csv_output)
    try:
        worktrees = GitRepo(repo).worktree_paths()
    except GitWtError as e:
        _fail(str(e), e.exit_code)

    if flags["json"]:
        print(json.dumps(worktrees, indent=2, default=str))
        return
    if flags["csv"]:
        writer = csv.writer(sys.stdout)
        writer.writerow(["branch", "path"])
        for wt in worktrees:
            writer.writerow([wt.get("branch") or "(detached)", wt.get("path", "")])
        return
    if not worktrees:
        print("No worktrees found.")
        return
    table = Table(box=None, pad_edge=False)
    table.add_column("Branch", style="cyan", no_wrap=True)
    table.add_column("Path", style="default")
    table.add_column("HEAD", style="dim")
    for wt in worktrees:
        branch = wt.get("branch") or "(detached)"
        head = (wt.get("head") or "")[:7]
        table.add_row(branch, wt.get("path", "?"), head)
    Console().print(table)


# ── shell completion ─────────────────────────────────────────────────


completions_app = typer.Typer(
    help="Shell completion: print the init script or install it into your rc file.",
    no_args_is_help=True,
)
app.add_typer(completions_app, name="completions")


@completions_app.command("show")
def completions_show(
    shell: str = typer.Argument(..., help="Shell to print the init script for (bash, zsh, fish)."),
) -> None:
    """Print the shell init script — source it via eval in your rc file.

    Example:
      eval "$(git-wt completions show bash)"   # ~/.bashrc
      eval "$(git-wt completions show zsh)"    # ~/.zshrc
      git-wt completions show fish | source    # fish config
    """
    import typer.main as typer_main

    try:
        script = _completions.get_completion_script(PROG, shell, click_cmd=typer_main.get_command(app))
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    print(script, end="" if script.endswith("\n") else "\n")


@completions_app.command("install")
def completions_install(
    shell: Optional[str] = typer.Argument(
        None, help="Shell to install for (bash, zsh, fish). Omit: detect from $SHELL."
    ),
    rcfile: Optional[Path] = typer.Option(
        None, "--rcfile",
        help="Rc file to edit (default: ~/.bashrc, ~/.zshrc, or ~/.config/fish/config.fish).",
    ),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation prompt (needed for non-interactive/agent use)."),
    quiet: bool = QuietOpt,
) -> None:
    """Install the eval line into your rc file (idempotent; keeps a .bak backup).

    Example:
      git-wt completions install          # detect shell from $SHELL
      git-wt completions install bash     # explicit shell
      git-wt completions install zsh --rcfile ~/.zshrc --yes
    """
    resolved = shell or _completions.detect_shell()
    if resolved is None:
        _fail(
            f"could not detect a supported shell from $SHELL={os.environ.get('SHELL', '')!r} "
            f"— pass one: {'|'.join(_completions.SUPPORTED_SHELLS)}",
            EXIT_USAGE,
        )
    default_rc = Path(rcfile).expanduser() if rcfile else Path(_completions.RC_FILES[resolved]).expanduser()
    # Confirm on a TTY (rc edit is destructive-ish); --yes bypasses;
    # non-interactive runs proceed (idempotent + .bak backup).
    if _stdin_isatty() and not yes:
        if not _confirm_tty(f"Install {PROG} completion for {resolved} into {default_rc}?"):
            _fail("aborted — rc file not modified", EXIT_GENERAL)
    try:
        rc, changed = _completions.install_completion(PROG, resolved, rcfile)
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    if not quiet:
        if changed:
            print(f"installed {PROG} completion for {resolved} in {rc}", file=sys.stderr)
            print(f"restart your shell or run: source {rc}", file=sys.stderr)
        else:
            print(f"already installed in {rc}", file=sys.stderr)


# ── doctor ───────────────────────────────────────────────────────────


@app.command("doctor")
def doctor_cmd(
    ctx: typer.Context,
    version: Optional[bool] = VersionOpt,
    verbose: bool = VerboseOpt,
    quiet: bool = QuietOpt,
    json_output: bool = JsonOpt,
) -> None:
    """Check the installed copy is in sync with the source tree.

    Example:
      git-wt doctor

    Exit codes: 0 in sync · 1 stale/missing receipt (fix: ./install.sh).
    """
    flags = _flags(ctx, verbose=verbose, quiet=quiet, json_output=json_output)
    status, message = doctor.check()
    if flags["json"]:
        print(json.dumps({"status": status, "message": message}, indent=2))
    else:
        print(message)
    if status != doctor.OK:
        raise typer.Exit(EXIT_GENERAL)


# ── interactive worktree picker ──────────────────────────────────────


def _interactive_select_worktree(
    repo_path: str | Path | None = None,
    *,
    hint: str = "--worktree <path>",
) -> dict:
    """Prompt user to pick a worktree interactively (TTY only).

    Uses fzf if available (best UX), otherwise a numbered menu via stdin.
    Non-TTY: raise GitWtError (exit 2) telling the caller which flag to pass.
    """
    repo = GitRepo(repo_path)
    wts = repo.worktree_paths()

    # Filter to non-bare worktrees with a branch
    choices = [wt for wt in wts if wt.get("branch") and not wt.get("bare")]
    if not choices:
        raise GitWtError("no worktrees available to select")

    # Non-interactive runs never block: fail with the actionable flag name.
    if not _stdin_isatty():
        raise GitWtError(
            f"no worktree specified and stdin is not a TTY — pass {hint} explicitly",
            exit_code=2,
        )

    # Build display lines: "branch  (path)"
    lines = [f"{wt['branch']}  ({wt['path']})" for wt in choices]

    # Try fzf if available (faster, filterable)
    if shutil.which("fzf"):
        import subprocess

        result = subprocess.run(
            ["fzf", "--prompt=Select worktree> "],
            input="\n".join(lines),
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            selected_line = result.stdout.strip()
            branch = selected_line.split("  (")[0].strip()
            for wt in choices:
                if wt["branch"] == branch:
                    return wt

    # Fallback: numbered menu
    print("\nAvailable worktrees:", file=sys.stderr)
    for i, wt in enumerate(choices, 1):
        print(f"  {i:3}. {wt['branch']:45s} {wt['path']}", file=sys.stderr)
    print(file=sys.stderr)
    try:
        choice = input("Select worktree (number or branch name): ").strip()
    except (EOFError, KeyboardInterrupt):
        raise GitWtError("aborted", exit_code=130)

    if not choice:
        raise GitWtError("no worktree selected", exit_code=2)

    # Try number first
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    except ValueError:
        pass

    # Try branch name match
    for wt in choices:
        if wt["branch"] == choice:
            return wt

    raise GitWtError(f"invalid selection: {choice}", exit_code=2)


# ── entry point ──────────────────────────────────────────────────────


def _normalize_bare_resume(argv: list[str]) -> list[str]:
    """Rewrite a value-less --resume into the interactive-pick sentinel.

    argparse gave --resume nargs="?" (bare flag = pick interactively).
    Click/Typer has no optional-value options, so normalize argv before
    parsing with argparse's exact disambiguation: a following option-like
    token (starts with '-') is NOT consumed as the value.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--resume":
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is None or (nxt.startswith("-") and nxt != "-"):
                out.append(f"--resume={_RESUME_PICK}")
                i += 1
                continue
        out.append(tok)
        i += 1
    return out


def main() -> None:
    _completions.ensure_completion_classes()  # typer 0.27: runtime server needs registered classes
    if "--resume" in sys.argv[1:]:
        sys.argv[1:] = _normalize_bare_resume(sys.argv[1:])
    try:
        app()
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
