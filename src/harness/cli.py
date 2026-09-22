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
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import typer

from . import __version__, backend, gitwt, refs, repos, store, trackers, worktrees
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


def _guard_harness(key: str | None, worktree: str) -> None:
    """Advisory pre-check: one live harness per worktree (issue #6).

    The atomic claim inside _run_harness is the real lock; this stays for
    early exits (review-all skip, --all skip, cleanup refusal) where we
    never reach a launch and must not claim a slot.
    """
    if not key:
        return
    rec = store.active_harness(key)
    if rec is None:
        # Same worktree may be recorded under a different key.
        for k, v in store.load_harnesses().items():
            if k != key and v.get("worktree") == worktree:
                rec = v
                break
    if rec is not None:
        _fail(f"worktree {worktree} already has a live harness "
              f"({rec['harness']}, pid {rec['pid']}) — wait for it to "
              "finish or kill it", 1)


def _harness_cell(key: str, worktree: str = "") -> str:
    """Display cell: "<harness> <pid>" while one is live, "" otherwise."""
    rec = store.active_harness(key)
    if rec is None and worktree:
        # The live harness may be recorded under a different key.
        for v in store.load_harnesses().values():
            if v.get("worktree") == worktree:
                rec = v
                break
    return f"{rec['harness']} {rec['pid']}" if rec else ""


def _run_harness(harness_name: str, prompt: str, worktree: str, fallback_dir: str,
                 no_tty: bool, no_runtime: bool, result: dict, json_output: bool,
                 run_key: str | None = None, session_file: str | None = None) -> None:
    """Launch the runtime in the worktree; with --no-runtime print the exact
    command instead and hand the worktree to the user (shell exec on TTY).

    Every real launch carries a session file: an explicit session_file
    (CLI --session-file) wins, otherwise a path is generated under
    sessions/<runtime>/ and passed to the runtime (--resume for omp).
    --no-runtime and --dry-run (never reaches here) write nothing.
    Sessions rows are recorded post-cutover (state.db exists) only.
    """
    from . import store_sqlite as _sq
    # Direct python-level calls (tests) bypass Typer/Click: the OptionInfo
    # default object leaks through instead of None. Normalize to None.
    if not isinstance(session_file, str):
        session_file = None
    runtime = backend.get_runtime(harness_name)
    preview_extra = runtime.session_file_flag(session_file) if session_file else []
    preview_args = _HARNESS_ARGS + preview_extra if preview_extra else _HARNESS_ARGS
    runtime_cmd = " ".join(shlex.quote(a) for a in
                           runtime.command_argv(prompt, no_tty, preview_args))
    if no_runtime:
        eprint(f"runtime command: {runtime_cmd}")
        result["runtime_command"] = runtime_cmd
        _print_result(result, json_output)
        if sys.stdin.isatty():
            # "cd" for the user: replace this process with their shell in the
            # worktree; the printed runtime command is theirs to run.
            backend.cd_worktree(worktree or fallback_dir)
            shell = os.environ.get("SHELL") or "/bin/sh"
            os.execvp(shell, [shell])
        return
    sid: str | None = None
    db = _sq.db_path()
    session_path = session_file or ""
    if run_key and not no_runtime:
        # Atomic one-harness-per-worktree lock: claim first, inside the same
        # flock that records it; launch only when we own the slot. A live
        # record (pid alive) fails here instead of spawning a second run.
        blocker = store.record_harness_run(run_key, harness_name, worktree or fallback_dir)
        if blocker is not None:
            _fail(f"worktree {worktree or fallback_dir} already has a live harness "
                  f"({blocker['harness']}, pid {blocker['pid']}) — wait for it to "
                  "finish or kill it", 1)
    # Every real launch gets a session file: explicit --session-file wins,
    # otherwise generate one (touched below so omp --resume can write it).
    # Pre-cutover (no state.db) there is no sessions row — the file alone
    # still lets the user resume the exact session later.
    if not session_path:
        session_path = str(_sq.session_file_path(_sq.gen_session_id(),
                                                 harness_name))
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    Path(session_path).touch(exist_ok=True)
    # Post-cutover sessions row (best effort; launch continues on failure).
    if run_key and db.exists():
        try:
            sid = _sq.insert_session(
                db, worktree_ref=run_key, runtime_name=harness_name,
                initiator_command=result.get("command", harness_name),
                prompt=prompt, file_path=session_path,
                session_id=Path(session_path).stem)
        except Exception as e:
            # Post-cutover insert failure: launch continues, warn only;
            # drop the pre-created file so no orphan .jsonl remains.
            eprint(f"warning: session record failed: {e}")
            sid = None
            try:
                Path(session_path).unlink(missing_ok=True)
            except OSError:
                pass
            session_path = session_file or ""
    try:
        extra = runtime.session_file_flag(session_path) if session_path else None
        backend.launch(harness_name, prompt, worktree or fallback_dir, no_tty,
                       (_HARNESS_ARGS + extra) if extra else _HARNESS_ARGS)
        if sid:
            _sq.finish_session(db, sid, "finished")
    except Exception:
        if sid:
            try:
                _sq.finish_session(db, sid, "failed")
            except Exception:
                pass
        raise
    finally:
        if run_key:
            store.clear_harness_run(run_key)
    _print_result(result, json_output)


def _split_harness_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first bare `--`; everything after feeds the harness."""
    if "--" in argv and argv.index("--") > 0:
        cut = argv.index("--")
        return argv[:cut], [a for a in argv[cut + 1:] if a != "--"]
    return argv, []


@app.command("start")
@_catch_harness_errors
def start(
    ref: str = typer.Argument(..., help="Issue key/URL, OWNER/REPO#NUM, or bare number."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    base: Optional[str] = typer.Option(None, "--base", autocompletion=_complete_branches, help="Base branch (default: repo default). A non-default branch runs on that branch instead of creating a new one."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively (auto commit/push/MR prompt suffix)."),
    no_runtime: bool = typer.Option(False, "-N", "--no-runtime", help="Skip launching the runtime: print the runtime command and land in an interactive shell inside the worktree."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path passed to the runtime (omp --resume)."),
) -> None:
    """Create worktree from issue and launch harness.

    Example:
      harness start https://github.com/OWNER/REPO/issues/22
      harness start OWNER/REPO#22 --repo my-checkout --no-tty
      harness start OWNER/REPO#22 --dry-run
      harness start OWNER/REPO#22 --base feat/22--add-login
      harness start OWNER/REPO#22 --no-runtime
    """
    if not isinstance(session_file, str):
        session_file = None
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
    if not no_runtime:
        _guard_harness(key, worktree)
    _run_harness(harness_name, prompt, worktree, str(r), no_tty, no_runtime,
                 result, json_output, run_key=key, session_file=session_file)


# ── review ────────────────────────────────────────────────────────────


def _is_reviewed(key: str, entry: dict) -> bool:
    """Reviewed AND the worktree tip still matches the recorded tip sha;
    new commits on the worktree invalidate the reviewed state."""
    if not entry.get("reviewed"):
        return False
    wt = entry.get("worktree", "")
    tip = repos.branch_tip(wt) if wt and Path(wt).is_dir() else ""
    return bool(tip) and tip == entry.get("reviewed_at", "")


def _reviewable_keys(links: dict) -> list[tuple[str, str]]:
    """(key, pr_url) pairs to review under --all: live worktree, resolvable
    PR/MR, no live harness, and not already reviewed at the current tip.

    A non-pr link carrying a pr_url whose pr:<url> entry also exists is an
    alias: reviewed/tip live on the pr: entry, so the alias is skipped and
    one worktree/PR yields exactly one child (links for different PRs stay
    distinct).
    """
    out = []
    for k, v in links.items():
        own_pr = v.get("pr_url", "")
        if own_pr and not k.startswith("pr:") and f"pr:{own_pr}" in links:
            continue
        if _is_reviewed(k, v):
            continue
        pr = worktrees.worktree_pr_url(k, v)
        if pr and not worktrees.is_valid_worktree(v.get("worktree", "")):
            continue
        if not pr:
            wt = v.get("worktree", "")
            if wt and Path(wt).is_dir():
                eprint(f"{k}: no PR/MR — skipping")
            continue
        if store.active_harness(k):
            eprint(f"{k}: harness already live — skipping")
            continue
        out.append((k, pr))
    return out


def _review_key_for(pr_url: str, worktree: str, branch: str) -> str:
    """Link key review state lives under: the recorded row for this
    worktree/branch when one exists, else the pr:<url> key.

    Reviewing an already-tracked worktree must reuse its row (stamping
    pr_url on it) — never insert a second row for the same path/branch
    (which collides on the worktrees.branch UNIQUE key).
    """
    return (worktrees.recorded_key(worktree, branch, store.load_links())
            or f"pr:{pr_url}")


def _mark_reviewed(key: str, worktree: str) -> None:
    """Persist reviewed=True + the current worktree tip on the review row."""
    entry = store.load_links().get(key, {})
    entry["reviewed"] = True
    entry["reviewed_at"] = repos.branch_tip(worktree)
    store.record_link(key, entry)


def _clear_reviewed(key: str) -> None:
    """Revert reviewed/reviewed_at after a failed (non-exec) harness launch.

    record_link merges, which cannot drop keys — rewrite the entry without
    them instead (other fields preserved), same as `link remove`.
    """
    links = store.load_links()
    entry = links.get(key)
    if not entry:
        return
    entry.pop("reviewed", None)
    entry.pop("reviewed_at", None)
    links[key] = entry
    store.save_links(links)


@app.command("review")
@_catch_harness_errors
def review(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="PR/MR URL, OWNER/REPO#NUM, or worktree ref (key/branch/worktree); optional with --all."),
    repo: Optional[str] = typer.Option(None, "--repo", autocompletion=_complete_repos, help="Registered name, local path, or clone URL."),
    depth: int = typer.Option(7, "--depth", help="Clone depth for repo URLs."),
    harness: Optional[str] = typer.Option(None, "--harness", help="Harness to run (default: configured; v1: omp)."),
    no_tty: bool = typer.Option(False, "--no-tty", help="Run harness non-interactively."),
    no_runtime: bool = typer.Option(False, "-N", "--no-runtime", help="Skip launching the runtime: print the runtime command and land in an interactive shell inside the worktree."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    all_wts: bool = typer.Option(False, "--all", help="Review every not-reviewed linked worktree in parallel (non-TTY)."),
    sequential: bool = typer.Option(False, "--sequential", help="With --all: review one-by-one instead of in parallel."),
    fix: bool = typer.Option(False, "--fix", help="With --all: children auto-fix identified issues after yielding."),
    post_comments: bool = typer.Option(False, "--post-comments", hidden=True, help="Append the auto-comment prompt segment (set by --all)."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path passed to the runtime (omp --resume)."),
) -> None:
    """Create worktree from PR/MR and launch review.

    Example:
      harness review https://github.com/OWNER/REPO/pull/33
      harness review OWNER/REPO#33 --no-tty
      harness review OWNER/REPO#33 --no-runtime
      harness review --all [--sequential] [--fix]
    """
    if not isinstance(session_file, str):
        session_file = None
    if sequential and not all_wts:
        _fail("--sequential requires --all", EXIT_USAGE)
    if fix and not all_wts:
        _fail("--fix requires --all", EXIT_USAGE)
    if session_file and all_wts:
        _fail("--session-file cannot be used with --all", EXIT_USAGE)
    if ref is None and not all_wts:
        _fail("missing PR/MR ref or worktree key\n"
              "  Pass a ref, or use --all to review every not-reviewed worktree.",
              EXIT_USAGE)
    if all_wts:
        reviewable = _reviewable_keys(store.load_links())
        if not reviewable:
            eprint("nothing to review")
            return

        def _child_argv(pr: str) -> list[str]:
            return [sys.executable, "-m", "harness", "review", pr,
                    "--no-tty", "--post-comments"] + (["--fix"] if fix else [])

        def _child_cmd(pr: str) -> str:
            return " ".join(shlex.quote(a) for a in _child_argv(pr))

        def _spawn(key: str, pr: str):
            repo = store.load_links().get(key, {}).get("repo", "")
            cwd = repo if repo and Path(repo).is_dir() else os.getcwd()
            # Child stdout/stderr inherit ours; the --post-comments child
            # appends the auto-comment segment to its review prompt.
            return subprocess.Popen(_child_argv(pr), cwd=cwd)

        if dry_run:
            rows = [{"key": k, "pr_url": pr, "command": _child_cmd(pr)}
                    for k, pr in reviewable]
            _print_rows(rows, json_output, csv_output=False,
                        columns=["key", "pr_url", "command"],
                        title="Review plan (dry-run)",
                        empty="nothing to review")
            return
        if no_runtime:
            rows = [{"key": k, "pr_url": pr, "command": _child_cmd(pr),
                     "exit_code": ""} for k, pr in reviewable]
            _print_rows(rows, json_output, csv_output=False,
                        columns=["key", "pr_url", "command", "exit_code"],
                        title="Review runs", empty="nothing reviewed")
            return
        rows: list[dict] = []

        if sequential:
            for key, pr in reviewable:
                proc = _spawn(key, pr)
                rows.append({"key": key, "pr_url": pr, "exit_code": proc.wait()})
        else:
            procs = [(key, pr, _spawn(key, pr)) for key, pr in reviewable]
            for key, pr, proc in procs:
                rows.append({"key": key, "pr_url": pr, "exit_code": proc.wait()})
        if not json_output:
            for row in rows:
                eprint(f"{row['key']}: review child exit {row['exit_code']}")
        _print_rows(rows, json_output, csv_output=False,
                    columns=["key", "pr_url", "exit_code"],
                    title="Review runs", empty="nothing reviewed")
        return
    session_ref = ref
    parse_err: HarnessError | None = None
    try:
        parsed = refs.parse_ref(ref)
    except HarnessError as e:
        parse_err = e
        parsed = None
    if parsed is None or parsed["kind"] not in ("pr", "mr"):
        # Worktree ref (key/branch/path): resolve to the recorded (or discovered) PR/MR.
        links = store.load_links()
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is not None:
            key = worktrees.pick_worktree(ref, resolved, links)
            entry = links.get(key, {})
            pr_url0 = worktrees.worktree_pr_url(key, entry) if entry else None
            if not pr_url0:
                _fail(f"worktree {key} has no recorded PR/MR.\n"
                      "  Pass a PR/MR URL, or create one first.", EXIT_USAGE)
            eprint(f"note: worktree {key} → {pr_url0}")
            ref = pr_url0
            parsed = refs.parse_ref(ref)
        elif parse_err is not None:
            raise parse_err
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

    reuse_key = worktrees.resolve_worktree(head_ref or session_ref, store.load_links())
    if isinstance(reuse_key, str):
        reuse_entry = store.load_links().get(reuse_key, {})
        if reuse_entry.get("worktree") and Path(reuse_entry["worktree"]).is_dir():
            worktree = reuse_entry["worktree"]
            branch = reuse_entry.get("branch", head_ref)
            review_key = _review_key_for(pr_url, worktree, branch)
            store.record_link(review_key, {"pr_url": pr_url, "worktree": worktree,
                                           "branch": branch, "repo": str(repo_dir)})
            prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)

            if post_comments:
                prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
            if fix:
                prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
            result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
                      "harness": harness_name}
            eprint(f"worktree: {worktree}  branch: {branch}")
            if not no_runtime:
                _guard_harness(review_key, worktree)
                _mark_reviewed(review_key, worktree)
            try:
                _run_harness(harness_name, prompt, worktree, str(repo_dir),
                             no_tty, no_runtime, result, json_output,
                             run_key=review_key, session_file=session_file)
            except HarnessError:
                if not no_runtime:
                    _clear_reviewed(review_key)
                raise
            return

    if not head_ref:
        _fail(f"could not determine the MR head branch for {pr_url}.\n"
              f"  Run `git fetch origin` in {repo_dir} and check `glab`/`gh` auth for that host.",
              EXIT_USAGE)
    wt = gitwt.start_worktree(repo_dir, branch=head_ref, base=base_branch)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", head_ref)
    review_key = _review_key_for(pr_url, worktree, branch)
    store.record_link(review_key, {"pr_url": pr_url, "worktree": worktree,
                                   "branch": branch, "repo": str(repo_dir)})
    try:
        repos.register_repo(repos.repo_name(repo_dir), repo_dir)
    except HarnessError:
        pass

    prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)

    if post_comments:
        prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
    if fix:
        prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
    result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
              "harness": harness_name}
    eprint(f"worktree: {worktree}  branch: {branch}")
    if not no_runtime:
        _guard_harness(review_key, worktree)
        _mark_reviewed(review_key, worktree)
    try:
        _run_harness(harness_name, prompt, worktree, str(repo_dir), no_tty,
                     no_runtime, result, json_output, run_key=review_key,
                     session_file=session_file)
    except HarnessError:
        if not no_runtime:
            _clear_reviewed(review_key)
        raise


# ── cleanup ───────────────────────────────────────────────────────────


@app.command("cleanup")
@_catch_harness_errors
def cleanup(
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="Issue ID/URL, PR/MR URL, or worktree ref (key/branch/worktree); omit with --merged."),
    force: bool = typer.Option(False, "--force", help="Skip state validation."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    merged: bool = typer.Option(False, "--merged", help="Clean every linked worktree whose PR/MR is merged/closed (requires --yes; skips live-harness and invalid worktrees)."),
    no_squash: bool = typer.Option(False, "--no-squash", help="Merge open PRs with a merge commit instead of squash."),
) -> None:
    """Close issue + remove worktree/branch/PR.

    Example:
      harness cleanup OWNER/REPO#22 --force --yes
      harness cleanup OWNER/REPO#22 --dry-run
      harness cleanup --merged --yes
    """
    if merged and ref:
        _fail("--merged takes no ref; it loops every linked worktree.", EXIT_USAGE)
    if not ref and not merged:
        _fail("ref required.\n"
              "  Pass a ref, or use --merged to clean every merged/closed link.",
              EXIT_USAGE)
    if merged and not yes and not dry_run:
        _fail("--merged requires --yes (non-interactive).\n"
              "  Re-run with --yes, or add --dry-run to preview.", EXIT_USAGE)

    links = store.load_links()

    if merged:
        # _status_cells(refresh_pr=True) is the merged/closed source of truth;
        # live harnesses and invalid worktrees are skipped, never torn down.
        rows = []
        for key, entry in links.items():
            branch = entry.get("branch", "")
            state = (_status_cells(dict(entry), refresh_pr=True).get("pr_data")
                     or {}).get("state", "")
            if state not in ("MERGED", "CLOSED"):
                rows.append({"key": key, "branch": branch,
                             "status": f"skipped:{state.lower() or 'no-pr'}"})
                continue
            if store.active_harness(key):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:live-harness"})
                continue
            wt = entry.get("worktree", "")
            if not wt or not worktrees.is_valid_worktree(wt):
                rows.append({"key": key, "branch": branch,
                             "status": "skipped:invalid"})
                continue
            rows.append(_cleanup_one(key, dict(entry), force, yes, dry_run,
                                     json_output, squash=not no_squash,
                                     merge=False))
        if json_output:
            print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
        else:
            _print_rows(rows, json_output=False, csv_output=False,
                        columns=["key", "branch", "status"],
                        title="Cleanup (merged/closed)",
                        empty="no linked worktrees")
        return

    resolved = worktrees.resolve_worktree(ref, links)
    if resolved is None:
        _fail(
            f"no linked state for {ref}.\n"
            "  Run `harness link list` to see linked worktrees.",
            EXIT_USAGE,
        )
    key = worktrees.pick_worktree(ref, resolved, links)
    entry = links.get(key, {})
    if entry is None or not entry:
        _fail(f"no linked state for {ref}.", EXIT_USAGE)
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    if not branch:
        _fail(f"linked entry for {ref} has no branch", EXIT_USAGE)
    if key != ref and not yes and not dry_run and sys.stdin.isatty():
        try:
            answer = input(f"ref {ref!r} matches worktree {key!r} — use it? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    if dry_run:
        _print_result({"dry_run": True, "key": key, "repo": str(repo), "branch": branch,
                       "pr_url": entry.get("pr_url", ""), "force": force,
                       "merge": entry.get("pr_url", "")}, json_output)
        return

    if not yes and not force and sys.stdin.isatty():
        try:
            answer = input(f"Remove worktree + delete branch '{branch}'? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            _fail("aborted", EXIT_USAGE)

    result = _cleanup_one(key, dict(entry), force, True, False, json_output,
                          squash=not no_squash)


def _merge_pr(pr_url: str, squash: bool) -> None:
    """Merge an open PR/MR (squash by default); raise HarnessError on failure."""
    if "github.com" in pr_url:
        run_cmd("gh", "pr", "merge", pr_url,
                *([] if squash else ["--no-squash"]))
    else:
        run_cmd("glab", "mr", "merge", pr_url,
                *([] if squash else ["--no-squash"]))
    eprint(f"merged {pr_url}")


def _cleanup_one(key: str, entry: dict, force: bool, yes: bool,
                 dry_run: bool, json_output: bool, squash: bool = True,
                 merge: bool | None = None) -> dict:
    """Clean one linked worktree: merge open PR/MR, git-wt cleanup, close + drop link.

    Shared by `cleanup <ref>` (after confirmation) and the `cleanup --merged`
    loop (after the merged/live-harness/validity gates). The caller owns ref
    resolution and prompts; this only acts. Returns a summary row with
    "status": "cleaned" or "dry-run".
    """
    repo = Path(entry.get("repo", "")).expanduser()
    branch = entry.get("branch", "")
    pr_url = entry.get("pr_url", "")
    stored_ref = entry.get("issue", "") or entry.get("pr_url", "") or key
    try:
        parsed = refs.parse_ref(key if not key.startswith("pr:") else stored_ref)
    except HarnessError:
        try:
            parsed = refs.parse_ref(stored_ref)
        except HarnessError:
            parsed = {"kind": "", "tool": "", "repo": "", "number": "",
                      "url": pr_url or stored_ref}
    if dry_run:
        return {"key": key, "branch": branch, "repo": str(repo),
                "pr_url": pr_url, "force": force, "status": "dry-run",
                "merge": pr_url or ""}
    state = (_status_cells(dict(entry), refresh_pr=False).get("pr_data")
             or {}).get("state", "")
    merged_now = False
    if merge is None:
        # Merge only on explicitly open states; unknown ("") falls back
        # to close-and-remove so offline/cache-miss cleanup still works.
        merge = bool(pr_url) and state in ("OPEN", "OPENED")
    if merge:
        # Open PR/MR: merge (squash default) so no work is lost. Any
        # failure raises BEFORE worktree removal / link drop / branch
        # delete — nothing is torn down on a failed merge.
        _merge_pr(pr_url, squash=squash)
        merged_now = True
    cleanup = gitwt.cleanup_worktree(repo, branch, delete_branch=True,
                                     force=force, yes=yes)
    if merged_now:
        # Remote branch goes LAST: local cleanup already succeeded.
        try:
            run_cmd("git", "-C", str(repo), "push", "origin",
                    "--delete", branch)
        except HarnessError as e:
            eprint(f"warning: remote branch delete failed: {e}")
    else:
        _close_issue(parsed, force)
        _close_pr(parsed, pr_url, force)
    remaining = {k: v for k, v in store.load_links().items() if k != key}
    store.save_links(remaining)
    if not json_output:
        eprint(f"{key}: cleaned")
    return {"key": key, "branch": branch, "status": "cleaned", "cleanup": cleanup}


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
    if not tracker:
        _fail("tracker is required: pass --tracker IPG|github:O/R|GitLab URL", EXIT_USAGE)
    if not (p / ".git").exists() and not p.is_dir():
        _fail(f"not a repo path: {p}", EXIT_USAGE)
    repos.register_repo(name, p)
    tid = trackers.normalize_id(tracker)
    cfg = store.load_config()
    entry = cfg.setdefault("trackers", {}).setdefault(tid, {"repos": []})
    norm = str(p.expanduser().resolve())
    if norm not in [str(Path(r).expanduser().resolve()) for r in entry.get("repos", [])]:
        entry.setdefault("repos", []).append(norm)
    store.save_config(cfg)
    _print_result({"registered": name, "path": str(p)}, json_output)


def _repo_tracker_map(cfg: dict) -> dict:
    """Reverse-lookup {resolved repo path: tracker id} from the mapping."""
    out: dict = {}
    for tid, entry in (cfg.get("trackers", {}) or {}).items():
        for r in (entry or {}).get("repos", []):
            try:
                out.setdefault(str(Path(r).expanduser().resolve()), tid)
            except Exception:
                continue
    return out


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
    tmap = _repo_tracker_map(cfg)
    items = [{"name": n, "path": v.get("path", ""),
              "tracker": tmap.get(str(Path(str(v.get("path", ""))).expanduser().resolve())
                                  if str(v.get("path", "")) else "", "")}
             for n, v in cfg.get("repos", {}).items()]
    _print_rows(items, json_output, csv_output, ["name", "path", "tracker"],
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

link_app = typer.Typer(help="View and manage tracker↔repo relations and worktree links.", no_args_is_help=True)
app.add_typer(link_app, name="link")


@link_app.command("list")
def link_list(
    worktree: bool = typer.Option(False, "--worktree", help="Show the worktree column in worktree links."),
    refresh_pr: bool = typer.Option(False, "--refresh-pr", help="Re-query PR status instead of using the cache."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output as CSV (stdout; logs go to stderr)."),
) -> None:
    """List tracker↔repo relations and worktree links.

    Example:
      harness link list
      harness link list --json
    """
    cfg = store.load_config()
    links = store.load_links()
    if json_output:
        print(json.dumps({"trackers": cfg.get("trackers", {}),
                          "worktrees": {k: _enrich_entry(k, v, refresh_pr)
                                        for k, v in links.items()}},
                         indent=2, ensure_ascii=False))
        return
    rows = [{"tracker": t, "repos": ", ".join(v.get("repos", []))}
            for t, v in cfg.get("trackers", {}).items()]
    _print_rows(rows, json_output, csv_output, ["tracker", "repos"],
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
    ref: str = typer.Argument(..., help="Tracker id or worktree key (issue/PR ref, branch, or worktree path)."),
    repo: Optional[str] = typer.Option(None, "--repo", help="Only remove this repo from the tracker mapping."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Remove a tracker mapping or a worktree link.

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
    resolved = worktrees.resolve_worktree(ref, store.load_links())
    if resolved is None:
        _fail(f"no tracker mapping or worktree link for {ref}", EXIT_USAGE)
    links = store.load_links()
    del links[resolved]
    store.save_links(links)
    _print_result({"removed": resolved}, json_output)


_PR_STYLES = {"open": "bright_green", "merged": "bright_magenta", "closed": "bright_red"}
_CI_TTL_SECONDS = 600
_CI_SYMBOLS = {"success": "✓", "failure": "✗", "running": "●"}
_CI_STYLES = {"success": "bright_green", "failure": "bright_red", "running": "cyan"}
_DB_CACHE: dict[str, str] = {}
_STATUS_TTL_SECONDS = 3 * 3600
_NEGATIVE_TTL_SECONDS = 30 * 60


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
    kind = "MR" if "merge_requests" in (pr.get("url") or "") else "PR"
    state = pr.get("state") or ""
    if state:
        return f"{kind} #{pr['number']} ({state})"
    return f"{kind} #{pr['number']}"


def _recorded_pr(url: str) -> dict | None:
    """Parse a recorded PR/MR URL into a minimal pr dict (state unknown)."""
    for rx, group in ((refs._GITLAB_MR, 3), (refs._GITHUB_PR, 2)):
        m = rx.match(url or "")
        if m:
            return {"number": int(m.group(group)), "state": "", "url": url}
    return None


def _seed_recorded_pr(cells: dict, entry: dict) -> dict:
    """A recorded pr_url wins when the cache/query produced no PR."""
    if not cells.get("pr_data") and entry.get("pr_url"):
        cells["pr_data"] = _recorded_pr(entry["pr_url"])
        cells["pr"] = _fmt_pr(cells["pr_data"])
    return cells


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
    ttl = _STATUS_TTL_SECONDS if cached.get("pr") else _NEGATIVE_TTL_SECONDS
    return age < timedelta(seconds=ttl)


def _ci_fresh(cached: dict) -> bool:
    """Cached CI still valid: checked within _CI_TTL_SECONDS."""
    ts = cached.get("ci_checked_at", "")
    if not ts:
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return False
    return age < timedelta(seconds=_CI_TTL_SECONDS)


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
        if shutil.which(other) is None:
            raise
        try:
            return refs.latest_pr(refs.fetch_pr_list_for_branch(other, branch, cwd=repo)), other
        except HarnessError as e2:
            eprint(f"warning: {other} pr lookup failed for {branch}: {e2}")
            raise


def _pr_cells(entry: dict, refresh_pr: bool = False) -> dict:
    """Commits + PR cells for one session, backed by pr_cache.json.

    A cache entry (keyed by branch) is reused while the session-branch tip
    and the remote-tracking base tip are unchanged and the entry is younger
    than its TTL (3h for a cached PR, 30min for a cached no-PR result),
    and --refresh-pr is not given. Valid cache: two `git rev-parse` calls,
    no host-CLI spawn, no API call. --refresh-pr re-queries only the PR
    (counts still reuse when tips are unchanged). When the cache or the
    live query yields no PR but the link records a `pr_url`, the recorded
    URL fills the PR cell. ``_tip``/``_tool`` are internal, consumed by the
    CI cell resolution in :func:`_status_cells`.
    """
    wt = entry.get("worktree", "")
    branch = entry.get("branch", "")
    repo = entry.get("repo", "")
    cells = {"commits": "-", "ab": None, "pr": "-", "pr_data": None,
             "base_branch": None, "_tip": None, "_tool": None}
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
            cells["_tip"], cells["_tool"] = branch_tip, cached.get("tool")
            cells.update(commits=_fmt_counts(ab), ab=ab, pr=_fmt_pr(pr),
                         pr_data=pr, base_branch=base_branch or None)
            if not refresh_pr:
                return _seed_recorded_pr(cells, entry)
            try:
                pr, used = _query_pr(repo, branch)
            except HarnessError:
                return _seed_recorded_pr(cells, entry)
            store.cache_pr_status(branch, pr, tool=used,
                                  base_branch=base_branch,
                                  branch_tip=branch_tip, base_tip=base_tip,
                                  behind=ab["behind"], ahead=ab["ahead"])
            cells["_tip"], cells["_tool"] = branch_tip, used
            cells.update(pr=_fmt_pr(pr), pr_data=pr)
            return _seed_recorded_pr(cells, entry)
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
            branch_tip = _git_tip(wt, "HEAD")
            store.cache_pr_status(branch, pr, tool=used if pr else None,
                                  base_branch=db,
                                  branch_tip=branch_tip,
                                  base_tip=_git_tip(wt, f"origin/{db}") if db else None,
                                  behind=(ab or {}).get("behind", 0),
                                  ahead=(ab or {}).get("ahead", 0))
            cells["_tip"], cells["_tool"] = branch_tip, used if pr else None
            cells.update(commits=_fmt_counts(ab), ab=ab)
        cells.update(pr=_fmt_pr(pr), pr_data=pr, base_branch=db)
    return _seed_recorded_pr(cells, entry)


def _status_cells(entry: dict, refresh_pr: bool = False) -> dict:
    """_pr_cells plus the CI pipeline cell.

    CI resolves after the PR: no PR -> no CI cell. A cached ``ci`` is
    reused while the branch tip is unchanged, the check is younger than
    _CI_TTL_SECONDS (10 min) and --refresh-pr is not given; otherwise the
    pipeline is fetched once and persisted via store.cache_ci_status.
    """
    cells = _pr_cells(entry, refresh_pr)
    cells["ci"] = _ci_cell(entry, cells, refresh_pr)
    return cells


def _ci_cell(entry: dict, cells: dict, refresh_pr: bool) -> str | None:
    """CI pipeline status for the resolved PR: success|failure|running|not_started.

    None when there is no PR (no url) or the lookup failed (soft failure —
    fetch_ci_status warns on stderr and returns None; nothing is cached so
    the next run retries).
    """
    pr = cells.get("pr_data")
    if not pr or not pr.get("url"):
        return None
    branch = entry.get("branch", "")
    cached = store.load_pr_cache().get(branch) if branch else None
    branch_tip = cells.get("_tip")
    if branch_tip is None and entry.get("worktree") \
            and Path(entry["worktree"]).exists():
        branch_tip = _git_tip(entry["worktree"], "HEAD")
    if not refresh_pr and cached and _ci_fresh(cached) \
            and cached.get("ci_sha") == branch_tip:
        return cached.get("ci")
    url = pr["url"]
    tool = ((cached or {}).get("tool")
            or ("gh" if "github.com" in url else "glab"))
    ci = refs.fetch_ci_status(tool, url, entry.get("repo", ""))
    if branch:
        store.cache_ci_status(branch, ci, sha=branch_tip or "")
    return ci


def _session_rows(links: dict, show_worktree: bool, refresh: bool
                  ) -> tuple[list[dict], list[str]]:
    rows = []
    for k, v in links.items():
        cells = _status_cells(v, refresh)
        row = {"key": k, "branch": v.get("branch", "?"),
               "harness": _harness_cell(k, v.get("worktree", "")),
               "commits": cells["commits"], "pr": cells["pr"],
               "ci": cells["ci"] or "",
               "_pr_state": (cells["pr_data"] or {}).get("state", "")}
        if show_worktree:
            row["worktree"] = v.get("worktree", "?")
        rows.append(row)
    columns = (["key", "worktree", "branch", "harness", "commits", "pr", "ci"]
               if show_worktree
               else ["key", "branch", "harness", "commits", "pr", "ci"])
    return rows, columns


def _colorize_session(row: dict) -> dict:
    out = dict(row)
    st = row.get("_pr_state", "")
    if st in _PR_STYLES:
        c = _PR_STYLES[st]
        out["pr"] = f"[{c}]{row['pr']}[/{c}]"
    ci = row.get("ci") or ""
    if ci in _CI_STYLES:
        s = _CI_STYLES[ci]
        out["ci"] = f"[{s}]{_CI_SYMBOLS[ci]}[/{s}]"
    else:
        out["ci"] = "-"
    if not out.get("harness"):
        out["harness"] = "—"
    return out


def _enrich_entry(key: str, entry: dict, refresh: bool) -> dict:
    cells = _status_cells(entry, refresh)
    return {**entry, "harness": _harness_cell(key, entry.get("worktree", "")),
            "commits": cells["commits"], "pr": cells["pr"],
            "ci": cells["ci"],
            "wt_valid": worktrees.is_valid_worktree(entry.get("worktree", "")),
            "commits_detail": cells["ab"], "pr_detail": cells["pr_data"]}


def _session_detail(key: str, entry: dict, refresh: bool) -> dict:
    cells = _status_cells(entry, refresh)
    detail = {"key": key, **entry,
              "harness": _harness_cell(key, entry.get("worktree", "")),
              "commits": cells["commits"],
              "pr": _fmt_pr(cells["pr_data"]), "commits_detail": cells["ab"],
              "pr_detail": cells["pr_data"], "base_branch": cells["base_branch"],
              "ci": cells["ci"],
              "wt_valid": worktrees.is_valid_worktree(entry.get("worktree", "")),
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
    c.print(f"  harness: {escape(str(detail.get('harness') or '—'))}")
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
    ci = detail.get("ci")
    if ci:
        style = _CI_STYLES.get(ci, "white")
        c.print(f"  ci: [{style}]{_CI_SYMBOLS.get(ci, ci)} {escape(ci)}[/{style}]")
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
    ref: Optional[str] = typer.Argument(None, autocompletion=_complete_refs, help="Issue/PR ref or worktree key (omit: all links)."),
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
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is not None:
            key = worktrees.pick_worktree(ref, resolved, links)
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
        print(json.dumps({k: _enrich_entry(k, v, refresh_pr)
                          for k, v in links.items()}, indent=2, ensure_ascii=False))
        return
    rows, columns = _session_rows(links, worktree, refresh_pr)
    _print_rows(rows, json_output, csv_output, columns,
                "Linked worktrees", "(no linked worktrees)",
                colorize=_colorize_session)
    eprint("commits: B|A = B commits behind, A commits ahead of the base branch")


# ── candidates ────────────────────────────────────────────────────────


def _candidates(force: bool = False) -> dict:
    """Unlinked open PR/MRs + my recent issues; shared by the `candidates`
    command and the webapp /api/candidates endpoint.

    PRs are collected from every registered repo (per-repo failures →
    warning) minus any URL already linked in links.json; issues are
    `trackers.list_my_issues` filtered to created within the last 7 days.
    """
    warnings: list[str] = []
    linked = {str(v["pr_url"]).rstrip("/")
              for v in store.load_links().values() if v.get("pr_url")}
    prs: list[dict] = []
    for name, entry in store.load_config().get("repos", {}).items():
        path = str(entry.get("path", ""))
        if not path or not Path(path).exists():
            continue
        try:
            tool = _repo_tool(path)
            if tool not in ("gh", "glab"):
                continue
            for p in refs.fetch_open_prs(tool, path):
                url = str(p.get("url", ""))
                if url.rstrip("/") in linked:
                    continue
                key = refs.pr_key(url)
                prs.append({**p, "url": url, "key": key,
                            "repo": key.split(":", 1)[-1].rsplit("#", 1)[0]})
        except HarnessError as e:
            warnings.append(f"{name}: {e}")
        except Exception as e:  # one bad repo must not kill the listing
            warnings.append(f"{name}: {e}")
    prs.sort(key=lambda p: p.get("updated", ""), reverse=True)
    issues = trackers.list_my_issues(warnings, force=force)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent = [i for i in issues
              if (dt := trackers.parse_created(str(i.get("created", ""))))
              is not None and dt >= cutoff]
    recent.sort(key=lambda i: str(i.get("created", "")), reverse=True)
    return {"prs": prs, "issues": recent, "worktrees": _scan_worktrees(),
            "warnings": warnings}


def _scan_root() -> Path:
    """Scan root for unregistered worktrees; configurable, defaults to
    ~/dev/worktrees (issue #8 names ~/dev/worktress, which is a typo —
    that spelling does not exist on disk)."""
    raw = str(store.load_config().get("scan_root", "") or "")
    return Path(raw).expanduser() if raw else Path.home() / "dev" / "worktrees"


def _scan_worktrees() -> list[dict]:
    """Unregistered on-disk worktrees under <scan_root>/<repo>/: two shapes —
    `<branch>` and `<issue-type>/<branch>` (recombined with a `/`). Paths
    already linked in links.json are excluded; non-worktree dirs are
    skipped via worktrees.is_valid_worktree. Read-only."""
    links = store.load_links()
    known = set()
    for v in links.values():
        p = v.get("worktree", "")
        if p:
            try:
                known.add(str(Path(p).expanduser().resolve()))
            except OSError:
                continue
    root = _scan_root()
    found: list[dict] = []
    if not root.is_dir():
        return found
    try:
        repos = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for repo_dir in repos:
        try:
            children = sorted(p for p in repo_dir.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            # A leaf dir holding a worktree is pattern 1; a dir whose
            # children are worktrees is the pattern-2 issue-type prefix.
            try:
                grandchildren = sorted(
                    p for p in child.iterdir() if p.is_dir())
            except OSError:
                grandchildren = []
            if grandchildren and not worktrees.is_valid_worktree(str(child)):
                cands = grandchildren
            else:
                cands = [child]
            for wt in cands:
                branch = ""
                if worktrees.is_valid_worktree(str(wt)):
                    try:
                        branch = (run_cmd("git", "-C", str(wt), "branch",
                                         "--show-current") or "").strip()
                    except HarnessError:
                        continue
                if not branch:
                    continue
                try:
                    resolved = str(wt.resolve())
                except OSError:
                    continue
                if resolved in known:
                    continue
                m = re.match(r"(?:.*/)?([A-Z][A-Z0-9]+-\d+.*)", branch)
                key = f"jira:{m.group(1)}" if m else f"branch:{branch}"
                found.append({"path": str(wt), "repo": repo_dir.name,
                              "branch": branch, "key_guess": key})
    found.sort(key=lambda r: r["path"])
    return found


@app.command("candidates")
@_catch_harness_errors
def candidates_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    csv_output: bool = typer.Option(False, "--csv", help="Output the PR/MR table as CSV (stdout; logs go to stderr)."),
    reset_cache: bool = typer.Option(False, "--reset-cache", help="Clear the cached issue rows and re-fetch live."),
) -> None:
    """List unlinked open PR/MRs and my recent issues (last 7 days).

    Example:
      harness candidates
      harness candidates --json
    """
    if reset_cache:
        from . import store_sqlite as _sq
        _sq.clear_issue_cache(_sq.db_path())
    out = _candidates(force=reset_cache)
    for w in out["warnings"]:
        eprint(f"warning: {w}")
    if json_output:
        print(json.dumps({"prs": out["prs"], "issues": out["issues"],
                          "worktrees": out["worktrees"]},
                         indent=2, ensure_ascii=False))
        return
    from rich.markup import escape


    # Escape user content ONLY for the Rich table path; --csv/--json
    # carry raw titles (escaping would corrupt machine-readable cells).
    def _esc(rows: list[dict]) -> list[dict]:
        return [{**r, "title": escape(str(r.get("title", "")))} for r in rows]

    _print_rows(_esc(out["prs"]) if not csv_output else out["prs"],
                False, csv_output,
                ["url", "title", "repo", "updated"],
                "Unlinked PR/MRs", "(no unlinked open PR/MRs)")
    _print_rows(_esc(out["issues"]), False, False,
                ["key", "title", "status", "created"],
                "Recent issues (reported by me, last 7 days)",
                "(no recent issues)")
    _print_rows(out["worktrees"], False, False,
                ["path", "repo", "branch", "key_guess"],
                "Unregistered worktrees (scan root)",
                "(no unregistered worktrees)")


# ── cd ────────────────────────────────────────────────────────────────


@app.command("cd")
@_catch_harness_errors
def cd_cmd(
    ref: str = typer.Argument(..., autocompletion=_complete_refs,
                              help="Issue/PR ref or worktree key."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Print the worktree root for a ref: cd "$(harness cd <ref>)".

    A child process cannot change the shell's cwd, so the command prints
    the path; the `completions install` shell wrapper (installed since
    0.4.0) makes bare `harness cd <ref>` change directory directly.
    """
    links = store.load_links()
    resolved = worktrees.resolve_worktree(ref, links)
    if resolved is None:
        _fail(f"no linked state for {ref}", EXIT_USAGE)
    key = worktrees.pick_worktree(ref, resolved, links)
    wt = links[key].get("worktree", "")
    if not wt or not Path(wt).exists():
        _fail(f"worktree missing for {key}: {wt or '?'}", EXIT_GENERAL)
    if json_output:
        print(json.dumps({"key": key, "worktree": wt}))
        return
    print(wt)


# ── open ──────────────────────────────────────────────────────────────


@app.command("open")
@_catch_harness_errors
def open_cmd(
    ref: str = typer.Argument(..., autocompletion=_complete_refs,
                              help="Issue/PR ref or worktree key."),
) -> None:
    """Open the linked worktree directory with the OS file manager.

    Prints the worktree path on stdout. Launches no harness (nothing to
    guard — issue #6 covers harness launches only); refuses missing or
    invalid worktrees.

    Example:
      harness open IPG-929
    """
    links = store.load_links()
    resolved = worktrees.resolve_worktree(ref, links)
    if resolved is None:
        _fail(f"no linked state for {ref}.\n"
              "  Run `harness link list` to see linked worktrees.", EXIT_USAGE)
    key = worktrees.pick_worktree(ref, resolved, links)
    wt = links.get(key, {}).get("worktree", "")
    if not wt or not worktrees.is_valid_worktree(wt):
        _fail(f"no valid worktree for {key}: {wt or '?'}", EXIT_GENERAL)
    opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform,
                                                         "xdg-open")
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([opener, wt], **kwargs)
    except FileNotFoundError:
        _fail(f"no opener available ({opener})", EXIT_GENERAL)
    print(wt)


@app.command("register")
@_catch_harness_errors
def register(
    path: str = typer.Argument(..., help="Path to an existing worktree (not the main checkout)."),
    key: Optional[str] = typer.Option(None, "--key", help="Worktree key to register under (default: derived from the branch — 'jira:<KEY>' for issue branches, else 'branch:<branch>')."),
    issue: Optional[str] = typer.Option(None, "--issue", help="Issue/PR ref to attach (Jira key/URL, OWNER/REPO#NUM, PR/MR URL); sets the default key when --key is omitted."),
    repo_opt: Optional[str] = typer.Option(None, "--repo", help="Main checkout of the repository (default: derived from the worktree's git metadata)."),
    yes: bool = typer.Option(False, "--yes", "-y", "--force", help="Overwrite an existing link for the same key."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Register an existing (unregistered) worktree as a link.

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
                                        help="Issue/PR ref or worktree key (omit: interactive pick, or --all)."),
    merge: bool = typer.Option(False, "-m", "--merge", help="Merge locally in the worktree instead of the default remote rebase. The branch is pushed to origin afterwards."),
    harness: bool = typer.Option(False, "--harness", help="On unresolvable conflicts, launch the coding harness in the worktree."),
    all_sessions: bool = typer.Option(False, "--all", help="Sync every linked worktree (confirmed one by one)."),
    yes: bool = typer.Option(False, "--yes", "--force", "-y", help="Skip confirmations."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would run without touching anything."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path passed to the runtime on conflict resolution (omp --resume)."),
) -> None:
    """Bring a worktree branch up to date with its base branch.

    Default: remote rebase via `gh pr update-branch --rebase` /
    `glab mr rebase` (host merges server-side). With -m/--merge: fetch,
    fast-forward the local default branch and merge it into the
    worktree's branch; the branch is pushed to origin afterwards
    (including after harness-resolved conflicts).

    Example:
      harness sync IPG-929
      harness sync IPG-929 --merge
      harness sync --all --dry-run
    """
    if not isinstance(session_file, str):
        session_file = None
    links = store.load_links()
    if session_file and all_sessions:
        _fail("--session-file cannot be used with --all", EXIT_USAGE)
    if ref:
        resolved = worktrees.resolve_worktree(ref, links)
        if resolved is None:
            _fail(f"no linked state for {ref}", EXIT_USAGE)
        key = worktrees.pick_worktree(ref, resolved, links)
        keys = [key]
    elif all_sessions:
        keys = list(links)
    else:
        if not links:
            _fail("no linked worktrees", EXIT_USAGE)
        if not sys.stdin.isatty():
            _fail("no ref given and stdin is not a TTY — pass a ref or --all",
                  EXIT_USAGE)
        keys = [worktrees.pick_worktree("sync", list(links), links)]
    results = []
    for k in keys:
        entry = links[k]
        eprint(f"syncing {k} …")
        if not all_sessions and not yes and not dry_run and sys.stdin.isatty():
            if not typer.confirm(f"sync {k} ({entry.get('branch', '?')})?"):
                _fail("aborted", EXIT_USAGE)
        results.append(_sync_one(k, entry, merge=merge,
                                 use_harness=harness, yes=yes or all_sessions,
                                 dry_run=dry_run, json_output=json_output,
                                 session_file=session_file))
        if all_sessions and result_failed(results[-1]):
            eprint(f"{k}: sync failed — continuing with remaining worktrees (--all)")
    if json_output:
        out = results[0] if len(results) == 1 and ref else results
        print(json.dumps(out, indent=2, ensure_ascii=False))


def result_failed(result: dict) -> bool:
    """True when a sync result reports a failure state."""
    return result.get("result") in ("conflict", "missing-worktree", "error") \
        or result.get("result", "").startswith("conflict-") \
        or result.get("result") is None


def _sync_one(key: str, entry: dict, merge: bool, use_harness: bool,
              yes: bool, dry_run: bool, json_output: bool,
              session_file: str | None = None) -> dict:
    if not isinstance(session_file, str):
        session_file = None
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
    # A recorded pr_url seed (state "") is display-only: it never reached a
    # live host query, so it must not drive the remote-rebase strategy.
    if merge or not pr or not pr.get("state"):
        if not db:
            _fail(f"{key}: cannot determine default branch for {repo}", EXIT_GENERAL)
        return _sync_local_merge(key, wt, branch, db, result,
                                 use_harness=use_harness, yes=yes,
                                 dry_run=dry_run, json_output=json_output,
                                 session_file=session_file)
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
                                 dry_run=False, json_output=json_output,
                                 session_file=session_file)
    result["result"] = "rebased"
    eprint(f"{key}: rebased PR #{pr['number']} via {tool}")
    result["pull"] = sync_mod.pull_rebased(Path(wt), branch)
    eprint(f"{key}: worktree updated from rebased origin/{branch} "
           f"({result['pull']})")
    return result


def _sync_local_merge(key: str, wt: str, branch: str, db: str, result: dict,
                      use_harness: bool, yes: bool, dry_run: bool,
                      json_output: bool, session_file: str | None = None) -> dict:
    """Local-merge flow shared by --merge, no-PR fallback, and rebase
    failure fallback."""
    if not isinstance(session_file, str):
        session_file = None
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
                _guard_harness(key, wt)
                _run_harness("omp", prompt, wt, wt,
                             no_tty=bool(yes), no_runtime=False,
                             result=result, json_output=json_output, run_key=key,
                             session_file=session_file)
            elif sys.stdin.isatty():
                if typer.confirm("launch the harness to resolve?"):
                    result["result"] = "conflict-harness"
                    prompt = (f"The branch {branch} has merge conflicts with "
                              f"{db} in files: {', '.join(out['conflicts'])}. "
                              "Resolve them, complete the merge, commit, push to "
                              f"origin/{branch}, and stop.")
                    _guard_harness(key, wt)
                    _run_harness("omp", prompt, wt, wt,
                                 no_tty=False, no_runtime=False,
                                 result=result, json_output=json_output, run_key=key,
                                 session_file=session_file)
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


@app.command("migrate")
@_catch_harness_errors
def migrate_cmd(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """One-shot migration: links/pr_cache/harnesses JSON → state.db (issue #9).

    Verifies row counts, then deletes the JSON files (config.json kept).
    Idempotent: re-run is a no-op.

    Example: harness migrate --json
    """
    from . import store_sqlite as sq
    out = sq.migrate_json(store.config_dir(), sq.db_path())
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
