"""harness CLI — launch AI agent harnesses in git-wt worktrees from issue/PR links.

stdout carries ONLY command output; every log/progress/confirmation line
goes to stderr.
"""

from __future__ import annotations

import csv
import functools
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Callable, Optional

import typer

from . import __version__, backend, gitwt, refs, repos, store, trackers
from . import sync as sync_mod
from . import completions as _completions
from . import doctor as _doctor
from .errors import HarnessError

EXIT_OK, EXIT_GENERAL, EXIT_USAGE = 0, 1, 2

# Extra harness args collected in main() by splitting argv on the first
# bare `--` (argparse.REMAINDER-style flags would swallow --repo etc.).
_HARNESS_ARGS: list[str] = []

# --base values treated as "create a new branch off it": the repo's detected
# default or one of these common defaults. Any other --base names an existing
# branch to run on directly (no new branch).
_DEFAULT_BRANCH_NAMES = {"main", "master", "develop"}

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
_complete_branches = _completions.complete_names(_completions._base_branch_candidates)
_complete_refs = _completions.complete_names(_completions._ref_candidates)


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


def _run_harness(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                 no_tty: bool, no_harness: bool, result: dict, json_output: bool) -> None:
    """Launch the harness in the worktree; with --no-harness print the exact
    command instead and hand the worktree to the user (shell exec on TTY)."""
    harness_cmd = " ".join(shlex.quote(a) for a in
                           backend.command_argv(harness_name, prompt, no_tty, _HARNESS_ARGS))
    if no_harness:
        eprint(f"harness command: {harness_cmd}")
        result["harness_command"] = harness_cmd
        _print_result(result, json_output)
        if sys.stdin.isatty():
            # "cd" for the user: replace this process with their shell in the
            # worktree; the printed harness command is theirs to run.
            backend.cd_worktree(worktree or fallback_dir)
            shell = os.environ.get("SHELL") or "/bin/sh"
            os.execvp(shell, [shell])
        return
    backend.launch(harness_name, prompt, worktree or fallback_dir, no_tty, _HARNESS_ARGS)
    _print_result(result, json_output)


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
    base: Optional[str] = typer.Option(None, "--base", autocompletion=_complete_branches, help="Base branch (default: repo default). A non-default branch runs on that branch instead of creating a new one."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively (auto commit/push/MR prompt suffix)."),
    no_harness: bool = typer.Option(False, "-N", "--no-harness", help="Skip launching the harness: print the harness command and land in an interactive shell inside the worktree."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Create worktree from issue and launch harness.

    Example:
      harness start https://github.com/OWNER/REPO/issues/22
      harness start OWNER/REPO#22 --repo my-checkout --no-tty
      harness start OWNER/REPO#22 --dry-run
      harness start OWNER/REPO#22 --base feat/22--add-login
      harness start OWNER/REPO#22 --no-harness
    """
    parsed = refs.parse_ref(ref)
    if parsed["kind"] in ("pr", "mr"):
        _fail(f"{ref} looks like a PR/MR — use `harness review`", EXIT_USAGE)
    r, outcome = trackers.resolve_for_tracker(
        trackers.tracker_id(parsed), repo, Path.cwd(), depth=depth,
        yes=yes, persist=not dry_run)
    tid = trackers.tracker_id(parsed)
    if outcome == "recorded" and tid and not dry_run:
        eprint(f"note: linked tracker {tid} to repo {r}")
    detected_default = repos.default_branch(r)
    # --base naming a non-default branch: run on that existing branch (worktree
    # checked out at it, upstream origin/<branch>); no new branch is created.
    branch_mode = base is not None and base != detected_default and base not in _DEFAULT_BRANCH_NAMES
    base_branch = base or detected_default
    cwd_root = repos.repo_root(Path.cwd())
    cur = repos.current_branch(r) if cwd_root == r else None
    # Running from inside a linked worktree on a non-protected branch
    # continues on that branch: the worktree exists, no new branch is created.
    cwd_branch = repos.worktree_branch(Path.cwd()) if cwd_root is not None else None
    cwd_mode = (cwd_branch is not None
                and cwd_branch not in _DEFAULT_BRANCH_NAMES
                and cwd_branch != detected_default)
    if cur and cur != base_branch and not yes and not dry_run and sys.stdin.isatty() and not cwd_mode:
        if branch_mode:
            eprint(f"note: repo is on '{cur}', worktree will check out existing branch '{base}'.")
        else:
            eprint(f"note: repo is on '{cur}', worktree will branch from '{base_branch}'.")
    elif cwd_mode and not yes and not dry_run and sys.stdin.isatty():
        eprint(f"note: continuing on existing branch '{cwd_branch}' (current worktree); no new branch created.")

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
        result = {"dry_run": True, "repo": str(r),
                  "base": detected_default if branch_mode else base_branch,
                  "issue": {"title": issue["title"]}, "harness": harness_name,
                  "key": key, "tracker": tid, "tracker_link": outcome,
                  "link": None if (branch_mode or cwd_mode) else link_url}
        if branch_mode:
            result["branch"] = base
        elif cwd_mode:
            result["branch"] = cwd_branch
        _print_result(result, json_output)
        return
    if branch_mode:
        wt = gitwt.start_worktree(r, branch=base, base=detected_default)
    elif cwd_mode:
        wt = gitwt.start_worktree(r, branch=cwd_branch, base=detected_default)
    else:
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
    prompt = backend.prompt_for_issue(issue["title"], issue["body"], ref,
                                      worktree=worktree, branch=branch)
    if no_tty:
        prompt += "\n\nAfter task done, commit, push and create an MR/PR to the default branch"
    result = {"worktree_path": worktree, "branch": branch,
              "base": detected_default if branch_mode else base_branch,
              "key": key, "harness": harness_name}
    eprint(f"worktree: {worktree}  branch: {branch}")
    _run_harness(harness_name, prompt, worktree, str(r), no_tty, no_harness,
                 result, json_output)


# ── review ────────────────────────────────────────────────────────────


@app.command("review")
@_catch_harness_errors
def review(
    ref: str = typer.Argument(..., autocompletion=_complete_refs, help="PR/MR URL, OWNER/REPO#NUM, or session ref (key/branch/worktree)."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively."),
    no_harness: bool = typer.Option(False, "-N", "--no-harness", help="Skip launching the harness: print the harness command and land in an interactive shell inside the worktree."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Create worktree from PR/MR and launch review.

    Example:
      harness review https://github.com/OWNER/REPO/pull/33
      harness review OWNER/REPO#33 --no-tty
      harness review OWNER/REPO#33 --no-harness
    """
    parsed = refs.parse_ref(ref)
    if parsed["kind"] not in ("pr", "mr"):
        # Session ref: resolve to the recorded (or discovered) PR/MR.
        links = store.load_links()
        resolved = _resolve_session_key(ref, links)
        if resolved is not None:
            key = _pick_session_key(ref, resolved)
            entry = links.get(key, {})
            pr_url0 = _session_pr_url(key, entry) if entry else None
            if not pr_url0:
                _fail(f"session {key} has no recorded PR/MR.\n"
                      "  Pass a PR/MR URL, or create one first.", EXIT_USAGE)
            eprint(f"note: session {key} → {pr_url0}")
            ref = pr_url0
            parsed = refs.parse_ref(ref)
    tid = trackers.tracker_id(parsed)
    repo_dir, outcome = trackers.resolve_for_tracker(
        tid, repo, Path.cwd(), depth=depth, yes=yes, persist=not dry_run)
    if outcome == "recorded" and tid and not dry_run:
        eprint(f"note: linked tracker {tid} to repo {repo_dir}")
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
                       "harness": harness_name, "head_ref": head_ref,
                       "tracker": tid, "tracker_link": outcome}, json_output)
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

    prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)
    result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
              "harness": harness_name}
    eprint(f"worktree: {worktree}  branch: {branch}")
    _run_harness(harness_name, prompt, worktree, str(repo_dir), no_tty, no_harness,
                 result, json_output)


# ── cleanup ───────────────────────────────────────────────────────────


@app.command("cleanup")
@_catch_harness_errors
def cleanup(
    ref: str = typer.Argument(..., autocompletion=_complete_refs, help="Issue ID/URL, PR/MR URL, or session ref (key/branch/worktree)."),
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
    links = store.load_links()
    resolved = _resolve_session_key(ref, links)
    if resolved is None:
        _fail(
            f"no linked state for {ref}.\n"
            "  Run `harness link list` to see linked sessions.",
            EXIT_USAGE,
        )
    key = _pick_session_key(ref, resolved)
    entry = links.get(key, {})
    if entry is None or not entry:
        _fail(f"no linked state for {ref}.", EXIT_USAGE)
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    stored_ref = entry.get("issue", "") or entry.get("pr_url", "") or ref
    try:
        parsed = refs.parse_ref(key if not key.startswith("pr:") else stored_ref)
    except HarnessError:
        try:
            parsed = refs.parse_ref(stored_ref)
        except HarnessError:
            parsed = {"kind": "", "tool": "", "repo": "", "number": "", "url": pr_url or stored_ref}
    if key != ref and not yes and not dry_run and sys.stdin.isatty():
        try:
            answer = input(f"ref {ref!r} matches session {key!r} — use it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)
    if not branch:
        _fail(f"linked entry for {ref} has no branch", EXIT_USAGE)

    if dry_run:
        _print_result({"dry_run": True, "key": key, "repo": str(repo), "branch": branch,
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

    remaining = {k: v for k, v in store.load_links().items() if k != key}
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


def _resolve_session_key(ref: str, links: dict) -> str | list[str] | None:
    """Resolve a cleanup ref to a session key.

    Exact session key first, then parseable issue/PR refs, then substring
    search over keys, worktrees, and branches. Returns the key, a list of
    keys on ambiguity, or None.
    """
    if ref in links:
        return ref
    try:
        parsed = refs.parse_ref(ref)
    except HarnessError:
        parsed = None
    needle = ref
    exact: str | None = None
    if parsed is not None:
        key = refs.issue_key(parsed)
        pr_key = f"pr:{parsed['url']}" if parsed.get("repo") else key
        if key in links:
            exact = key
        elif pr_key in links:
            exact = pr_key
        needle = parsed.get("number", "") or ref
    matches = [k for k, v in links.items()
               if needle in k
               or needle in (v.get("worktree", "") or "")
               or needle in (v.get("branch", "") or "")]
    if not matches:
        bare = needle.split("/")[-1].split("--")[0].strip()
        if bare and bare != needle:
            matches = [k for k, v in links.items()
                       if bare in k
                       or bare in (v.get("worktree", "") or "")
                       or bare in (v.get("branch", "") or "")]
    if exact is not None and exact not in matches:
        matches = [exact, *matches]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches


def _pick_session_key(ref: str, resolved: str | list[str]) -> str:
    """Disambiguate multiple fuzzy matches interactively."""
    if isinstance(resolved, str):
        return resolved
    if not sys.stdin.isatty():
        _fail(f"ambiguous ref {ref!r} matches: {', '.join(resolved)}.\n"
              "  Re-run with the exact session key.", EXIT_USAGE)
    eprint(f"multiple sessions match {ref!r}:")
    for i, k in enumerate(resolved, 1):
        eprint(f"  {i}. {k}")
    try:
        choice = input("select session [number]: ").strip()
    except EOFError:
        choice = ""
    if choice.isdigit() and 1 <= int(choice) <= len(resolved):
        return resolved[int(choice) - 1]
    _fail("aborted", EXIT_USAGE)


def _session_pr_url(key: str, entry: dict) -> str | None:
    """Recorded PR/MR URL for a session; falls back to a live branch query."""
    if entry.get("pr_url"):
        return entry["pr_url"]
    repo = entry.get("repo", "")
    branch = entry.get("branch", "")
    if not (repo and branch and Path(repo).exists()):
        return None
    tool = repos._detect_host_cli(Path(repo))
    if not tool:
        return None
    try:
        pr = refs.latest_pr(refs.fetch_pr_list_for_branch(tool, branch, cwd=repo))
    except HarnessError:
        return None
    if pr:
        store.record_link(key, {"pr_url": pr["url"]})
        return pr["url"]
    return None


def _close_pr(parsed: dict, pr_url: str, force: bool) -> None:
    url = pr_url or (parsed.get("url", "") if parsed.get("kind") in ("pr", "mr") else "")
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
        if "already closed" in str(e).lower() or "already been closed" in str(e).lower() or "404" in str(e) or "not found" in str(e).lower():
            eprint(f"note: {url} already closed; continuing with local cleanup.")
            return
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
        tid = trackers.normalize_id(tracker)
        cfg = store.load_config()
        entry = cfg.setdefault("trackers", {}).setdefault(tid, {"repos": []})
        norm = str(p.expanduser().resolve())
        if norm not in [str(Path(r).expanduser().resolve()) for r in entry.get("repos", [])]:
            entry.setdefault("repos", []).append(norm)
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

# ── link ──────────────────────────────────────────────────────────────

link_app = typer.Typer(help="View and manage tracker↔repo relations and session links.", no_args_is_help=True)
app.add_typer(link_app, name="link")


@link_app.command("list")
def link_list(
    worktree: bool = typer.Option(False, "--worktree", help="Show the worktree column in session links."),
    refresh_pr: bool = typer.Option(False, "--refresh-pr", help="Re-query PR status instead of using the cache."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List tracker↔repo relations and session links.

    Example:
      harness link list
      harness link list --json
    """
    cfg = store.load_config()
    links = store.load_links()
    if json_output:
        print(json.dumps({"trackers": cfg.get("trackers", {}),
                          "sessions": {k: _enrich_entry(v, refresh_pr)
                                       for k, v in links.items()}},
                         indent=2, ensure_ascii=False))
        return
    rows = [{"tracker": t, "repos": ", ".join(v.get("repos", []))}
            for t, v in cfg.get("trackers", {}).items()]
    _print_rows(rows, json_output, csv_output, ["tracker", "repos"],
                "Tracker links", "(no tracker links)")
    srows, scolumns, scaptions = _session_rows(links, worktree, refresh_pr)
    _print_rows(srows, json_output, csv_output, scolumns,
                "Session links", "(no session links)",
                caption="\n".join(scaptions) or None, colorize=_colorize_session)


@link_app.command("set")
@_catch_harness_errors
def link_set(
    tracker: str = typer.Argument(..., help="Tracker id (e.g. jira:IPG, github:OWNER/REPO) or bare Jira prefix / OWNER/REPO."),
    repo: str = typer.Argument(..., help="Repo path or registered name to link."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Link a tracker to a repo (persists the relation).

    Example:
      harness link set jira:IPG ~/projects/projectx
      harness link set github:OWNER/REPO my-checkout
    """
    tid = trackers.normalize_id(tracker)
    target = repos.resolve_repo(repo, Path.cwd())
    cfg = store.load_config()
    entry = cfg.setdefault("trackers", {}).setdefault(tid, {"repos": []})
    norm = str(target.expanduser().resolve())
    if norm not in [str(Path(r).expanduser().resolve()) for r in entry.get("repos", [])]:
        entry.setdefault("repos", []).append(norm)
    store.save_config(cfg)
    _print_result({"tracker": tid, "repos": entry["repos"]}, json_output)


@link_app.command("remove")
@_catch_harness_errors
def link_remove(
    ref: str = typer.Argument(..., help="Tracker id or session key (issue/PR ref, branch, or worktree path)."),
    repo: Optional[str] = typer.Option(None, "--repo", help="Only remove this repo from the tracker mapping."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Remove a tracker mapping or a session link.

    Example:
      harness link remove jira:IPG
      harness link remove jira:IPG --repo ~/projects/other
      harness link remove o/r#22
    """
    tid = trackers.normalize_id(ref)
    cfg = store.load_config()
    if tid in cfg.get("trackers", {}):
        if repo:
            target = str(repos.resolve_repo(repo, Path.cwd()).expanduser().resolve())
            kept = [r for r in cfg["trackers"][tid].get("repos", [])
                    if str(Path(r).expanduser().resolve()) != target]
            if len(kept) == len(cfg["trackers"][tid].get("repos", [])):
                _fail(f"repo {repo} not linked to tracker {tid}", EXIT_USAGE)
            if kept:
                cfg["trackers"][tid]["repos"] = kept
            else:
                del cfg["trackers"][tid]
        else:
            del cfg["trackers"][tid]
        store.save_config(cfg)
        _print_result({"removed": tid}, json_output)
        return
    resolved = _resolve_session_key(ref, store.load_links())
    if resolved is None:
        _fail(f"no tracker mapping or session link for {ref}", EXIT_USAGE)
    links = store.load_links()
    del links[resolved]
    store.save_links(links)
    _print_result({"removed": resolved}, json_output)


_PR_STYLES = {"open": "green", "merged": "magenta", "closed": "red"}
_DB_CACHE: dict[str, str] = {}
_TOOL_CACHE: dict[str, str | None] = {}


def _repo_default_branch(repo: str) -> str | None:
    if repo not in _DB_CACHE:
        try:
            _DB_CACHE[repo] = repos.default_branch(Path(repo))
        except HarnessError:
            _DB_CACHE[repo] = ""
    return _DB_CACHE[repo] or None


def _repo_tool(repo: str) -> str | None:
    if repo not in _TOOL_CACHE:
        _TOOL_CACHE[repo] = (repos._detect_host_cli(Path(repo))
                             if Path(repo).exists() else None)
    return _TOOL_CACHE[repo]


def _fmt_pr(pr: dict | None) -> str:
    if not pr:
        return "-"
    return f"PR #{pr['number']} ({pr['state']})"


def _fetch_pr_via(tool: str, branch: str, repo: str) -> dict | None:
    try:
        return refs.latest_pr(refs.fetch_pr_list_for_branch(tool, branch, cwd=repo))
    except HarnessError as e:
        eprint(f"warning: {tool} pr lookup failed for {branch}: {e}")
        return None


def _pr_cell(entry: dict, refresh: bool = False) -> tuple[str, dict | None]:
    """Latest PR for the session branch: (display, raw) — cache by default."""
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cached = store.load_pr_cache().get(branch)
    if not refresh and cached is not None:
        pr = cached["pr"]
        return _fmt_pr(pr), pr
    tool = _repo_tool(repo)
    if not tool or not branch:
        return "-", None
    pr = _fetch_pr_via(tool, branch, repo)
    used = tool
    if pr is None:
        used = "glab" if tool == "gh" else "gh"
        pr = _fetch_pr_via(used, branch, repo)
    store.cache_pr_status(branch, pr, tool=used if pr else None)
    return _fmt_pr(pr), pr


def _commits_cell(entry: dict) -> tuple[str, dict | None]:
    """'behind|ahead' vs the repo's remote-tracking default branch."""
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    if wt and not Path(wt).exists():
        return "gone", None
    db = _repo_default_branch(repo)
    if not (db and branch and wt and Path(wt).exists()):
        return "-", None
    ab = repos.ahead_behind(Path(wt), db)
    if not ab:
        return "-", None
    if not ab["behind"] and not ab["ahead"]:
        return "0|0", ab
    return f"{ab['behind']}|{ab['ahead']}", ab


def _caption_for(key: str, ab: dict) -> str:
    parts = []
    for side in ("behind", "ahead"):
        if ab[side]:
            hashes = ab[f"{side}_hashes"][:4]
            more = "…" if len(ab[f"{side}_hashes"]) > 4 else ""
            parts.append(f"{side}: {' '.join(hashes)}{more}")
    return f"{key} " + "  ·  ".join(parts)


def _session_rows(links: dict, show_worktree: bool, refresh: bool
                  ) -> tuple[list[dict], list[str], list[str]]:
    rows, captions = [], []
    for k, v in links.items():
        commits, ab = _commits_cell(v)
        pr, pr_data = _pr_cell(v, refresh)
        row = {"key": k, "branch": v.get("branch", "?"), "commits": commits,
               "pr": pr, "_pr_state": (pr_data or {}).get("state", "")}
        if show_worktree:
            row["worktree"] = v.get("worktree", "?")
        rows.append(row)
        if ab and (ab["behind"] or ab["ahead"]):
            captions.append(_caption_for(k, ab))
    columns = (["key", "worktree", "branch", "commits", "pr"] if show_worktree
               else ["key", "branch", "commits", "pr"])
    return rows, columns, captions


def _colorize_session(row: dict) -> dict:
    out = dict(row)
    st = row.get("_pr_state", "")
    if st in _PR_STYLES:
        c = _PR_STYLES[st]
        out["pr"] = f"[{c}]{row['pr']}[/{c}]"
    return out


def _enrich_entry(entry: dict, refresh: bool) -> dict:
    commits, ab = _commits_cell(entry)
    pr, pr_data = _pr_cell(entry, refresh)
    return {**entry, "commits": commits, "pr": pr,
            "commits_detail": ab, "pr_detail": pr_data}


def _session_detail(key: str, entry: dict, refresh: bool) -> dict:
    commits, ab = _commits_cell(entry)
    pr, pr_data = _pr_cell(entry, refresh)
    return {"key": key, **entry, "commits": commits, "pr": _fmt_pr(pr_data),
            "commits_detail": ab, "pr_detail": pr_data}


def _print_detail(detail: dict) -> None:
    from rich.console import Console
    c = Console()
    c.print(f"[bold]{detail['key']}[/bold]")
    for k in ("worktree", "branch", "repo", "issue"):
        if detail.get(k):
            c.print(f"  {k}: {detail[k]}")
    pd = detail.get("pr_detail")
    if pd:
        style = _PR_STYLES.get(pd.get("state", ""), "white")
        c.print(f"  PR: [#{style}]#{pd['number']} {pd['title']} ({pd['state']})"
                f"[/{style}] by {pd.get('author', '?')}")
        c.print(f"  url: {pd.get('url', '')}")
    cd = detail.get("commits_detail")
    if cd:
        c.print(f"  commits: {cd['behind']} behind / {cd['ahead']} ahead"
                f" of the default branch")
        if cd["behind_hashes"]:
            c.print(f"    behind: {' '.join(cd['behind_hashes'])}")
        if cd["ahead_hashes"]:
            c.print(f"    ahead: {' '.join(cd['ahead_hashes'])}")


# ── status ────────────────────────────────────────────────────────────


@app.command("status")
@_catch_harness_errors
def status(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="Issue/PR ref or session key (omit: all links)."),
    worktree: bool = typer.Option(False, "--worktree", help="Show the worktree column."),
    refresh_pr: bool = typer.Option(False, "--refresh-pr", help="Re-query PR status instead of using the cache."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """Show linked issue↔PR↔worktree state (Rich table by default).

    Columns: behind|ahead vs the repo's remote-tracking default branch and
    the latest PR/MR for the branch (cached; --refresh-pr re-queries).

    Example:
      harness status
      harness status IPG-929
      harness status OWNER/REPO#22
      harness status --json
    """
    links = store.load_links()
    if ref:
        resolved = _resolve_session_key(ref, links)
        if resolved is not None:
            key = _pick_session_key(ref, resolved)
            entry = links[key]
        else:
            try:
                parsed = refs.parse_ref(ref)
            except HarnessError:
                _fail(f"no linked state for {ref}", EXIT_USAGE)
            key = refs.issue_key(parsed)
            entry = links.get(key) or links.get(f"pr:{parsed['url']}", {})
            if not entry:
                _fail(f"no linked state for {ref}", EXIT_USAGE)
        detail = _session_detail(key, entry, refresh=refresh_pr)
        if json_output:
            print(json.dumps(detail, indent=2, ensure_ascii=False))
            return
        _print_detail(detail)
        return
    rows, columns, captions = _session_rows(links, worktree, refresh_pr)
    if json_output:
        print(json.dumps({k: _enrich_entry(v, refresh_pr)
                          for k, v in links.items()}, indent=2, ensure_ascii=False))
        return
    _print_rows(rows, json_output, csv_output, columns,
                "Linked sessions", "(no linked sessions)",
                caption="\n".join(captions) or None, colorize=_colorize_session)


# ── sync ──────────────────────────────────────────────────────────────


@app.command("sync")
@_catch_harness_errors
def sync_cmd(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs,
                                        help="Issue/PR ref or session key (omit: interactive pick, or --all)."),
    merge: bool = typer.Option(False, "-m", "--merge", help="Merge locally in the worktree instead of the default remote rebase."),
    push: bool = typer.Option(False, "--push", help="With --merge, push the updated session branch."),
    harness: bool = typer.Option(False, "--harness", help="On unresolvable conflicts, launch the coding harness in the worktree."),
    all_sessions: bool = typer.Option(False, "--all", help="Sync every linked session (confirmed one by one)."),
    yes: bool = typer.Option(False, "--yes", "--force", "-y", help="Skip confirmations."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without touching anything."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Bring a session branch up to date with its base branch.

    Default: remote rebase via `gh pr update-branch --rebase` /
    `glab mr rebase` (host merges server-side). With -m/--merge: fetch,
    fast-forward the local default branch and merge it into the session
    branch in the worktree; push only with --push.

    Example:
      harness sync IPG-929
      harness sync IPG-929 --merge --push
      harness sync --all --dry-run
    """
    links = store.load_links()
    if ref:
        resolved = _resolve_session_key(ref, links)
        if resolved is None:
            _fail(f"no linked state for {ref}", EXIT_USAGE)
        key = _pick_session_key(ref, resolved)
        keys = [key]
    elif all_sessions:
        keys = list(links)
    else:
        if not links:
            _fail("no linked sessions", EXIT_USAGE)
        if not sys.stdin.isatty():
            _fail("no ref given and stdin is not a TTY — pass a ref or --all",
                  EXIT_USAGE)
        keys = [_pick_session_key("sync", list(links))]
    results = []
    for k in keys:
        entry = links[k]
        eprint(f"syncing {k} …")
        if not yes and not dry_run and sys.stdin.isatty():
            if not typer.confirm(f"sync {k} ({entry.get('branch', '?')})?"):
                _fail("aborted", EXIT_USAGE)
        results.append(_sync_one(k, entry, merge=merge, do_push=push,
                                 use_harness=harness, yes=yes,
                                 dry_run=dry_run, json_output=json_output))
    if json_output:
        out = results[0] if len(results) == 1 and ref else results
        print(json.dumps(out, indent=2, ensure_ascii=False))


def _sync_one(key: str, entry: dict, merge: bool, do_push: bool, use_harness: bool,
              yes: bool, dry_run: bool, json_output: bool) -> dict:
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    result: dict = {"key": key, "branch": branch, "strategy": None, "result": None}
    if not wt or not Path(wt).exists():
        result["result"] = "missing-worktree"
        eprint(f"{key}: worktree missing — skipped")
        return result
    dirty = sync_mod.dirty_files(Path(wt))
    if dirty:
        _fail(f"{key}: worktree has uncommitted changes: {', '.join(dirty)}",
              EXIT_GENERAL)
    db = _repo_default_branch(repo)
    _, pr_data = _pr_cell(entry, refresh=False)
    pr = pr_data
    tool = store.get_cached_pr_tool(branch) or _repo_tool(repo)
    if merge or not pr:
        if not db:
            _fail(f"{key}: cannot determine default branch for {repo}", EXIT_GENERAL)
        result["strategy"] = "local-merge"
        if dry_run:
            result["result"] = "would-merge"
            eprint(f"{key}: would merge origin/{db} into {branch} in {wt}")
            return result
        out = sync_mod.local_merge(Path(wt), db)
        if out["status"] == "conflict":
            handled = sync_mod.auto_resolve_changelog(Path(wt), out["conflicts"])
            if not handled:
                eprint(f"{key}: conflicts in: {', '.join(out['conflicts'])}")
                if use_harness:
                    result["result"] = "conflict-harness"
                    prompt = (f"The branch {branch} has merge conflicts with "
                              f"{db} in files: {', '.join(out['conflicts'])}. "
                              "Resolve them, complete the merge, and stop.")
                    _run_harness("omp", prompt, wt, wt, False, False, result, json_output)
                    return result
                if sys.stdin.isatty():
                    if typer.confirm("launch the harness to resolve?"):
                        result["result"] = "conflict-harness"
                        prompt = (f"The branch {branch} has merge conflicts with "
                                  f"{db} in files: {', '.join(out['conflicts'])}. "
                                  "Resolve them, complete the merge, and stop.")
                        _run_harness("omp", prompt, wt, wt, False, False, result, json_output)
                        return result
                    _fail("aborted (merge left in progress; abort with `git merge --abort`)",
                          EXIT_USAGE)
                _fail("conflicts need human resolution — re-run with --harness "
                      "to launch the coding harness", EXIT_USAGE)
            result["result"] = "merged"
        elif out["status"] == "up-to-date":
            result["result"] = "up-to-date"
            eprint(f"{key}: already up to date")
        else:
            result["result"] = "merged"
            eprint(f"{key}: merged {db} into {branch}")
        if do_push and result["result"] == "merged":
            sync_mod.push(Path(wt), branch)
            eprint(f"{key}: pushed {branch}")
            result["pushed"] = True
        return result
    # Remote rebase (default): host rebases the branch on its base.
    result["strategy"] = "remote-rebase"
    if dry_run:
        result["result"] = f"would-rebase-via-{tool}"
        eprint(f"{key}: would run {tool} rebase for PR #{pr_data['number']} ({pr_data['url']})")
        return result
    sync_mod.rebase_remote(tool, pr_data, Path(wt))
    result["result"] = "rebased"
    eprint(f"{key}: rebased PR #{pr_data['number']} via {tool}")
    return result


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
    _completions.ensure_completion_classes()  # typer 0.27: runtime server needs registered classes
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
