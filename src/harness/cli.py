"""harness CLI — launch AI agent harnesses in git-wt worktrees from issue/PR links.

stdout carries ONLY command output; every log/progress/confirmation line
goes to stderr.
"""

from __future__ import annotations

import csv
import functools
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

import typer

from . import __version__, backend, gitwt, refs, repos, store
from . import completions as _completions
from . import doctor as _doctor
from .errors import HarnessError

EXIT_OK, EXIT_GENERAL, EXIT_USAGE = 0, 1, 2

# Extra harness args collected in main() by splitting argv on the first
# bare `--` (argparse.REMAINDER-style flags would swallow --repo etc.).
_HARNESS_ARGS: list[str] = []

app = typer.Typer(
    name="harness",
    help="Launch AI agent harnesses in git-wt worktrees from issue/PR links.",
    no_args_is_help=True,
    add_completion=False,  # single completion system: `completions show|install` (see completions.py)
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
    epilog=("Examples:\n"
            "  harness start https://github.com/OWNER/REPO/issues/22\n"
            "  harness start OWNER/REPO#22 --repo my-checkout\n"
            "  harness review https://github.com/OWNER/REPO/pull/33\n"
            "  harness cleanup OWNER/REPO#22 --force --yes\n"
            "  harness repo add --name projectx --path ~/projects/projectx\n"
            "  harness status\n\n"
            "Exit codes: 0=success, 1=error, 2=needs human input / usage error"),
)

_complete_repos = _completions.complete_names(repos.repo_names)


def _version_callback(value: bool) -> None:
    if value:
        print(f"harness {__version__}")
        raise typer.Exit(0)


@app.callback()
def _main(
    version: Optional[bool] = typer.Option(None, "--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose output (extra detail on stderr)."),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress non-essential output on stderr."),
) -> None:
    """Global options."""


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
                columns: list[str], title: str, empty: str) -> None:
    """Human-first list output: Rich table by default, --csv/--json opt-in."""
    if json_output:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if csv_output:
        w = csv.writer(sys.stdout)
        w.writerow(columns)
        for r in rows:
            w.writerow([r.get(k, "") for k in columns])
        return
    if not rows:
        print(empty)
        return
    from rich.console import Console
    from rich.table import Table
    table = Table(title=title)
    for col in columns:
        table.add_column(col)
    for r in rows:
        table.add_row(*[str(r.get(k, "")) for k in columns])
    Console().print(table)


def _split_harness_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first bare `--`; everything after feeds the harness."""
    if "--" in argv and argv.index("--") > 0:
        cut = argv.index("--")
        return argv[:cut], [a for a in argv[cut + 1:] if a != "--"]
    return argv, []


# ── start ─────────────────────────────────────────────────────────────


@app.command("start")
@_catch_harness_errors
def start(
    ref: str = typer.Argument(..., help="Issue key/URL, OWNER/REPO#NUM, or bare number."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    base: Optional[str] = typer.Option(None, "--base", help="Base branch (default: repo default)."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively (auto commit/push/MR prompt suffix)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Create worktree from issue and launch harness.

    Example:
      harness start https://github.com/OWNER/REPO/issues/22
      harness start OWNER/REPO#22 --repo my-checkout --no-tty
      harness start OWNER/REPO#22 --dry-run
    """
    parsed = refs.parse_ref(ref)
    if parsed["kind"] in ("pr", "mr"):
        _fail(f"{ref} looks like a PR/MR — use `harness review`", EXIT_USAGE)
    r = repos.resolve_repo(repo, Path.cwd(), depth=depth)
    base_branch = base or repos.default_branch(r)
    cur = repos.current_branch(r) if repos.repo_root(Path.cwd()) == r else None
    if cur and cur != base_branch and not yes and not dry_run and sys.stdin.isatty():
        eprint(f"note: repo is on '{cur}', worktree will branch from '{base_branch}'.")

    issue = refs.fetch_issue(parsed)
    key = refs.issue_key(parsed)
    harness_name = harness or store.load_config().get("default_harness", "omp")

    # Jira: pass a full browse URL to git-wt --link (it detects the tracker
    # from the URL path); jira-cli itself takes the bare key.
    if parsed["tool"] == "jira-cli" and not parsed["url"].startswith("http"):
        link_url = f"https://tribe.jibit.cloud/browse/{parsed['number']}"
    else:
        link_url = parsed["url"] if parsed["tool"] == "jira-cli" or parsed["repo"] else None

    issue_id, slug = gitwt.build_branch_for_issue(parsed, issue["title"])
    if parsed["tool"] == "jira-cli" and not issue_id:
        issue_id = parsed["number"]

    if dry_run:
        _print_result({"dry_run": True, "repo": str(r), "base": base_branch,
                       "issue": {"title": issue["title"]}, "harness": harness_name,
                       "key": key, "link": link_url}, json_output)
        return
    wt = gitwt.start_worktree(r, issue=issue_id, slug=slug, link=link_url, base=base_branch)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", "")
    store.record_link(key, {"issue": ref, "worktree": worktree,
                            "branch": branch, "repo": str(r)})
    # Remember the repo under a derived name for future --repo picks.
    try:
        repos.register_repo(repos.repo_name(r), r)
    except HarnessError:
        pass

    prompt = backend.prompt_for_issue(issue["title"], issue["body"], ref)
    if no_tty:
        prompt += "\n\nAfter task done, commit, push and create an MR/PR to the default branch"
    eprint(f"worktree: {worktree}  branch: {branch}")
    backend.launch(harness_name, prompt, worktree or str(r), no_tty, _HARNESS_ARGS)
    _print_result({"worktree_path": worktree, "branch": branch, "base": base_branch,
                   "key": key, "harness": harness_name}, json_output)


# ── review ────────────────────────────────────────────────────────────


@app.command("review")
@_catch_harness_errors
def review(
    ref: str = typer.Argument(..., help="PR/MR URL or OWNER/REPO#NUM."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Create worktree from PR/MR and launch review.

    Example:
      harness review https://github.com/OWNER/REPO/pull/33
      harness review OWNER/REPO#33 --no-tty
    """
    parsed = refs.parse_ref(ref)
    repo_dir = repos.resolve_repo(repo, Path.cwd(), depth=depth)
    base_branch = repos.default_branch(repo_dir)
    harness_name = harness or store.load_config().get("default_harness", "omp")

    pr_url = parsed["url"] if parsed["repo"] else ref
    info: dict = {}
    if parsed["repo"] and parsed["kind"] in ("pr", "mr"):
        try:
            info = refs.fetch_pr_info(parsed)
        except HarnessError as e:
            eprint(f"warning: {e}")
    head_ref = info.get("head_ref", "")

    if dry_run:
        _print_result({"dry_run": True, "repo": str(repo_dir), "pr_url": pr_url,
                       "harness": harness_name, "head_ref": head_ref}, json_output)
        return

    if head_ref:
        wt = gitwt.start_worktree(repo_dir, branch=head_ref, base=base_branch)
    else:
        # No head ref known: create a review worktree off the base branch.
        wt = gitwt.start_worktree(repo_dir, branch=None, issue=None, slug="review", base=base_branch)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", head_ref)
    store.record_link(f"pr:{pr_url}", {"pr_url": pr_url, "worktree": worktree,
                                       "branch": branch, "repo": str(repo_dir)})
    try:
        repos.register_repo(repos.repo_name(repo_dir), repo_dir)
    except HarnessError:
        pass

    prompt = backend.prompt_for_review(pr_url)
    eprint(f"worktree: {worktree}  branch: {branch}")
    backend.launch(harness_name, prompt, worktree or str(repo_dir), no_tty, _HARNESS_ARGS)
    _print_result({"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
                   "harness": harness_name}, json_output)


# ── cleanup ───────────────────────────────────────────────────────────


@app.command("cleanup")
@_catch_harness_errors
def cleanup(
    ref: str = typer.Argument(..., help="Issue ID/URL or PR/MR URL."),
    force: bool = typer.Option(False, "--force", help="Skip state validation."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Close issue + remove worktree/branch/PR.

    Example:
      harness cleanup OWNER/REPO#22 --force --yes
      harness cleanup OWNER/REPO#22 --dry-run
    """
    parsed = refs.parse_ref(ref)
    key = refs.issue_key(parsed)
    links = store.load_links()
    entry = links.get(key) or links.get(f"pr:{parsed['url']}" if parsed["repo"] else key)
    if entry is None:
        _fail(
            f"no linked state for {ref}.\n"
            "  Pass the worktree repo explicitly or run start/review first.",
            EXIT_USAGE,
        )
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    if not branch:
        _fail(f"linked entry for {ref} has no branch", EXIT_USAGE)

    if dry_run:
        _print_result({"dry_run": True, "repo": str(repo), "branch": branch,
                       "pr_url": pr_url, "force": force}, json_output)
        return

    if not yes and not force and sys.stdin.isatty():
        try:
            answer = input(f"Remove worktree + delete branch '{branch}'? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    result = gitwt.cleanup_worktree(repo, branch, delete_branch=True,
                                    force=force, yes=True)
    _close_issue(parsed, force)
    _close_pr(parsed, pr_url, force)

    remaining = {k: v for k, v in store.load_links().items()
                 if k != key and k != f"pr:{parsed['url']}"}
    store.save_links(remaining)
    _print_result({"branch": branch, "cleanup": result, "closed": ref}, json_output)


def _close_issue(parsed: dict, force: bool) -> None:
    if parsed["tool"] == "jira-cli":
        # Best effort: comment the resolution; transitions vary per project,
        # so leave status change to the user unless --force.
        try:
            from .errors import run_cmd as _run
            _run("jira-cli", "issue", parsed["number"], "add-comment",
                 "--body", "Resolved via harness cleanup.")
            eprint(f"commented on {parsed['number']} (status left unchanged)")
        except HarnessError as e:
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "gh" and parsed["repo"] and parsed["kind"] in ("issue", "issue_or_pr"):
        try:
            from .errors import run_cmd as _run
            target = [parsed["number"], "--repo", parsed["repo"]] if parsed["kind"] == "issue_or_pr" \
                else [parsed["url"]]
            _run("gh", "issue", "close", *target)
            eprint(f"closed issue {parsed['url'] or parsed['number']}")
        except HarnessError as e:
            if not force:
                raise
            eprint(f"warning: {e}")
    elif parsed["tool"] == "glab":
        eprint("note: glab issue close not automated in v1; close it in the UI.")


def _close_pr(parsed: dict, pr_url: str, force: bool) -> None:
    url = pr_url or (parsed["url"] if parsed["kind"] in ("pr", "mr") else "")
    if not url:
        return
    try:
        from .errors import run_cmd as _run
        if "github.com" in url:
            _run("gh", "pr", "close", url)
        else:
            _run("glab", "mr", "close", url)
        eprint(f"closed {url}")
    except HarnessError as e:
        if not force:
            raise
        eprint(f"warning: {e}")


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
      harness repo add --name projectx --path ~/projects/projectx
      harness repo add --name projectx --path ~/projects/projectx --tracker IPG
    """
    p = path.expanduser()
    if not (p / ".git").exists() and not p.is_dir():
        _fail(f"not a repo path: {p}", EXIT_USAGE)
    repos.register_repo(name, p)
    if tracker:
        cfg = store.load_config()
        cfg.setdefault("trackers", {})[tracker] = {"repos": [name]}
        store.save_config(cfg)
    _print_result({"registered": name, "path": str(p)}, json_output)


@repo_app.command("list")
def repo_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List registered repos (Rich table by default).

    Example:
      harness repo list
      harness repo list --csv
      harness repo list --json | jq '.[].name'
    """
    cfg = store.load_config()
    items = [{"name": n, **v} for n, v in cfg.get("repos", {}).items()]
    _print_rows(items, json_output, csv_output, ["name", "path"],
                "Registered repos", "(no repos registered)")


@repo_app.command("remove")
@_catch_harness_errors
def repo_remove(
    name: str = typer.Argument(..., autocompletion=_complete_repos, help="Registered repo name."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Unregister a repo.

    Example: harness repo remove projectx
    """
    cfg = store.load_config()
    if name not in cfg.get("repos", {}):
        _fail(f"unknown repo: {name}", EXIT_USAGE)
    del cfg["repos"][name]
    store.save_config(cfg)
    _print_result({"removed": name}, json_output)


# ── status ────────────────────────────────────────────────────────────


@app.command("status")
@_catch_harness_errors
def status(
    ref: Optional[str] = typer.Argument(None, help="Issue/PR ref (omit: all links)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """Show linked issue↔PR↔worktree state (Rich table by default).

    Example:
      harness status
      harness status OWNER/REPO#22
      harness status --json
    """
    links = store.load_links()
    if ref:
        parsed = refs.parse_ref(ref)
        key = refs.issue_key(parsed)
        entry = links.get(key) or links.get(f"pr:{parsed['url']}", {})
        if json_output:
            print(json.dumps({"key": key, **entry}, indent=2))
            return
        if not entry:
            print(f"no linked state for {ref}")
            return
        for k, v in entry.items():
            print(f"{k}: {v}")
        return
    if json_output:
        print(json.dumps(links, indent=2))
        return
    items = [{"key": k, "worktree": v.get("worktree", "?"), "branch": v.get("branch", "?")}
             for k, v in links.items()]
    _print_rows(items, json_output, csv_output, ["key", "worktree", "branch"],
                "Linked sessions", "(no linked sessions)")


# ── doctor ────────────────────────────────────────────────────────────


@app.command("doctor")
def doctor(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Check install sync + tool availability.

    Example: harness doctor

    Exit codes: 0 in sync · 1 stale/missing receipt (fix: ./install.sh).
    """
    result = _doctor.check(json_output=json_output)
    if json_output:
        print(json.dumps(result, indent=2))
    if result["status"] != "ok":
        raise typer.Exit(EXIT_GENERAL)


# ── shell completion --------------------------------------------------

completions_app = typer.Typer(help="Shell completion: print the init script or install it into your rc file.", no_args_is_help=True)
app.add_typer(completions_app, name="completions")


@completions_app.command("show")
def completions_show(
    shell: str = typer.Argument(..., help="Shell to print the init script for (bash, zsh, fish)."),
) -> None:
    """Print the shell init script — source it via eval in your rc file.

    Example:
      eval "$(harness completions show bash)"   # ~/.bashrc
      eval "$(harness completions show zsh)"    # ~/.zshrc
      harness completions show fish | source    # fish config
    """
    import typer.main as _typer_main

    try:
        script = _completions.get_completion_script("harness", shell, click_cmd=_typer_main.get_command(app))
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    print(script, end="" if script.endswith("\n") else "\n")


@completions_app.command("install")
def completions_install(
    shell: Optional[str] = typer.Argument(None, help="Shell to install for (bash, zsh, fish). Omit: detect from $SHELL."),
    rcfile: Optional[str] = typer.Option(None, "--rcfile", help="Rc file to edit (default: ~/.bashrc, ~/.zshrc, fish config)."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation (needed for non-interactive/agent use)."),
) -> None:
    """Install the eval line into your rc file (idempotent; keeps a .bak backup).

    Example:
      harness completions install          # detect shell from $SHELL
      harness completions install bash     # explicit shell
      harness completions install zsh --rcfile ~/.zshrc --yes
    """
    resolved = shell or _completions.detect_shell()
    if resolved is None:
        _fail(f"cannot detect shell from $SHELL={os.environ.get('SHELL', '')!r}; pass bash, zsh, or fish explicitly", EXIT_USAGE)
    if not yes and sys.stdin.isatty() and not typer.confirm(f"Add harness completion to your {resolved} rc file?"):
        raise typer.Exit(EXIT_USAGE)
    try:
        rc, changed = _completions.install_completion("harness", resolved, Path(rcfile) if rcfile else None)
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    if changed:
        _completions.print_install_hint("harness", resolved, rc)
    else:
        print(f"already installed in {rc}", file=sys.stderr)


def main() -> None:
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
