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
    """Create a worktree via `git-wt start --json`. Returns parsed result."""
    require_git_wt()
    _ = depth  # git-wt owns fetch depth today; kept for CLI compatibility
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
    out = run_cmd(*args, cwd=repo)
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse git-wt output: {out!r}")


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
