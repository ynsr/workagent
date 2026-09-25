"""git-wt subprocess wrapper — harness shells out to git-wt, never imports it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import repos
from .errors import HarnessError, run_cmd


def require_git_wt() -> str:
    if shutil.which("git-wt") is None:
        raise HarnessError("`git-wt` not found on PATH (https://github.com/ynsr/cli-agents-config)")
    return "git-wt"


def start_worktree(repo: Path, branch: str | None = None, issue: str | None = None,
                   slug: str | None = None, link: str | None = None,
                   base: str | None = None, depth: int = 7) -> dict:
    """Create a worktree via `git-wt start --json`. Never fails on existing state.

    If the branch/worktree already exists, falls back to `git-wt start --resume`
    (branch name extracted from git-wt's error message) and continues.
    After creation/resume, verifies the worktree upstream points at the feature
    branch — never the base/default branch — and repairs it if needed.
    Returns parsed result (with an extra `resumed: True` flag on fallback).
    """
    require_git_wt()
    _ = depth  # git-wt owns fetch depth today; kept for CLI compatibility
    if branch:
        _ensure_local_branch(repo, branch)
    args = _start_args(repo, branch, issue, slug, link, base)
    try:
        result = _run_start(args, repo)
    except HarnessError as e:
        existing = _extract_existing_branch(str(e))
        if existing is None:
            raise
        result = _resume_existing(repo, existing, base)
    else:
        _repair_path_mismatch(repo, result, base)
    _ensure_upstream(result, base)
    return result


def _ensure_local_branch(repo: Path, branch: str) -> None:
    """Make sure ``--branch <branch>`` names a local branch git-wt can check out.

    Fetches from origin first so a freshly-pushed PR/MR source branch is
    visible even when the local remote-tracking mirror is stale; a
    remote-only branch is then materialized as a local tracking branch.
    Missing everywhere → actionable error.
    """
    if run_cmd("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}",
               cwd=repo, check=False) is not None:
        return
    run_cmd("git", "fetch", "origin", branch, cwd=repo, check=False)
    if run_cmd("git", "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}",
               cwd=repo, check=False) is not None:
        run_cmd("git", "branch", "--track", branch, f"origin/{branch}", cwd=repo)
        return
    raise HarnessError(
        f"branch {branch!r} not found locally or as origin/{branch} "
        f"even after `git fetch origin {branch}` (repo: {repo})"
    )


def _resume_existing(repo: Path, branch: str, base: str | None) -> dict:
    """Resume an existing branch; prune/recreate when worktree metadata is stale."""
    try:
        result = _run_start(
            ["git-wt", "start", "--json", "--repo", str(repo), "--resume", branch],
            repo,
        )
        result["resumed"] = True
        return result
    except HarnessError:
        pass
    # Stale worktree registration (path removed but git still lists it): prune and retry.
    run_cmd("git", "-C", str(repo), "worktree", "prune", check=False)
    try:
        result = _run_start(
            ["git-wt", "start", "--json", "--repo", str(repo), "--resume", branch],
            repo,
        )
        result["resumed"] = True
        return result
    except HarnessError:
        pass
    # Last resort: branch exists but no usable worktree — recreate in place.
    run_cmd("git", "-C", str(repo), "worktree", "prune", check=False)
    wt_path = _expected_worktree_path(repo, branch)
    if wt_path.exists() and not _is_git_worktree(wt_path):
        import shutil
        shutil.rmtree(wt_path)
    result = _run_start(
        ["git-wt", "start", "--json", "--repo", str(repo), "--resume", branch],
        repo,
    )
    result["resumed"] = True
    return result


def _repair_path_mismatch(repo: Path, result: dict, base: str | None) -> None:
    """Handle create-success where the path on disk isn't a live worktree."""
    wt = result.get("worktree_path")
    if not wt or _is_git_worktree(Path(str(wt))):
        return
    branch = result.get("branch", "")
    run_cmd("git", "-C", str(repo), "worktree", "prune", check=False)
    if branch:
        resumed = _resume_existing(repo, branch, base)
        result.update(resumed)


def _expected_worktree_path(repo: Path, branch: str) -> Path:
    from pathlib import Path as _P
    return _P.home() / "dev" / "worktrees" / repo.resolve().name / branch


def _is_git_worktree(path: Path) -> bool:
    try:
        out = run_cmd("git", "-C", str(path), "rev-parse", "--is-inside-work-tree")
        return (out or "").strip() == "true"
    except HarnessError:
        return False


def _start_args(repo: Path, branch: str | None, issue: str | None, slug: str | None,
                link: str | None, base: str | None) -> list[str]:
    args = ["git-wt", "start", "--json", "--repo", str(repo)]
    if link:
        args += ["--link", link]
    elif branch:
        args += ["--branch", branch]
    else:
        if issue:
            args += ["--issue", issue]
        if slug:
            args += ["--slug", slug]
    if base:
        args += ["--base", base]
    return args


def _run_start(args: list[str], repo: Path) -> dict:
    out = run_cmd(*args, cwd=repo)
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse git-wt output: {out!r}")


def _extract_existing_branch(message: str) -> str | None:
    """Pull the branch name out of git-wt's 'already exists' error."""
    import re
    m = re.search(r"branch '([^']+)' already exists", message)
    if m:
        return m.group(1)
    # `git worktree add` collision variants
    m = re.search(r"'(?:[^']*?/)?([^'/]+)' already (?:used|registered|exists)", message)
    return m.group(1) if m else None


def _ensure_upstream(result: dict, base: str | None) -> None:
    """Guarantee the worktree pushes to its feature branch, never the base.

    git-wt sets this on create, but resumed/recreated worktrees can carry a
    stale upstream (e.g. origin/<base>) or none at all. Detect, repair
    without network, and verify — raising if the upstream still isn't the
    feature branch.
    """
    wt = result.get("worktree_path")
    branch = result.get("branch")
    if not wt or not branch:
        return
    bad = {None, "", base, f"origin/{base}" if base else "\0"}
    upstream = _read_upstream(wt, branch)
    if upstream == f"origin/{branch}":
        return  # correct already
    if upstream not in bad:
        raise HarnessError(
            f"worktree {wt} tracks unexpected upstream '{upstream}' "
            f"(expected origin/{branch}) — refusing to continue",
        )
    run_cmd("git", "-C", str(wt), "update-ref", f"refs/remotes/origin/{branch}", "HEAD")
    # Direct config (not --set-upstream-to): works with no remote, since the
    # tracking ref above points at the worktree HEAD by construction.
    run_cmd("git", "-C", str(wt), "config", f"branch.{branch}.remote", "origin")
    run_cmd("git", "-C", str(wt), "config", f"branch.{branch}.merge", f"refs/heads/{branch}")
    # Verify via config (not @{u}: that shorthand follows the worktree HEAD,
    # not <branch>, and misreads in linked worktrees).
    remote = run_cmd("git", "-C", str(wt), "config", f"branch.{branch}.remote")
    merge = run_cmd("git", "-C", str(wt), "config", f"branch.{branch}.merge")
    if remote != "origin" or merge != f"refs/heads/{branch}":
        raise HarnessError(
            f"failed to set upstream of {wt} to origin/{branch} "
            f"(got {remote}/{merge})",
        )
    result["upstream_fixed"] = True


def _read_upstream(wt: str, branch: str) -> str | None:
    """Read the upstream of <branch> via config (works without a remote)."""
    try:
        remote = run_cmd("git", "-C", str(wt), "config", f"branch.{branch}.remote")
        merge = run_cmd("git", "-C", str(wt), "config", f"branch.{branch}.merge")
    except HarnessError:
        return None
    if not remote or not merge:
        return None
    short = merge.removeprefix("refs/heads/")
    return f"{remote}/{short}" if remote != "." else short


def build_branch_for_issue(parsed_link: dict, title: str) -> tuple[str | None, str | None]:
    """Best-effort branch parts from an issue: (issue_id, slug)."""
    number = parsed_link.get("number", "")
    slug = _slugify(title) if title else ""
    return number or None, slug or None


def _slugify(text: str, max_len: int = 50) -> str:
    import re
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len].strip("-")


def cleanup_worktree(repo: Path, branch: str, delete_branch: bool = True,
                     force: bool = False, yes: bool = False) -> dict:
    args = ["git-wt", "cleanup", "--json", "--repo", str(repo), "--branch", branch]
    if delete_branch:
        args.append("--delete-branch")
    if force:
        args.append("--force")
    if yes:
        args.append("--yes")
    out = run_cmd(*args, cwd=repo)
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse git-wt output: {out!r}")
