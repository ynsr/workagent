"""git-wt CLI — create/finish/cleanup git worktrees for task isolation."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from . import doctor
from .completions import (
    PROG,
    RC_FILES,
    SUPPORTED_SHELLS,
    detect_shell,
    get_completion_script,
    install_completion,
)
from .git_utils import GitRepo, GitWtError
from .worktree import (
    cleanup_task,
    finish_task,
    start_task,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="git-wt",
        description="Create, finish, and clean up git worktrees for AI agent task isolation.",
        epilog=(
            "Examples:\n"
            "  git-wt start --type feat --issue 123 --slug add-login\n"
            "  git-wt start --branch feat/123--add-login --ephemeral\n"
            "  git-wt start --resume feat/123--add-login\n"
            "  git-wt start --link https://tribe.jibit.cloud/browse/IPG-930\n"
            "  git-wt start --link https://github.com/owner/repo/issues/22\n"
            "  git-wt start chor/IPG-806--my-slug\n"
            "  git-wt finish --worktree ~/dev/worktrees/projectx/feat/123--add-login --skip-tests\n"
            "  git-wt cleanup --branch feat/123--add-login --delete-branch\n"
            "  git-wt list\n\n"
            "Exit codes: 0=success, 1=error, 2=needs human input"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}", help="show version and exit")

    # Global flags — accepted before the subcommand. Subcommands define
    # their own --json/-v (default SUPPRESS) so they also work after it;
    # the same args attribute is written either way.
    parser.add_argument("-v", "--verbose", action="store_true", default=False,
                        help="Verbose output")
    parser.add_argument("-q", "--quiet", action="store_true", default=False,
                        help="Suppress non-essential stderr output")
    parser.add_argument("--json", action="store_true", default=False,
                        help="Output as JSON (stdout)")

    # Stderr-only flags are parsed before subcommand dispatch
    subparsers = parser.add_subparsers(dest="command", required=False)

    # ── start ───────────────────────────────────────────────────────
    sp = subparsers.add_parser("start", help="Create or resume a worktree for a task")
    sp.add_argument("branch_name", nargs="?", default=None,
                    help="Full branch name (positional, e.g. chor/IPG-806--slug)")
    sp.add_argument("--branch", help="Explicit branch name (alternative to positional)")
    sp.add_argument("--type", choices=["feat", "fix", "chore", "docs", "refactor"],
                    help="Branch type prefix")
    sp.add_argument("--issue", help="Issue/ticket id (folded into branch name)")
    sp.add_argument("--slug", help="Short description (folded into branch name)")
    sp.add_argument("--link", help="GitHub/GitLab/Jira/Todoist issue URL — auto-detects tool and fetches title")
    sp.add_argument("--base", help="Base branch (default: repo default)")
    sp.add_argument("--repo", type=Path, help="Repo path (default: current directory)")
    sp.add_argument("--ephemeral", action="store_true",
                    help="Create worktree in a temp dir instead of ~/dev/worktrees/")
    sp.add_argument("--resume", nargs="?", const=True, default=False,
                    help="Resume an existing branch/worktree (omit value to pick interactively)")
    sp.add_argument("--on-dirty", choices=["stash", "commit", "push", "ignore"],
                    help="How to handle dirty base branch checkout")
    sp.add_argument("--force", action="store_true",
                    help="Re-create branch if it already exists locally")
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON (stdout)")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Verbose output")
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress non-essential stderr output")

    # ── finish ──────────────────────────────────────────────────────
    sp = subparsers.add_parser("finish", help="Push branch and create a draft PR/MR")
    sp.add_argument("--worktree", type=Path, default=None,
                    help="Worktree path (default: interactive picker if omitted)")
    sp.add_argument("--base", help="PR target branch (default: repo default)")
    sp.add_argument("--title", help="PR title (default: derived from branch name)")
    sp.add_argument("--skip-tests", action="store_true", help="Skip the test run")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON (stdout)")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Verbose output")
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress non-essential stderr output")

    # ── cleanup ─────────────────────────────────────────────────────
    sp = subparsers.add_parser("cleanup", help="Remove a worktree and optionally its branch")
    sp.add_argument("--branch", default=None,
                    help="Branch to clean up (omit to pick interactively)")
    sp.add_argument("--repo", type=Path, help="Repo path (default: current directory)")
    sp.add_argument("--force", action="store_true",
                    help="Remove even if PR is open or state unknown")
    sp.add_argument("--delete-branch", action="store_true",
                    help="Also delete local and remote branch")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON (stdout)")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Verbose output")
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress non-essential stderr output")

    # ── list ────────────────────────────────────────────────────────
    sp = subparsers.add_parser("list", help="List worktrees for a repo")
    sp.add_argument("--repo", type=Path, help="Repo path (default: current directory)")
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON (stdout)")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Verbose output")
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress non-essential stderr output")

    # ── completion ──────────────────────────────────────────────────
    sp = subparsers.add_parser(
        "completion",
        help="Print a shell completion script — eval it in your rc file",
    )
    sp.add_argument("shell", choices=SUPPORTED_SHELLS,
                    help="Shell to print the script for (bash, zsh, fish)")

    # ── completion-install ──────────────────────────────────────────
    sp = subparsers.add_parser(
        "completion-install",
        help="Install completion into your rc file (idempotent; keeps a .bak backup)",
    )
    sp.add_argument("shell", nargs="?", default=None,
                    help="Shell (default: detect from $SHELL)")
    sp.add_argument("--rcfile", type=Path, default=None,
                    help="Rc file to edit (default: ~/.bashrc, ~/.zshrc, or ~/.config/fish/config.fish)")
    sp.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Verbose output")
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress non-essential stderr output")

    # ── doctor ──────────────────────────────────────────────────────
    sp = subparsers.add_parser(
        "doctor",
        help="Check the installed copy is in sync with the source tree",
    )
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Output as JSON (stdout)")
    sp.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS, help="Verbose output")
    sp.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help="Suppress non-essential stderr output")

    args = parser.parse_args()

    # Dev-run staleness warning (stderr only; the installed copy never warns).
    warn = doctor.dev_warning()
    if warn:
        eprint(warn)

    # Handle no subcommand
    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Dispatch
    try:
        result = _dispatch(args)
    except GitWtError as e:
        eprint(f"error: {e}")
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        eprint("interrupted")
        sys.exit(130)

    # Output
    if isinstance(result, list):
        _output(args, "\n".join(str(r) for r in result) if not args.json else result)
    elif isinstance(result, dict):
        _output(args, result)
    elif result is not None:
        _output(args, result)


# ── dispatch ─────────────────────────────────────────────────────────


def _dispatch(args: argparse.Namespace) -> dict | list | str | None:
    """Dispatch to the appropriate handler based on args.command."""
    if args.command == "start":
        return _cmd_start(args)
    elif args.command == "finish":
        return _cmd_finish(args)
    elif args.command == "cleanup":
        return _cmd_cleanup(args)
    elif args.command == "list":
        return _cmd_list(args)
    elif args.command == "completion":
        return _cmd_completion(args)
    elif args.command == "completion-install":
        return _cmd_completion_install(args)
    elif args.command == "doctor":
        return _cmd_doctor(args)
    return None


_UNSET = object()


def _cmd_start(args: argparse.Namespace) -> dict:
    """Handle `git-wt start`."""
    # Handle interactive resume: --resume without a value
    resume_branch: str | None
    if args.resume is True:
        # --resume flag given without a value → interactive picker
        selected = _interactive_select_worktree(args.repo)
        resume_branch = selected["branch"]
    elif args.resume:
        resume_branch = args.resume
    else:
        resume_branch = None

    # Resolve branch: positional branch_name overrides --branch
    branch = args.branch_name or args.branch

    result = start_task(
        repo_path=args.repo,
        branch=branch,
        type_=args.type,
        issue=args.issue,
        slug=args.slug,
        link=args.link,
        base=args.base,
        ephemeral=args.ephemeral,
        resume=resume_branch,
        on_dirty=args.on_dirty,
        force=args.force,
    )

    wt = result.get("worktree_path", "")
    branch = result.get("branch", "")
    base = result.get("base", "")
    action = result.get("action", "created")

    if not isinstance(wt, str):
        wt = str(wt) if wt else ""

    msg = f"Created worktree at: {wt}"
    if action == "resumed":
        msg = f"Resumed worktree at: {wt}"
    elif action == "recreated":
        msg = f"Recreated worktree at: {wt}"

    return {
        "message": msg,
        "worktree_path": wt,
        "cd_command": f"cd {wt}",
        "branch": branch,
        "base": base,
        "action": action,
    }


def _cmd_finish(args: argparse.Namespace) -> dict:
    """Handle `git-wt finish`."""
    worktree = args.worktree
    if worktree is None:
        # No --worktree given → interactive picker
        selected = _interactive_select_worktree(args.repo)
        worktree = Path(selected["path"])

    result = finish_task(
        worktree,
        base=args.base,
        title=args.title,
        skip_tests=args.skip_tests,
    )

    pr_url = result.get("pr_url", "none")
    branch = result.get("branch", "")

    return {
        "message": f"Pushed {branch}, PR: {pr_url}",
        "branch": branch,
        "pr_url": pr_url,
        "tests_ran": result.get("tests_ran"),
    }


def _cmd_cleanup(args: argparse.Namespace) -> dict:
    """Handle `git-wt cleanup`."""
    branch = args.branch
    if branch is None:
        # No --branch given → interactive picker
        selected = _interactive_select_worktree(args.repo)
        branch = selected["branch"]

    result = cleanup_task(
        repo_path=args.repo,
        branch=branch,
        force=args.force,
        delete_branch=args.delete_branch,
    )

    actions = result.get("actions", [])
    return {
        "message": "; ".join(actions) if actions else f"No worktree found for branch '{branch}'",
        "actions": actions,
    }


def _cmd_list(args: argparse.Namespace) -> list[dict] | str:
    """Handle `git-wt list`."""
    repo = GitRepo(args.repo)
    worktrees = repo.worktree_paths()

    if not worktrees:
        return "No worktrees found."

    if args.json:
        return worktrees

    lines = []
    for wt in worktrees:
        branch = wt.get("branch") or "(detached)"
        path = wt.get("path", "?")
        lines.append(f"{path:60s} {branch}")
    return "\n".join(lines)


# ── shell completion handlers ────────────────────────────────────────


def _cmd_completion(args: argparse.Namespace) -> None:
    """Handle `git-wt completion` — print the script to stdout."""
    try:
        script = get_completion_script(PROG, args.shell)
    except ValueError as e:
        raise GitWtError(str(e), exit_code=2)
    sys.stdout.write(script)


def _cmd_completion_install(args: argparse.Namespace) -> dict:
    """Handle `git-wt completion-install` — idempotent rc edit."""
    shell = args.shell or detect_shell()
    if shell not in SUPPORTED_SHELLS:
        raise GitWtError(
            "could not detect a supported shell from $SHELL — pass one: "
            f"{'|'.join(SUPPORTED_SHELLS)}",
            exit_code=2,
        )
    default_rc = (
        Path(args.rcfile).expanduser() if args.rcfile
        else Path(RC_FILES[shell]).expanduser()
    )
    # Confirm on a TTY (rc edit is destructive-ish); --yes bypasses,
    # non-interactive runs proceed (idempotent + .bak backup).
    if sys.stdin.isatty() and not args.yes:
        try:
            answer = input(f"Install {PROG} completion for {shell} into {default_rc}? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            eprint("aborted")
            sys.exit(130)
        if answer.strip().lower() not in ("y", "yes"):
            eprint("aborted — rc file not modified")
            sys.exit(1)
    try:
        rc, changed = install_completion(PROG, shell, rcfile=args.rcfile)
    except ValueError as e:
        raise GitWtError(str(e), exit_code=2)
    if changed:
        if not args.quiet:
            print(f"restart your shell or run: source {rc}", file=sys.stderr)
        return {
            "message": f"installed {PROG} completion for {shell} in {rc}",
            "rc": str(rc),
            "changed": True,
        }
    if not args.quiet:
        eprint(f"already installed in {rc}")
    return {
        "message": f"{PROG} completion for {shell} already installed in {rc}",
        "rc": str(rc),
        "changed": False,
    }


def _cmd_doctor(args: argparse.Namespace) -> None:
    """Handle `git-wt doctor` — install-sync self-check (0 ok, 1 stale/missing)."""
    status, message = doctor.check()
    _output(args, {"status": status, "message": message})
    if status != "ok":
        sys.exit(1)


# ── output helpers ───────────────────────────────────────────────────


def _output(args: argparse.Namespace, data: dict | list | str) -> None:
    """Print output: JSON to stdout (with stderr for progress) or plain text."""
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    elif isinstance(data, dict):
        msg = data.get("message", "")
        if msg:
            print(msg)
        cd_cmd = data.get("cd_command")
        if cd_cmd:
            print(cd_cmd)
        # In verbose mode, show full result
        if args.verbose and not args.quiet:
            for k, v in data.items():
                if k != "message" and v is not None:
                    eprint(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            print(item)
    else:
        print(data)


def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)


# ── interactive worktree picker ────────────────────────────────────


def _interactive_select_worktree(
    repo_path: str | Path | None = None,
) -> dict:
    """Prompt user to pick a worktree interactively.

    Uses fzf if available (best UX), otherwise a numbered menu via stdin.
    Returns the selected worktree dict with 'branch' and 'path'.
    """
    from .git_utils import GitRepo, GitWtError

    repo = GitRepo(repo_path)
    wts = repo.worktree_paths()

    # Filter to non-bare worktrees with a branch
    choices = [wt for wt in wts if wt.get("branch") and not wt.get("bare")]
    if not choices:
        raise GitWtError("no worktrees available to select")

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

    # Fallback: numbered menu (requires TTY)
    if not sys.stdin.isatty():
        raise GitWtError(
            "no worktree specified and not in interactive terminal — "
            "provide --worktree / --branch / --resume <name> explicitly",
            exit_code=2,
        )

    eprint("\nAvailable worktrees:")
    for i, wt in enumerate(choices, 1):
        eprint(f"  {i:3}. {wt['branch']:45s} {wt['path']}")
    eprint()
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


if __name__ == "__main__":
    main()