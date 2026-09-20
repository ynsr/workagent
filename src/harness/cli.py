"""harness CLI — launch AI agent harnesses in git-wt worktrees from issue/PR links.

stdout carries ONLY command output; every log/progress/confirmation line
goes to stderr.
"""

from __future__ import annotations
import csv
import functools
import json
import os
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import typer

from . import __version__, backend, gitwt, pick, refs, repos, store, trackers
from . import completions as _completions
from . import doctor as _doctor
from . import sync as sync_mod
from .errors import HarnessError, run_cmd

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
    link_url: str | None = None
    if parsed["tool"] == "jira-cli":
        if parsed["url"].startswith("http"):
            link_url = parsed["url"]
        else:
            site = refs.jira_site()
            if site:
                link_url = f"{site}/browse/{parsed['number']}"
    elif parsed["repo"]:
        link_url = parsed["url"]
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
    rec = {"issue": ref, "worktree": worktree, "branch": branch, "repo": str(r)}
    if link_url and link_url.startswith("http"):
        rec["issue_url"] = link_url
    store.record_link(key, rec)
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
            key = _pick_session_key(ref, resolved, links)
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
            info = refs.fetch_pr_info(parsed, cwd=str(repo_dir))
        except HarnessError as e:
            eprint(f"warning: {e}")
    head_ref = info.get("head_ref", "")

    if dry_run:
        _print_result({"dry_run": True, "repo": str(repo_dir), "pr_url": pr_url,
                       "harness": harness_name, "head_ref": head_ref,
                       "tracker": tid, "tracker_link": outcome}, json_output)
        return

    if not head_ref:
        _fail(f"could not determine the MR head branch for {pr_url}.\n"
              f"  Run `git fetch origin` in {repo_dir} and check `glab`/`gh` auth for that host.",
              EXIT_USAGE)
    wt = gitwt.start_worktree(repo_dir, branch=head_ref, base=base_branch)
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
    key = _pick_session_key(ref, resolved, links)
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


def _pick_session_key(ref: str, resolved: str | list[str],
                      links: dict | None = None) -> str:
    """Disambiguate multiple fuzzy matches interactively."""
    if isinstance(resolved, str):
        return resolved
    if not sys.stdin.isatty():
        _fail(f"ambiguous ref {ref!r} matches: {', '.join(resolved)}.\n"
              "  Re-run with the exact session key.", EXIT_USAGE)
    options = [(k, (links or {}).get(k, {}).get("branch", "")) for k in resolved]
    idx = pick.pick(f"multiple sessions match {ref!r}:", options)
    if idx is None:
        _fail("aborted", EXIT_USAGE)
    return resolved[idx]


def _session_pr_url(key: str, entry: dict) -> str | None:
    """Recorded PR/MR URL for a session; falls back to a live branch query."""
    if entry.get("pr_url"):
        return entry["pr_url"]
    repo = entry.get("repo", "")
    branch = entry.get("branch", "")
    if not (repo and branch and Path(repo).exists()):
        return None
    tool = _repo_tool(repo)
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
    srows, scolumns = _session_rows(links, worktree, refresh_pr)
    _print_rows(srows, json_output, csv_output, scolumns,
                "Session links", "(no session links)",
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


_PR_STYLES = {"open": "bright_green", "merged": "bright_magenta", "closed": "bright_red"}
_DB_CACHE: dict[str, str] = {}
_STATUS_TTL_SECONDS = 3 * 3600


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
    cfg = store.load_config()
    entry = next((e for e in cfg.get("repos", {}).values()
                  if e.get("path") == repo), None)
    remote = repos.remote_url(Path(repo))
    if (entry and entry.get("tool") in ("gh", "glab")
            and entry.get("remote") == remote):
        return entry["tool"]
    tool = (repos._detect_host_cli(Path(repo))
            if Path(repo).exists() else None)
    if entry is not None and (tool != entry.get("tool")
                              or remote != entry.get("remote")):
        entry["tool"] = tool
        entry["remote"] = remote
        store.save_config(cfg)
    return tool


def _fmt_pr(pr: dict | None) -> str:
    if not pr:
        return "-"
    return f"PR #{pr['number']} ({pr['state']})"


def _fmt_counts(ab: dict | None) -> str:
    if not ab:
        return "-"
    return f"{ab['behind']}|{ab['ahead']}"


def _git_tip(wt: str, ref: str) -> str | None:
    try:
        return run_cmd("git", "-C", wt, "rev-parse", "--verify", "-q", ref) or None
    except HarnessError:
        return None


def _cache_fresh(cached: dict) -> bool:
    ts = cached.get("checked_at", "")
    if not ts:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return False
    return age < timedelta(seconds=_STATUS_TTL_SECONDS)


def _query_pr(repo: str, branch: str) -> tuple[dict | None, str | None]:
    """(pr, tool) via the detected host CLI, falling back to the other one.

    Returns (None, None) when no host CLI is available; raises HarnessError
    when a CLI exists but every lookup fails (caller keeps cached data).
    """
    tool = _repo_tool(repo)
    if not tool or not branch:
        return None, None
    try:
        return refs.latest_pr(refs.fetch_pr_list_for_branch(tool, branch, cwd=repo)), tool
    except HarnessError as e:
        eprint(f"warning: {tool} pr lookup failed for {branch}: {e}")
        other = "glab" if tool == "gh" else "gh"
        try:
            return refs.latest_pr(refs.fetch_pr_list_for_branch(other, branch, cwd=repo)), other
        except HarnessError as e2:
            eprint(f"warning: {other} pr lookup failed for {branch}: {e2}")
            raise


def _status_cells(entry: dict, refresh_pr: bool = False) -> dict:
    """Commits + PR cells for one session, backed by pr_cache.json.

    A cache entry (keyed by branch) is reused while the session-branch tip
    and the remote-tracking base tip are unchanged, the entry is younger
    than 3h, and --refresh-pr is not given. Valid cache: two `git
    rev-parse` calls, no host-CLI spawn, no API call. --refresh-pr
    re-queries only the PR (counts still reuse when tips are unchanged).
    """
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cells = {"commits": "-", "ab": None, "pr": "-", "pr_data": None,
             "base_branch": None}
    cached = store.load_pr_cache().get(branch) if branch else None
    wt_ok = bool(wt) and Path(wt).exists()
    if wt and not wt_ok:
        cells["commits"] = "gone"
    if wt_ok and branch and cached and _cache_fresh(cached):
        base_branch = cached.get("base_branch") or ""
        branch_tip = _git_tip(wt, "HEAD")
        base_tip = _git_tip(wt, f"origin/{base_branch}") if base_branch else None
        if (cached.get("branch_tip") == branch_tip
                and cached.get("base_tip") == base_tip):
            ab = {"behind": int(cached.get("behind") or 0),
                  "ahead": int(cached.get("ahead") or 0)}
            pr = cached.get("pr")
            cells.update(commits=_fmt_counts(ab), ab=ab, pr=_fmt_pr(pr),
                         pr_data=pr, base_branch=base_branch or None)
            if not refresh_pr:
                return cells
            try:
                pr, used = _query_pr(repo, branch)
            except HarnessError:
                return cells
            store.cache_pr_status(branch, pr, tool=used,
                                  base_branch=base_branch,
                                  branch_tip=branch_tip, base_tip=base_tip,
                                  behind=ab["behind"], ahead=ab["ahead"])
            cells.update(pr=_fmt_pr(pr), pr_data=pr)
            return cells
    if branch:
        db = ((cached or {}).get("base_branch")
              or _repo_default_branch(repo)) or None
        ab = repos.ahead_behind(Path(wt), db) if (wt_ok and db) else None
        try:
            pr, used = _query_pr(repo, branch)
        except HarnessError:
            pr, used = (cached or {}).get("pr"), (cached or {}).get("tool")
        if pr is None and used is None and cached and cached.get("pr"):
            pr, used = cached["pr"], cached.get("tool")
        if wt_ok:
            store.cache_pr_status(branch, pr, tool=used if pr else None,
                                  base_branch=db,
                                  branch_tip=_git_tip(wt, "HEAD"),
                                  base_tip=_git_tip(wt, f"origin/{db}") if db else None,
                                  behind=(ab or {}).get("behind", 0),
                                  ahead=(ab or {}).get("ahead", 0))
            cells.update(commits=_fmt_counts(ab), ab=ab)
        cells.update(pr=_fmt_pr(pr), pr_data=pr, base_branch=db)
    return cells


def _session_rows(links: dict, show_worktree: bool, refresh: bool
                  ) -> tuple[list[dict], list[str]]:
    rows = []
    for k, v in links.items():
        cells = _status_cells(v, refresh)
        row = {"key": k, "branch": v.get("branch", "?"),
               "commits": cells["commits"], "pr": cells["pr"],
               "_pr_state": (cells["pr_data"] or {}).get("state", "")}
        if show_worktree:
            row["worktree"] = v.get("worktree", "?")
        rows.append(row)
    columns = (["key", "worktree", "branch", "commits", "pr"] if show_worktree
               else ["key", "branch", "commits", "pr"])
    return rows, columns


def _colorize_session(row: dict) -> dict:
    out = dict(row)
    st = row.get("_pr_state", "")
    if st in _PR_STYLES:
        c = _PR_STYLES[st]
        out["pr"] = f"[{c}]{row['pr']}[/{c}]"
    return out


def _enrich_entry(entry: dict, refresh: bool) -> dict:
    cells = _status_cells(entry, refresh)
    return {**entry, "commits": cells["commits"], "pr": cells["pr"],
            "commits_detail": cells["ab"], "pr_detail": cells["pr_data"]}


def _session_detail(key: str, entry: dict, refresh: bool) -> dict:
    cells = _status_cells(entry, refresh)
    detail = {"key": key, **entry, "commits": cells["commits"],
              "pr": _fmt_pr(cells["pr_data"]), "commits_detail": cells["ab"],
              "pr_detail": cells["pr_data"], "base_branch": cells["base_branch"],
              "issue_url": refs.issue_url(key, entry.get("issue"))}
    if not cells["pr_data"]:
        detail["create_hint"] = _create_hint(entry)
    return detail


def _print_detail(detail: dict) -> None:
    from rich.console import Console
    from rich.markup import escape
    c = Console()
    c.print(f"[bold]{escape(detail['key'])}[/bold]")
    for k in ("worktree", "branch", "repo"):
        if detail.get(k):
            c.print(f"  {k}: {escape(str(detail[k]))}")
    issue = detail.get("issue_url") or detail.get("issue")
    if issue:
        c.print(f"  issue: {escape(str(issue))}")
    pd = detail.get("pr_detail")
    if pd:
        style = _PR_STYLES.get(pd.get("state", ""), "white")
        c.print(f"  PR: [{style}]#{pd['number']} {escape(pd.get('title', ''))} "
                f"({pd.get('state', '')})[/{style}] by "
                f"{escape(pd.get('author', '?'))}")
        c.print(f"  url: {escape(pd.get('url', ''))}")
    cd = detail.get("commits_detail")
    if cd:
        base = escape(detail.get("base_branch") or "the base branch")
        c.print(f"  commits: {cd['behind']} behind / {cd['ahead']} ahead "
                f"of {base}")
    hint = detail.get("create_hint")
    if hint:
        c.print(f"  no PR/MR — create one: {escape(str(hint))}")


def _create_hint(entry: dict) -> str | None:
    """URL or shell command to open a new MR/PR for the session's branch.

    GitHub: web create-PR URL. GitLab: glab CLI command (self-hosted
    instances have no stable web create URL with a prefilled source
    branch).
    """
    branch = entry.get("branch", "")
    if not branch:
        return None
    remote = repos.remote_url(Path(entry["repo"])) if entry.get("repo") else None
    if not remote:
        return None
    host = repos._remote_host(remote) or ""
    if "github.com" in host:
        m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?/?$", remote)
        if m:
            return (f"https://github.com/{m.group(1)}/{m.group(2)}/compare/"
                    f"{branch}?expand=1")
        return None
    m = re.search(r"[:/](?P<path>[^/]+/[^/]+?)(?:\.git)?/?$", remote)
    if not m:
        return None
    path = m.group("path")
    if "gitlab" in host:
        return f"glab mr create --source-branch {shlex.quote(branch)}"
    return (f"glab mr create --repo {shlex.quote(host)}/"
            f"{shlex.quote(path)} --source-branch {shlex.quote(branch)}")

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
            key = _pick_session_key(ref, resolved, links)
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
    if json_output:
        print(json.dumps({k: _enrich_entry(v, refresh_pr)
                          for k, v in links.items()}, indent=2, ensure_ascii=False))
        return
    rows, columns = _session_rows(links, worktree, refresh_pr)
    _print_rows(rows, json_output, csv_output, columns,
                "Linked sessions", "(no linked sessions)",
                colorize=_colorize_session)
    eprint("commits: B|A = B commits behind, A commits ahead of the base branch")


# ── cd ────────────────────────────────────────────────────────────────


@app.command("cd")
@_catch_harness_errors
def cd_cmd(
    ref: str = typer.Argument(..., autocompletion=_complete_refs,
                              help="Issue/PR ref or session key."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Print the worktree root for a ref: cd "$(harness cd <ref>)".

    A child process cannot change the shell's cwd, so the command prints
    the path; the `completions install` shell wrapper (installed since
    0.4.0) makes bare `harness cd <ref>` change directory directly.
    """
    links = store.load_links()
    resolved = _resolve_session_key(ref, links)
    if resolved is None:
        _fail(f"no linked state for {ref}", EXIT_USAGE)
    key = _pick_session_key(ref, resolved, links)
    wt = links[key].get("worktree", "")
    if not wt or not Path(wt).exists():
        _fail(f"worktree missing for {key}: {wt or '?'}", EXIT_GENERAL)
    if json_output:
        print(json.dumps({"key": key, "worktree": wt}))
        return
    print(wt)


@app.command("register")
@_catch_harness_errors
def register(
    path: str = typer.Argument(..., help="Path to an existing worktree (not the main checkout)."),
    key: Optional[str] = typer.Option(None, "--key", help="Session key to register under (default: derived from the branch — 'jira:<KEY>' for issue branches, else 'branch:<branch>')."),
    issue: Optional[str] = typer.Option(None, "--issue", help="Issue/PR ref to attach (Jira key/URL, OWNER/REPO#NUM, PR/MR URL); sets the default key when --key is omitted."),
    repo_opt: Optional[str] = typer.Option(None, "--repo", help="Main checkout of the repository (default: derived from the worktree's git metadata)."),
    yes: bool = typer.Option(False, "--yes", "-y", "--force", help="Overwrite an existing link for the same key."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Register an existing (unregistered) worktree as a session link.

    Example: harness link-wt ~/dev/worktrees/projectx/feat/IPG-999--x

    The worktree must be a linked git worktree (not the main checkout).
    After registration the path works with status, sync, cd, and cleanup.
    """
    wt = Path(path).expanduser().resolve()
    if not wt.is_dir():
        _fail(f"not a directory: {wt}", EXIT_USAGE)
    # Main checkouts have commondir == gitdir; linked worktrees differ.
    try:
        gitdir = (run_cmd("git", "-C", str(wt), "rev-parse", "--absolute-git-dir")
                  or "").strip()
        commondir = (run_cmd("git", "-C", str(wt), "rev-parse",
                             "--path-format=absolute", "--git-common-dir")
                     or "").strip()
    except HarnessError as e:
        _fail(f"not a git repository: {wt} ({e})", EXIT_USAGE)
    if not gitdir or not commondir or \
            Path(commondir).resolve() == Path(gitdir).resolve():
        _fail(f"{wt} is a main checkout, not a linked worktree", EXIT_USAGE)
    branch = (run_cmd("git", "-C", str(wt), "branch", "--show-current")
              or "").strip()
    if not branch:
        _fail(f"cannot determine branch for {wt} (detached HEAD?)", EXIT_USAGE)
    main_repo = str(Path(commondir).parent.resolve())
    if repo_opt:
        r = Path(repo_opt).expanduser().resolve()
        if not r.is_dir():
            _fail(f"--repo is not a directory: {r}", EXIT_USAGE)
        main_repo = str(r)

    issue_ref = issue
    issue_url: str | None = None
    parsed = None
    if issue_ref:
        try:
            parsed = refs.parse_ref(issue_ref)
        except HarnessError as e:
            _fail(str(e), EXIT_USAGE)
        if parsed["url"].startswith("http"):
            issue_url = parsed["url"]
        elif parsed["tool"] == "jira-cli" and (site := refs.jira_site()):
            issue_url = f"{site}/browse/{parsed['number']}"
        if key is None:
            key = refs.issue_key(parsed)
    if key is None:
        m = re.match(r"(?:feat/)?([A-Z][A-Z0-9]+)-(\d+)", branch)
        key = f"jira:{m.group(0).split('/')[-1]}" if m else f"branch:{branch}"

    if parsed is not None and main_repo:
        # The user named this worktree path explicitly — that IS the
        # confirmation for the tracker↔repo relation; persist it.
        trackers.check_or_record(trackers.tracker_id(parsed), main_repo,
                                 yes=True, persist=True)

    rec: dict = {"worktree": str(wt), "branch": branch, "repo": main_repo}
    if issue_ref:
        rec["issue"] = issue_ref
    if issue_url:
        rec["issue_url"] = issue_url
    cur = store.lookup_link(key)
    if cur is not None:
        cur_wt = str(Path(str(cur.get("worktree", ""))).resolve())
        if cur_wt == str(wt):
            rec = {**cur, **rec}
            eprint(f"{key}: already registered — nothing to do")
        elif not yes:
            _fail(f"{key} is already linked to {cur_wt} — re-run with "
                  "--force to overwrite", EXIT_GENERAL)
        else:
            eprint(f"{key}: overwriting previous link to {cur_wt}")
    store.record_link(key, rec)
    if cur is None:
        try:
            repos.register_repo(repos.repo_name(Path(main_repo)), main_repo)
        except HarnessError:
            pass
    result = {"key": key, "worktree": rec["worktree"], "branch": rec["branch"],
              "repo": rec["repo"], "issue": rec.get("issue", "")}
    if rec.get("issue_url"):
        result["issue_url"] = rec["issue_url"]
    if json_output:
        print(json.dumps(result))
    else:
        print(f"registered {key} → {rec['worktree']} (branch {rec['branch']})")


# ── sync ──────────────────────────────────────────────────────────────


@app.command("sync")
@_catch_harness_errors
def sync_cmd(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs,
                                        help="Issue/PR ref or session key (omit: interactive pick, or --all)."),
    merge: bool = typer.Option(False, "-m", "--merge", help="Merge locally in the worktree instead of the default remote rebase. The branch is pushed to origin afterwards."),
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
    branch in the worktree; the branch is pushed to origin afterwards
    (including after harness-resolved conflicts).

    Example:
      harness sync IPG-929
      harness sync IPG-929 --merge
      harness sync --all --dry-run
    """
    links = store.load_links()
    if ref:
        resolved = _resolve_session_key(ref, links)
        if resolved is None:
            _fail(f"no linked state for {ref}", EXIT_USAGE)
        key = _pick_session_key(ref, resolved, links)
        keys = [key]
    elif all_sessions:
        keys = list(links)
    else:
        if not links:
            _fail("no linked sessions", EXIT_USAGE)
        if not sys.stdin.isatty():
            _fail("no ref given and stdin is not a TTY — pass a ref or --all",
                  EXIT_USAGE)
        keys = [_pick_session_key("sync", list(links), links)]
    results = []
    for k in keys:
        entry = links[k]
        eprint(f"syncing {k} …")
        if not all_sessions and not yes and not dry_run and sys.stdin.isatty():
            if not typer.confirm(f"sync {k} ({entry.get('branch', '?')})?"):
                _fail("aborted", EXIT_USAGE)
        results.append(_sync_one(k, entry, merge=merge,
                                 use_harness=harness, yes=yes or all_sessions,
                                 dry_run=dry_run, json_output=json_output))
        if all_sessions and result_failed(results[-1]):
            eprint(f"{k}: sync failed — continuing with remaining sessions (--all)")
    if json_output:
        out = results[0] if len(results) == 1 and ref else results
        print(json.dumps(out, indent=2, ensure_ascii=False))


def result_failed(result: dict) -> bool:
    """True when a sync result reports a failure state."""
    return result.get("result") in ("conflict", "missing-worktree", "error") \
        or result.get("result", "").startswith("conflict-") \
        or result.get("result") is None


def _sync_one(key: str, entry: dict, merge: bool, use_harness: bool,
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
    cells = _status_cells(entry, refresh_pr=False)
    pr = cells["pr_data"]
    tool = store.get_cached_pr_tool(branch) or _repo_tool(repo)
    db = cells["base_branch"] or _repo_default_branch(repo)
    if merge or not pr:
        if not db:
            _fail(f"{key}: cannot determine default branch for {repo}", EXIT_GENERAL)
        return _sync_local_merge(key, wt, branch, db, result,
                                 use_harness=use_harness, yes=yes,
                                 dry_run=dry_run, json_output=json_output)
    # Remote rebase (default): host rebases the branch on its base.
    result["strategy"] = "remote-rebase"
    if dry_run:
        result["result"] = f"would-rebase-via-{tool}"
        eprint(f"{key}: would run {tool} rebase for PR #{pr['number']} ({pr['url']})")
        return result
    try:
        sync_mod.rebase_remote(tool, pr, Path(wt))
    except HarnessError as e:
        eprint(f"{key}: {tool} rebase failed ({e}) — falling back to "
               "local merge")
        result["strategy"] = "local-merge"
        result["fallback"] = True
        if not db:
            _fail(f"{key}: cannot determine default branch for {repo}", EXIT_GENERAL)
        return _sync_local_merge(key, wt, branch, db, result,
                                 use_harness=use_harness, yes=yes,
                                 dry_run=False, json_output=json_output)
    result["result"] = "rebased"
    eprint(f"{key}: rebased PR #{pr['number']} via {tool}")
    result["pull"] = sync_mod.pull_rebased(Path(wt), branch)
    eprint(f"{key}: worktree updated from rebased origin/{branch} "
           f"({result['pull']})")
    return result


def _sync_local_merge(key: str, wt: str, branch: str, db: str, result: dict,
                      use_harness: bool, yes: bool, dry_run: bool,
                      json_output: bool) -> dict:
    """Local-merge flow shared by --merge, no-PR fallback, and rebase
    failure fallback."""
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
            run_harness_now = use_harness or yes
            if run_harness_now:
                result["result"] = "conflict-harness"
                prompt = (f"The branch {branch} has merge conflicts with "
                          f"{db} in files: {', '.join(out['conflicts'])}. "
                          "Resolve them, complete the merge, commit, push to "
                          f"origin/{branch}, and stop.")
                _run_harness("omp", prompt, wt, wt,
                             no_tty=bool(yes), no_harness=False,
                             result=result, json_output=json_output)
            elif sys.stdin.isatty():
                if typer.confirm("launch the harness to resolve?"):
                    result["result"] = "conflict-harness"
                    prompt = (f"The branch {branch} has merge conflicts with "
                              f"{db} in files: {', '.join(out['conflicts'])}. "
                              "Resolve them, complete the merge, commit, push to "
                              f"origin/{branch}, and stop.")
                    _run_harness("omp", prompt, wt, wt,
                                 no_tty=False, no_harness=False,
                                 result=result, json_output=json_output)
                else:
                    _fail("aborted (merge left in progress; abort with "
                          "`git merge --abort`)", EXIT_USAGE)
            else:
                result["result"] = "conflict"
                eprint(f"{key}: conflicts need human resolution — re-run "
                       "with --harness or --yes to auto-launch the harness")
            return result
        result["result"] = "merged"
    elif out["status"] == "up-to-date":
        result["result"] = "up-to-date"
        eprint(f"{key}: already up to date")
    else:
        result["result"] = "merged"
        eprint(f"{key}: merged {db} into {branch}")
    if result["result"] == "merged":
        sync_mod.push(Path(wt), branch)
        eprint(f"{key}: pushed {branch}")
        result["pushed"] = True
    return result


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


# ── serve (web UI) ────────────────────────────────────────────────────


def _default_static_dir() -> Path:
    """Built web UI directory: source tree, else the install receipt's source_dir."""
    source = Path(__file__).resolve().parent.parent.parent
    if (source / "web" / "dist" / "index.html").exists():
        return source / "web" / "dist"
    receipt = Path.home() / ".local" / "share" / "harness" / "install-receipt.json"
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
      harness serve
      harness serve --port 3345 --allowed-host devbox.local

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
              "(e.g. pip install 'harness[web]')", EXIT_GENERAL)
    run_server(host, resolved_port, Path(resolved_static), list(allowed_host or []))





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
    wrapper = _completions.cd_wrapper(prog="harness", shell=shell)
    print(wrapper + script, end="" if script.endswith("\n") else "\n")


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
