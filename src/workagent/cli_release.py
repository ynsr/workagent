"""`release` command: ship default -> release via FF-only auto-merge MR."""
from __future__ import annotations

import re as _re
import sys
import time
from typing import Optional

import typer

from . import repos
from .cli_core import (
    EXIT_GENERAL,
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _print_result,
    app,
    eprint,
)
from .cli_repo_util import _repo_tool
from .errors import HarnessError, run_cmd

_now = time.monotonic
_sleep = time.sleep

_PIPELINE_GATE_RE = _re.compile(r"405|pipeline.*must succeed|must succeed",
                                _re.IGNORECASE)


@app.command("release")
@_catch_harness_errors
def release(
    repo: str = typer.Argument(..., help="Registered repo name or path."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompts."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
    checks: str = typer.Option("ci", "--checks", help="Gates: ci, local, both, or none."),
    diff: str = typer.Option("remote", "--diff", help="Diff source: remote or local."),
    pre_release_timeout: str = typer.Option("10m", "--pre-release-timeout",
                                            help="Timeout for the pre-release gate (e.g. 10m)."),
    ci_timeout: str = typer.Option("15m", "--ci-timeout",
                                   help="Timeout for the CI gate (e.g. 15m)."),
    env_file: Optional[str] = typer.Option(None, "--env-file", help="Shell script sourced before the pre-release gate command (default: env_file in config.json, auto-created)."),
) -> None:
    """Ship default -> release via pinned-SHA MR merge (FF-only, never squash)."""
    result = run_release(repo, yes=yes, dry_run=dry_run, json_output=json_output,
                         checks=checks, diff=diff,
                         pre_release_timeout=pre_release_timeout, ci_timeout=ci_timeout,
                         env_file=env_file)
    _print_result(result, json_output)


def _host_tool(path: str) -> str:
    """Host CLI for *path*: stored repo tool, else glab/gh probe, else glab."""
    try:
        tool = _repo_tool(path)
        if tool in ("gh", "glab"):
            return tool
    except Exception:
        pass
    return "glab"


def _existing_open_mr(tool: str, path: str, default_branch: str, release_branch: str) -> str:
    """URL of an existing open default->release MR, else ''."""
    from . import refs as _refs
    try:
        prs = _refs.fetch_open_prs(tool, cwd=path)
    except Exception:
        return ""
    for pr in prs:
        if str(pr.get("branch", "")) != default_branch:
            continue
        target = str(pr.get("target_branch", "") or pr.get("base", "") or "")
        if target and target != release_branch:
            continue
        url = str(pr.get("url", "") or "")
        if url:
            return url
    return ""


def _open_mr(tool: str, path: str, default_branch: str, release_branch: str, title: str) -> str:
    """Open the release MR; return its URL."""
    if tool == "gh":
        out = run_cmd("gh", "pr", "create", "--base", release_branch,
                      "--head", default_branch, "--title", title, cwd=path)
    else:
        out = run_cmd("glab", "mr", "create", "--source-branch", default_branch,
                      "--target-branch", release_branch, "--title", title, cwd=path)
    url = (out or "").strip().splitlines()[-1] if out else ""
    if not url:
        raise HarnessError("host CLI opened the MR but returned no URL", exit_code=1)
    return url
def _find_or_open_mr(tool: str, path: str, default_branch: str, release_branch: str,
                     title: str) -> str:
    """Reuse an existing open default->release MR, else open one."""
    existing = _existing_open_mr(tool, path, default_branch, release_branch)
    return existing or _open_mr(tool, path, default_branch, release_branch, title)


def _origin_tip_now(path: str, branch: str) -> str:
    """Fresh origin/<branch> SHA (no cache; caller fetches first)."""
    return (run_cmd("git", "-C", path, "rev-parse", f"origin/{branch}") or "").strip()


def _fetch_branch(path: str, branch: str) -> None:
    """Refresh origin/<branch>; never fails the release (offline tolerated)."""
    try:
        run_cmd("git", "-C", path, "fetch", "origin", branch, echo=False)
    except HarnessError:
        pass


def _head_contains(path: str, release_branch: str, pinned: str) -> bool:
    """True when origin/<release> == pinned or contains it (merge commit)."""
    head = _origin_tip_now(path, release_branch)
    if head == pinned:
        return True
    try:
        run_cmd("git", "-C", path, "merge-base", "--is-ancestor", pinned,
                f"origin/{release_branch}", echo=False)
    except HarnessError:
        return False
    return True


def _verify_head(path: str, release_branch: str, pinned: str) -> None:
    """Confirm origin/<release> has *pinned* after merge; _fail otherwise.

    Fetches the branch first (the checkout's remote-tracking ref is stale
    after a host-side MR merge) and retries twice; a merge-commit head is
    accepted when it contains the pinned SHA.
    """
    head = ""
    for _ in range(3):
        _fetch_branch(path, release_branch)
        try:
            if _head_contains(path, release_branch, pinned):
                _invalidate_diff_cache_by_path(path)
                return
            head = _origin_tip_now(path, release_branch)
        except HarnessError as e:
            _fail(f"cannot verify release head: {e}", EXIT_GENERAL)
            return
    _fail(f"release head {head or '?'} != pinned {pinned}; verify on the host.",
          EXIT_GENERAL)


def _invalidate_diff_cache_by_path(path: str) -> None:
    """Drop cached release diff counts for the repo at *path* (merged)."""
    try:
        from . import store as _store
        from . import store_sqlite as _sq
        from pathlib import Path as _P
        db = _store._sqlite_path()  # noqa: SLF001 — same package
        if db is None:
            return
        want = str(_P(path).expanduser().resolve())
        for name, info in _store.load_repos().items():
            try:
                got = str(_P(str((info or {}).get("path", ""))).expanduser().resolve())
            except Exception:
                continue
            if got and got == want:
                with _sq.connect(db) as conn:
                    conn.execute("DELETE FROM release_diff_cache WHERE repo_key = ?",
                                 (name,))
    except Exception:
        pass

def _mr_state_now(mr_url: str, path: str) -> dict | None:
    """Fresh MR state; None on lookup failure (warned, caller proceeds)."""
    from . import refs as _refs
    try:
        return _refs.fetch_pr_merge_state(mr_url, cwd=path)
    except HarnessError as e:
        eprint(f"warning: cannot read MR state for {mr_url}: {e}")
        return None


def _wait_mr_merged(tool: str, path: str, mr_url: str, pinned: str,
                    timeout_s: int) -> bool:
    """Poll until the MR reports merged; False on timeout/terminal state.

    gh merges immediately (nothing to wait for); GitLab auto-merge may
    queue behind a pipeline. Unknown state lookups (non-MR URLs in tests)
    are treated as merged so callers proceed to SHA verification.
    """
    if tool != "glab":
        return True
    deadline = _now() + timeout_s
    wait = 5.0
    while True:
        fresh = _mr_state_now(mr_url, path)
        if fresh is None:
            return True
        state = str(fresh.get("state") or "").lower()
        if state == "merged":
            return True
        if state in ("closed",):
            eprint(f"warning: {mr_url} reports {state}; verifying pinned SHA anyway.")
            return True
        if _now() + wait > deadline:
            _sleep(max(0.0, deadline - _now()))
            fresh = _mr_state_now(mr_url, path)
            if fresh is not None and str(fresh.get("state") or "").lower() == "merged":
                return True
            return False
        eprint(f"merge queued on the host (MR {state}); waiting for {pinned[:8]} …")
        _sleep(wait)
        wait = min(wait * 2, 30.0)


def _pipeline_gate_rejection(msg: str) -> bool:
    """True when the host rejected a merge over its pipeline gate (405)."""
    return bool(_PIPELINE_GATE_RE.search(msg or ""))


def _merge_pinned(tool: str, path: str, release_branch: str, pinned: str,
                  mr_url: str, strategy: list[str], checks: str = "ci",
                  ci_timeout_s: int = 900) -> str:
    """Merge *pinned* via the first viable strategy; return strategy used.

    checks local/none forces an immediate GitLab merge (--auto-merge=false:
    glab otherwise queues behind any running pipeline). checks ci/both keeps
    the host default and waits for the queued merge (bounded by
    ci_timeout_s) before SHA verification.
    """
    from . import release_caps as _caps
    from . import repos_release as _rr
    info = None
    try:
        rows = _rr.store.load_repos().values()
    except Exception:
        rows = []
    for row in rows:
        if str((row or {}).get("path", "")) == path:
            info = row
            break
    default = str((info or {}).get("default_branch", "") or "")
    _fetch_branch(path, default)
    if default and _origin_tip_now(path, default) not in ("", pinned):
        _fail(f"release source advanced past {pinned}; re-run release.", EXIT_GENERAL)
    force = checks in ("local", "none")
    last_err: Exception | None = None
    for strat in strategy:
        try:
            if strat == "gitlab-mr-ff" and tool == "glab":
                argv = ["glab", "mr", "merge", "--sha", pinned]
                if force:
                    argv.append("--auto-merge=false")
                argv.append(mr_url)
                try:
                    run_cmd(*argv, cwd=path)
                except HarnessError as e:
                    if force and _pipeline_gate_rejection(str(e)):
                        _fail(f"release merge rejected ({strat}): the project "
                              f"requires a successful pipeline before merging; "
                              f"re-run with --checks ci. MR left open: {mr_url}",
                              EXIT_GENERAL)
                    raise
                if not _wait_mr_merged(tool, path, mr_url, pinned, ci_timeout_s):
                    _fail(f"release merge queued on the host but {mr_url} never "
                          f"reported merged within {ci_timeout_s}s; MR left open: "
                          f"{mr_url}", EXIT_GENERAL)
                _verify_head(path, release_branch, pinned)
                return strat
            if strat == "ff-push":
                run_cmd("git", "push", "origin",
                        f"{pinned}:refs/heads/{release_branch}", cwd=path)
                if tool == "gh" and "github-pr-merge-commit" in strategy:
                    continue
                _verify_head(path, release_branch, pinned)
                return strat
            if strat == "github-pr-merge-commit" and tool == "gh":
                run_cmd("gh", "pr", "merge", "--merge", "--match-head-commit",
                        mr_url, cwd=path)
                _verify_head(path, release_branch, pinned)
                return strat
        except HarnessError as e:
            last_err = e
            try:
                _caps.invalidate(tool, path, release_branch)
            except Exception:
                pass
            if strat == "ff-push" and tool == "gh" and "github-pr-merge-commit" in strategy:
                continue
            _fail(f"release merge rejected ({strat}): {e}; MR left open: {mr_url}",
                  EXIT_GENERAL)
    if last_err is not None:
        _fail(f"release merge rejected: {last_err}; MR left open: {mr_url}", EXIT_GENERAL)
    _fail(f"no merge strategy available; MR left open: {mr_url}", EXIT_GENERAL)
    raise AssertionError("unreachable")


def _resolve_name(repo: str, registry: dict) -> str:
    """Registry key for a name or checkout path; '' when unknown."""
    if repo in registry:
        return repo
    from pathlib import Path as _P
    try:
        want = str(_P(repo).expanduser().resolve())
    except Exception:
        return ""
    for n, info in registry.items():
        try:
            got = str(_P(str(info.get("path", ""))).expanduser().resolve())
        except Exception:
            continue
        if got and got == want:
            return n
    return ""


def run_release(repo: str, *, yes: bool, dry_run: bool, json_output: bool,
                checks: str = "ci", diff: str = "remote",
                pre_release_timeout: str = "10m", ci_timeout: str = "15m",
                env_file: str | None = None) -> dict:
    """Release flow returning a JSON-able result dict (raises via _fail)."""
    from . import release_lock as _lock
    from . import release_util as _util
    from . import store as _store
    if checks not in ("ci", "local", "both", "none"):
        _fail(f"bad --checks {checks!r}: use ci, local, both, or none", EXIT_USAGE)
    if diff not in ("remote", "local"):
        _fail(f"bad --diff {diff!r}: use remote or local", EXIT_USAGE)
    local_timeout = _util.parse_duration(pre_release_timeout)
    ci_timeout_s = _util.parse_duration(ci_timeout)
    registry = _store.load_repos()
    name = _resolve_name(repo, registry)
    if not name:
        _fail(f"unknown repo: {repo}", EXIT_USAGE)
    assert name
    base = _util.worktree_base()
    lock = _lock.lock_path(str(base), name)
    try:
        with _lock.held_lock(lock):
            return _run_locked(name, registry, yes=yes, dry_run=dry_run,
                               checks=checks, diff=diff, local_timeout=local_timeout,
                               ci_timeout_s=ci_timeout_s, env_file=env_file)
    except KeyboardInterrupt:
        _fail("interrupted; worktree kept for inspection.", EXIT_GENERAL)
    raise AssertionError("unreachable")


def _run_locked(name: str, registry: dict, *, yes: bool, dry_run: bool,
                checks: str, diff: str, local_timeout: int, ci_timeout_s: int,
                env_file: str | None = None) -> dict:
    """Steps 1-7 inside the release lock."""
    from . import release_gates as gates
    from .release_util import worktree_base
    ctx = repos.validate_release(name, diff)
    if ctx.get("note"):
        return {"repo": name, "merged": False, "mr_url": "", **ctx}
    path, tool = ctx["path"], ctx["tool"]
    default, release_branch, pinned = ctx["default_branch"], ctx["release_branch"], ctx["pinned_sha"]
    strategy = list(ctx.get("strategy") or [])
    gate = ((registry.get(name) or {}).get("commands") or {}).get("pre-release") or ""
    title = f"Release {default} -> {release_branch}"
    if dry_run:
        eprint(f"dry-run: validate {name} {default}@{pinned} -> {release_branch} "
               f"({ctx.get('ahead', 0)} commits)")
        eprint(f"dry-run: would open or reuse MR {title}")
        if checks in ("local", "both") and gate:
            eprint(f"dry-run: would run pre-release: {gate}")
        if checks in ("ci", "both"):
            eprint(f"dry-run: would poll CI for {pinned}")
        mode = " (--auto-merge=false, immediate)" if checks in ("local", "none") else " (host default, wait for merged)"
        eprint(f"dry-run: would merge {pinned} via {strategy[0] if strategy else 'none'}{mode}")
        return {"repo": name, "pinned_sha": pinned, "checks": checks,
                "strategy": strategy, "mr_url": "", "merged": False, "dry_run": True}
    mr_url = _find_or_open_mr(tool, path, default, release_branch, title)
    try:
        if checks in ("local", "both") and gate:
            gates.run_local_gate(path, name, pinned, gate, local_timeout, env_file)
        if checks in ("ci", "both"):
            gates.run_ci_gate(tool, path, pinned, ci_timeout_s)
    except KeyboardInterrupt:
        _fail("interrupted; worktree kept for inspection.", EXIT_GENERAL)
        raise AssertionError("unreachable")
    if not yes:
        if sys.stdin.isatty():
            try:
                print(f"Release {pinned} via {strategy[0] if strategy else 'none'} "
                      f"checks={checks} {mr_url}? [y/N] ", file=sys.stderr, end="")
                answer = sys.stdin.readline().strip().lower()
            except EOFError:
                answer = ""
            if answer not in ("y", "yes"):
                _fail("aborted", EXIT_USAGE)
        else:
            _fail("needs --yes in non-interactive mode", EXIT_USAGE)
    used = _merge_pinned(tool, path, release_branch, pinned, mr_url, strategy,
                         checks=checks, ci_timeout_s=ci_timeout_s)
    try:
        from . import release_worktree as _wt
        _wt.finish(_wt.release_path(str(worktree_base()), name), True)
    except Exception:
        pass
    eprint(f"released {pinned} via {used} checks={checks} {mr_url}")
    return {"repo": name, "pinned_sha": pinned, "checks": checks,
            "strategy": used, "mr_url": mr_url, "merged": True}
