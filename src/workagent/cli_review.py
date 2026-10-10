"""`review` command + helpers (moved verbatim from cli.py)."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import backend, gitwt, refs, repos, store, trackers, worktrees
from . import sync as sync_mod
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _init_completions,
    _print_result,
    _print_rows,
    app,
    eprint,
)
from .cli_harness import _guard_harness, _run_harness, append_extra_prompt
from .errors import HarnessError

_COMP = _init_completions()
_complete_repos = _COMP["repos"]
_complete_refs = _COMP["refs"]

# ── review ────────────────────────────────────────────────────────────


def _is_reviewed(key: str, entry: dict) -> bool:
    """Reviewed AND the worktree tip still matches the recorded tip sha;
    new commits on the worktree invalidate the reviewed state."""
    if not entry.get("reviewed"):
        return False
    wt = entry.get("worktree", "")
    tip = repos.branch_tip(wt) if wt and Path(wt).is_dir() else ""
    return bool(tip) and tip == entry.get("reviewed_at", "")


def _reviewable_keys(links: dict, force: bool = False) -> list[tuple[str, str]]:
    """(key, pr_url) pairs to review under --all: live worktree, resolvable
    PR/MR, no live harness, and not already reviewed at the current tip.

    A non-pr link carrying a pr_url whose pr:<url> entry also exists is an
    alias: reviewed/tip live on the pr: entry, so the alias is skipped and
    one worktree/PR yields exactly one child (links for different PRs stay
    distinct).

    Issue #28: worktrees with an in-progress review/PR-comment (a live
    harness or any unresolved PR thread) are skipped unless force=True;
    worktrees without a PR/MR can never be reviewed. --force-all re-includes
    already-reviewed worktrees (still PR/MR only).
    """
    out = []
    for k, v in links.items():
        if v.get("active", 1) == 0:
            eprint(f"{k}: deactivated — skipping")
            continue
        own_pr = v.get("pr_url", "")
        if own_pr and not k.startswith("pr:") and f"pr:{own_pr}" in links:
            continue
        if _is_reviewed(k, v) and not force:
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
        if not force and _has_unresolved_comments(v, pr):
            eprint(f"{k}: unresolved PR comments — skipping (use --force-all)")
            continue
        out.append((k, pr))
    return out


def _has_unresolved_comments(entry: dict, pr_url: str) -> bool:
    """True when the cached PR stats record unresolved threads (issue #28).

    Cache-only by design: _reviewable_keys must not spawn host-CLI lookups
    (the --all tests run fully offline). Live stats ride the status cells
    via _reviews_cell and persist through cache_review_stats.
    """
    branch = str(entry.get("branch", ""))
    repo = str(entry.get("repo", "") or "")
    cached = store.lookup_pr_cache(branch, repo)
    n = (cached or {}).get("unresolved")
    return isinstance(n, int) and n > 0


def _same_repo(entry: dict, repo_dir: Path) -> bool:
    """True when the recorded entry belongs to ``repo_dir`` (or either side unrecorded)."""
    recorded = (entry.get("repo", "") or "").strip()
    if not recorded or not str(repo_dir):
        return True
    try:
        return Path(recorded).expanduser().resolve() == Path(str(repo_dir)).expanduser().resolve()
    except OSError:
        return recorded == str(repo_dir)

def _review_key_for(pr_url: str, worktree: str, branch: str, repo: str = "") -> str:
    """Link key review state lives under: the recorded row for this
    worktree/branch when one exists, else the source branch name.

    Reviewing an already-tracked worktree must reuse its row (stamping
    pr_url on it) — never insert a second row for the same path/(repo,
    branch) (which collides on the UNIQUE keys). The match is
    repo-scoped: the same branch name in another repo is a different
    worktree (e.g. IPG-1002 in projectx vs ipg-commons).
    """
    return (worktrees.recorded_key(worktree, branch, store.load_links(), repo=repo)
            or branch)


def _adopt_fix_reuse(result: dict, review_key: str) -> bool:
    """Point *result* at the latest finished review transcript when the
    pinned session file already names that session id.

    The web server passes the saved `sessions.file_path` as explicit
    --session-file (absolute review/... path). When its stem matches the
    latest review session id, mark the reuse so the harness layer treats
    the path as a resume target verbatim instead of rescoping it under
    fix_comments/ (same stem, wrong dir → omp starts a fresh session).
    Returns True when the reuse was adopted.
    """
    try:
        from pathlib import Path as _Path
        from . import store_sqlite as _sq
        pinned = str(result.get("session_file", "") or "")
        if not pinned:
            return False
        db = _sq.db_path()
        if not db.exists():
            return False
        existing = _sq.get_session(db, _Path(pinned).stem)
        if existing is None:
            return False
        if str(existing.get("worktree_ref", "")) != review_key:
            return False
        if str(existing.get("session_type", "") or "") != "review":
            return False
        latest = _sq.latest_review_session(db, review_key)
        if latest is not None and str(latest.get("id", "")) != str(existing.get("id", "")):
            return False
        if str(existing.get("state", "")) == "running":
            return False
        result["reuse_session_id"] = str(existing.get("id", ""))
        result["session_file"] = str(existing.get("file_path", "") or pinned)
        return True
    except Exception:
        return False


def _apply_fix_session(result: dict, review_key: str, fix_comments: bool,
                       new_fix_session: bool, explicit_file: str | None) -> None:
    """Resolve the fix-comments session strategy into *result*.

    Explicit --session-file always wins. When it names the latest
    finished review session (the web passes the saved file_path), the
    reuse is adopted so the transcript is resumed verbatim. Otherwise a
    fix run continues the latest non-running review session (reuse id +
    transcript) unless --new-fix-session forces a fresh fix_comments row
    (linked back via fixed_from_session_id). Non-fix runs leave result
    untouched.
    """
    if explicit_file:
        result["session_file"] = explicit_file
        if fix_comments and not new_fix_session:
            _adopt_fix_reuse(result, review_key)
        return
    if not fix_comments:
        return
    from . import store_sqlite as _sq
    db = _sq.db_path()
    latest = _sq.latest_review_session(db, review_key) if db.exists() else None
    if latest and not new_fix_session:
        result["reuse_session_id"] = latest["id"]
        result["session_file"] = latest.get("file_path")
    elif latest:
        result["session_metadata"] = {"fixed_from_session_id": latest["id"]}


def _ensure_branch_worktree(repo_dir: Path, head_ref: str, base_branch: str,
                            pr_url: str) -> tuple[str, str, str]:
    """Reuse or create the worktree for a PR/MR source branch.

    Returns (key, worktree, branch): an already-recorded row for this
    (repo, branch) wins (its key is reused with pr_url stamped on it);
    otherwise a fresh worktree is created via git-wt and recorded under
    the bare branch name. Branch names are unique per repo
    (UNIQUE (repo_key, branch)), so the same branch in another repo is
    simply a different row — e.g. IPG-1002 in projectx vs ipg-commons.
    """
    links = store.load_links()
    reuse = worktrees.recorded_key("", head_ref, links, repo=str(repo_dir))
    if isinstance(reuse, str):
        entry = links.get(reuse, {})
        worktree = entry.get("worktree", "")
        branch = entry.get("branch", head_ref)
        store.record_link(reuse, {"pr_url": pr_url, "worktree": worktree,
                                  "branch": branch, "repo": str(repo_dir)})
        return reuse, worktree, branch
    wt = gitwt.start_worktree(repo_dir, branch=head_ref, base=base_branch)
    worktree = wt.get("worktree_path", "")
    branch = wt.get("branch", head_ref)
    store.record_link(head_ref, {"pr_url": pr_url, "worktree": worktree,
                                 "branch": branch, "repo": str(repo_dir)})
    try:
        repos.register_repo(repos.repo_name(repo_dir), repo_dir)
    except HarnessError:
        pass
    return head_ref, worktree, branch


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
    links = store.load_links(include_inactive=True)
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
    launch: bool = typer.Option(False, "-L", "--launch", help="Launch the harness in the worktree (default: print the harness command and land in an interactive shell inside the worktree)."),
    terminal: bool = typer.Option(False, "--terminal", help="Open the harness command in the OS terminal instead of running headless (mutually exclusive with --launch)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    all_wts: bool = typer.Option(False, "--all", help="Review every not-reviewed linked worktree in parallel (non-TTY)."),
    sequential: bool = typer.Option(False, "--sequential", help="With --all: review one-by-one instead of in parallel."),
    fix: bool = typer.Option(False, "--fix", help="With --all: children auto-fix identified issues after yielding."),
    force_all: bool = typer.Option(False, "--force-all", help="With --all: include already-reviewed and unresolved-comment worktrees too (still needs a PR/MR)."),
    post_comments: bool = typer.Option(False, "--post-comments", hidden=True, help="Append the auto-comment prompt segment (set by --all)."),
    fix_comments: bool = typer.Option(False, "--fix-comments", help="Fix open PR/MR review comments instead of reviewing: validate each finding, apply, resolve/close, commit and push."),
    new_fix_session: bool = typer.Option(False, "--new-fix-session", help="With --fix-comments: start a fresh fix session instead of continuing the latest review session."),
    session_file: Optional[str] = typer.Option(None, "--session-file", help="Transcript .jsonl path: resumed via omp --resume when non-empty, otherwise used as --session-dir scope for a fresh session."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before terminal/headless harness commands (default: env_file in config.json, auto-created)."),
    extra_prompt: Optional[str] = typer.Option(None, "--extra-prompt", help="Extra instructions appended to the harness prompt."),
) -> None:
    """Create worktree from PR/MR and launch review.

    Example:
      workagent review https://github.com/OWNER/REPO/pull/33
      workagent review OWNER/REPO#33 --no-tty
      workagent review OWNER/REPO#33 --launch
      workagent review --all [--sequential] [--fix]
    """
    if not isinstance(session_file, str):
        session_file = None
    if terminal and launch:
        _fail("--terminal cannot be used with --launch (pick one run mode)", EXIT_USAGE)
    if sequential and not all_wts:
        _fail("--sequential requires --all", EXIT_USAGE)
    if fix and not all_wts:
        _fail("--fix requires --all", EXIT_USAGE)
    if force_all and not all_wts:
        _fail("--force-all requires --all", EXIT_USAGE)
    if session_file and all_wts:
        _fail("--session-file cannot be used with --all (one transcript per worktree — omit it and each launch gets its own file)", EXIT_USAGE)
    if fix and fix_comments:
        _fail("--fix cannot be used with --fix-comments", EXIT_USAGE)
    if post_comments and fix_comments:
        _fail("--post-comments cannot be used with --fix-comments", EXIT_USAGE)
    if new_fix_session and not fix_comments:
        _fail("--new-fix-session requires --fix-comments", EXIT_USAGE)
    if ref is None and not all_wts:
        _fail("missing PR/MR ref or worktree key\n"
              "  Pass a ref, or use --all to review every not-reviewed worktree.",
              EXIT_USAGE)
    if all_wts:
        reviewable = _reviewable_keys(store.load_links(include_inactive=True), force=force_all)
        if not reviewable:
            eprint("nothing to review")
            return

        def _child_argv(pr: str) -> list[str]:
            argv = [sys.executable, "-m", "workagent", "review", pr, "--no-tty"]
            if env_file:
                argv += ["--env-file", env_file]
            if fix_comments:
                return argv + ["--fix-comments"]
            return argv + ["--post-comments"] + (["--fix"] if fix else [])


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
        if not launch:
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
    if parsed is not None and parsed["kind"] == "issue_or_pr":
        repo_hint = f"github.com/{parsed['repo']}/pull/NUM" if parsed["repo"] else "github.com/OWNER/REPO/pull/NUM"
        _fail(f"{ref} is ambiguous — `review` needs a PR/MR URL (e.g. "
              f"https://{repo_hint}).", EXIT_USAGE)
    _pre_repo = ""
    if not repo:
        _pre = worktrees.resolve_worktree(ref, store.load_links())
        if isinstance(_pre, str):
            _pre_repo = store.load_links().get(_pre, {}).get("repo", "")
    repo_dir, base_branch, tid, outcome = trackers.resolve_repo_for_ref(
        parsed, repo, Path.cwd(), depth=depth, yes=yes,
        persist=not dry_run, pinned=_pre_repo)
    if outcome == "recorded" and tid and not dry_run:
        eprint(f"note: linked tracker {tid} to repo {repo_dir}")
    base_branch = repos.default_branch(repo_dir)
    harness_name = harness or store.load_config().get("default_harness", "omp")
    pr_url = parsed["url"]
    info: dict = {}
    if parsed["kind"] in ("pr", "mr"):
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
        _reuse_entry = store.load_links().get(reuse_key, {})
        if _reuse_entry.get("branch", head_ref) == (head_ref or session_ref) and not _same_repo(_reuse_entry, repo_dir):
            reuse_key = None  # same branch name, other repo — not our worktree
    if isinstance(reuse_key, str):
        reuse_entry = store.load_links().get(reuse_key, {})
        if reuse_entry.get("worktree") and Path(reuse_entry["worktree"]).is_dir():
            worktree = reuse_entry["worktree"]
            branch = reuse_entry.get("branch", head_ref)
            review_key = _review_key_for(pr_url, worktree, branch, repo=str(repo_dir))
            store.record_link(review_key, {"pr_url": pr_url, "worktree": worktree,
                                           "branch": branch, "repo": str(repo_dir)})
            if fix_comments:
                prompt = backend.prompt_for_fix_comments(pr_url, worktree=worktree, branch=branch)
            else:
                prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)

            if post_comments and not fix_comments:
                prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
            if fix and not fix_comments:
                prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
            prompt = append_extra_prompt(prompt, extra_prompt)
            result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
                      "harness": harness_name, "command": "review",
                      "fix_comments": fix_comments}
            eprint(f"worktree: {worktree}  branch: {branch}")
            if launch:
                # Same staleness rule as sync: review a pulled tip, never a
                # stale local. Fast-forward/merge origin/<branch> first; a
                # pull conflict aborts loudly (exit 1) instead of launching
                # the harness on half-merged state. Without --launch (preview) the pull is skipped:
                # the command is printed without touching git.
                try:
                    pulled = sync_mod.pull_branch(Path(worktree), branch)
                except HarnessError as e:
                    _fail(f"{e}", EXIT_GENERAL)
                if pulled != "up-to-date":
                    eprint(f"{review_key}: pulled origin/{branch} ({pulled})")
                _guard_harness(review_key, worktree)
                _mark_reviewed(review_key, worktree)
            _apply_fix_session(result, review_key, fix_comments, new_fix_session, session_file)
            try:
                _run_harness(harness_name, prompt, worktree, str(repo_dir),
                             no_tty, launch, result, json_output,
                             run_key=review_key, session_file=result.get("session_file"),
                             terminal=terminal, env_file=env_file)
            except HarnessError:
                if launch:
                    _clear_reviewed(review_key)
                raise
            return
    if not head_ref:
        _fail(f"could not determine the MR head branch for {pr_url}.\n"
              f"  Run `git fetch origin` in {repo_dir} and check `glab`/`gh` auth for that host.",
              EXIT_USAGE)
    review_key, worktree, branch = _ensure_branch_worktree(
        repo_dir, head_ref, base_branch, pr_url)

    if fix_comments:
        prompt = backend.prompt_for_fix_comments(pr_url, worktree=worktree, branch=branch)
    else:
        prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)

    if post_comments and not fix_comments:
        prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
    if fix and not fix_comments:
        prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
    prompt = append_extra_prompt(prompt, extra_prompt)
    result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
              "harness": harness_name, "command": "review",
              "fix_comments": fix_comments}
    eprint(f"worktree: {worktree}  branch: {branch}")
    if launch:
        _guard_harness(review_key, worktree)
        _mark_reviewed(review_key, worktree)
    _apply_fix_session(result, review_key, fix_comments, new_fix_session, session_file)
    try:
        _run_harness(harness_name, prompt, worktree, str(repo_dir), no_tty,
                     launch, result, json_output, run_key=review_key,
                     session_file=result.get("session_file"), terminal=terminal,
                     env_file=env_file)
    except HarnessError:
        if launch:
            _clear_reviewed(review_key)
        raise


