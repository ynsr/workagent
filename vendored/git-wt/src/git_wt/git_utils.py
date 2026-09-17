"""Low-level git operations, repo detection, host CLI interaction."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


class GitWtError(Exception):
    """Base exception for all git-wt errors."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class GitRepo:
    """Encapsulates a git repo path and provides query methods."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path or os.getcwd()).resolve()
        self._root: Path | None = None

    # ── repo discovery ──────────────────────────────────────────────

    @property
    def root(self) -> Path:
        if self._root is not None:
            return self._root
        result = _run_git(self._path, "rev-parse", "--show-toplevel")
        if result is None:
            raise GitWtError(
                f"not inside a git repo: {self._path}  (pass --repo or cd into one)",
                exit_code=2,
            )
        self._root = Path(result.strip())
        return self._root

    @property
    def name(self) -> str:
        return self.root.name

    def current_branch(self) -> str:
        result = _run_git(self._path, "rev-parse", "--abbrev-ref", "HEAD")
        if result is None:
            raise GitWtError("could not determine current branch")
        return result.strip()

    # ── default branch ──────────────────────────────────────────────

    def default_branch(self, *, use_remote: bool = True) -> str | None:
        """Detect the repo's default branch. Checks gh/glab first, then origin/HEAD."""
        cli = detect_host_cli(self.root)
        branch: str | None = None

        if cli == "gh":
            branch = _run_cmd("gh", "repo", "view", "--json", "defaultBranchRef",
                              "-q", ".defaultBranchRef.name", cwd=self.root)
            if branch:
                branch = branch.strip()

        elif cli == "glab":
            out = _run_cmd("glab", "repo", "view", "-F", "json", cwd=self.root)
            if out:
                m = re.search(r'"default_branch"\s*:\s*"([^"]+)"', out)
                if m:
                    branch = m.group(1)

        if not branch and use_remote:
            _run_git(self._path, "fetch", "origin", check=False)
            out = _run_git(self._path, "symbolic-ref", "refs/remotes/origin/HEAD",
                           check=False)
            if out:
                branch = out.strip().replace("refs/remotes/origin/", "")

        return branch or None

    # ── branch checks ───────────────────────────────────────────────

    def branch_exists(self, name: str, *, remote: bool = False) -> bool:
        ref = f"refs/remotes/origin/{name}" if remote else f"refs/heads/{name}"
        out = _run_git(self._path, "show-ref", "--verify", "--quiet", ref,
                       check=False)
        return out is not None

    def is_protected(self, name: str) -> bool:
        return name.lower() in ("main", "master", "develop", "staging", "production")

    # ── fetch ───────────────────────────────────────────────────────

    def fetch(self, ref: str | None = None) -> None:
        args = ["fetch", "origin"]
        if ref:
            args.append(ref)
        _run_git(self._path, *args)

    # ── worktree query ──────────────────────────────────────────────

    def worktree_path_for_branch(self, branch: str) -> str | None:
        """Return the worktree path for a given branch, or None."""
        out = _run_git(self._path, "worktree", "list", "--porcelain", check=False)
        if not out:
            return None
        lines = out.strip().splitlines()
        current_path = None
        for line in lines:
            if line.startswith("worktree "):
                current_path = line[len("worktree "):]
            elif line == f"branch refs/heads/{branch}":
                return current_path
        return None

    def worktree_paths(self) -> list[dict]:
        """List all worktrees with metadata."""
        out = _run_git(self._path, "worktree", "list", "--porcelain", check=False)
        if not out:
            return []
        result: list[dict] = []
        current: dict = {}
        for line in out.strip().splitlines():
            if line.startswith("worktree "):
                current = {"path": line[len("worktree "):], "branch": None, "head": None, "bare": False, "detached": False}
            elif line.startswith("branch refs/heads/"):
                current["branch"] = line[len("branch refs/heads/"):]
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True
            elif line == "" and current:
                result.append(current)
                current = {}
        if current:
            result.append(current)
        return result

    # ── dirty check ─────────────────────────────────────────────────

    def is_dirty(self, worktree_path: str | Path | None = None) -> bool:
        target = Path(worktree_path) if worktree_path else self._path
        out = _run_git(target, "status", "--porcelain", check=False)
        return bool(out and out.strip())

    def stash(self, worktree_path: str | Path | None = None, message: str = "") -> None:
        target = Path(worktree_path) if worktree_path else self._path
        msg = message or "auto-stash before base update"
        _run_git(target, "stash", "push", "-u", "-m", msg)

    def commit_worktree(self, worktree_path: str | Path | None = None,
                        message: str = "", push: bool = False) -> None:
        target = Path(worktree_path) if worktree_path else self._path
        msg = message or "WIP before base update"
        _run_git(target, "add", "-A")
        _run_git(target, "commit", "-m", msg)
        if push:
            branch = _run_git(target, "rev-parse", "--abbrev-ref", "HEAD")
            if branch:
                _run_git(target, "push", "-u", "origin", branch.strip())


# ── subprocess helpers ──────────────────────────────────────────


def _run_git(cwd: str | Path, *args: str, check: bool = True) -> str | None:
    """Run a git command and return stdout, or None on failure."""
    return _run_cmd("git", *args, cwd=cwd, check=check)


def _run_cmd(name: str, *args: str, cwd: str | Path | None = None,
             check: bool = True, timeout: int = 120) -> str | None:
    """Run an external command, return stdout or None."""
    try:
        result = subprocess.run(
            [name, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        if check:
            raise GitWtError(f"command not found: {name}")
        return None
    except subprocess.TimeoutExpired:
        if check:
            raise GitWtError(f"command timed out after {timeout}s: {name} {' '.join(args)}")
        return None

    if result.returncode != 0:
        if check:
            msg = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
            raise GitWtError(f"{name} failed: {msg}")
        return None

    return result.stdout


def has_command(name: str) -> bool:
    """Check if a command exists on PATH."""
    import shutil
    return shutil.which(name) is not None


def detect_host_cli(repo_root: str | Path | None = None) -> str | None:
    """Detect which host CLI (gh/glab) is authenticated for this repo."""
    if has_command("gh"):
        out = _run_cmd("gh", "repo", "view", cwd=repo_root, check=False)
        if out is not None:
            return "gh"
    if has_command("glab"):
        out = _run_cmd("glab", "repo", "view", cwd=repo_root, check=False)
        if out is not None:
            return "glab"
    return None


def _parse_issue_id(branch: str) -> str | None:
    """Extract the first numeric ID from a branch name like 'feat/123-fix'."""
    m = re.search(r"[0-9]+", branch)
    return m.group(0) if m else None