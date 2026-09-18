"""Worktree creation, removal, and branch naming."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .git_utils import GitRepo, GitWtError, _run_git
from .link_utils import (
    detect_tool_from_link,
    extract_issue_id,
    fetch_issue_title,
    parse_title_to_branch_components,
)

_INVALID_BRANCH_CHARS = re.compile(r'[^a-zA-Z0-9._/-]')
_COLLAPSE_DASHES = re.compile(r'-{2,}')


def _sanitize_branch_part(part: str) -> str:
    """Replace characters invalid for git ref names with '-'.

    Keeps letters, digits, dots, underscores, slashes, and dashes.
    Collapses consecutive dashes and strips leading/trailing dashes/dots.
    Never returns empty.
    """
    sanitized = _INVALID_BRANCH_CHARS.sub("-", part)
    sanitized = _COLLAPSE_DASHES.sub("-", sanitized)
    sanitized = sanitized.strip(".-")
    return sanitized or "unnamed"


def build_branch_name(
    branch: str | None = None,
    type_: str | None = None,
    issue: str | None = None,
    slug: str | None = None,
) -> str:
    """Build a branch name from components, or return the explicit name.

    Priority: explicit --branch wins. Otherwise combine --type/--issue/--slug
    with `--` between issue and slug (e.g. feat/123--add-login).

    Each component is sanitized to replace characters invalid for git ref names.
    """
    if branch:
        return branch
    parts: list[str] = []
    if type_:
        parts.append(_sanitize_branch_part(type_))
    if issue:
        parts.append(_sanitize_branch_part(issue))
    suffix = _sanitize_branch_part(slug) if slug else ""
    if suffix:
        joined = "/".join(parts) if parts else ""
        if joined:
            return f"{joined}--{suffix}"
        return suffix
    return "/".join(parts)


def resolve_worktree_dir(
    repo: GitRepo,
    branch: str,
    ephemeral: bool = False,
) -> Path:
    """Return the worktree directory for a branch.

    Persistent: ~/dev/worktrees/<repo-name>/<branch>
    Ephemeral:  temp dir (caller must clean up)
    """
    if ephemeral:
        safe_prefix = branch.replace("/", "-").replace("\\", "-")
        return Path(tempfile.mkdtemp(prefix=f"wt-{safe_prefix}-"))
    return Path.home() / "dev" / "worktrees" / repo.name / branch


def start_task(
    repo_path: str | Path | None = None,
    *,
    branch: str | None = None,
    type_: str | None = None,
    issue: str | None = None,
    slug: str | None = None,
    link: str | None = None,
    base: str | None = None,
    ephemeral: bool = False,
    resume: str | None = None,
    on_dirty: str | None = None,
    force: bool = False,
) -> dict:
    """Create or resume a worktree for a task.

    If *link* is provided, it auto-detects the issue tracker (gh/glab/jira-cli/td),
    fetches the issue title, and derives type_/issue/slug from it.
    Explicit --type/--issue/--slug override link-derived values.

    Returns a dict with keys: worktree_path, branch, base, action.
    """
    repo = GitRepo(repo_path)
    repo.root  # validate we're in a repo

    # ── resume path ─────────────────────────────────────────────────
    if resume:
        branch_name = resume
        wt_path = resolve_worktree_dir(repo, branch_name, ephemeral)

        # Check if worktree already exists
        existing = repo.worktree_path_for_branch(branch_name)
        if existing:
            return {
                "worktree_path": Path(existing),
                "branch": branch_name,
                "base": base or "",
                "action": "resumed",
            }

        # Recreate from remote
        repo.fetch(f"{branch_name}:{branch_name}")
        if not repo.branch_exists(branch_name):
            raise GitWtError(
                f"branch '{branch_name}' not found locally or on origin — cannot resume"
            )

        wt_path.parent.mkdir(parents=True, exist_ok=True)
        _run_git(repo.root, "worktree", "add", str(wt_path), branch_name)
        return {
            "worktree_path": wt_path,
            "branch": branch_name,
            "base": base or "",
            "action": "recreated",
        }

    # ── resolve from link ─────────────────────────────────────────
    if link and not resume:
        tool = detect_tool_from_link(link)
        issue_id = extract_issue_id(link, tool)
        title = fetch_issue_title(link, tool)
        if not title:
            raise GitWtError(f"failed to fetch issue title from: {link}")
        parsed = parse_title_to_branch_components(title, issue_id)
        # Explicit flags override link-derived values
        type_ = type_ or parsed["type_"]
        issue = issue or parsed["issue"]
        slug = slug or parsed["slug"]

    # ── new task ─────────────────────────────────────────────────────
    branch_name = build_branch_name(branch, type_, issue, slug)
    if not branch_name:
        raise GitWtError(
            "provide --branch, or --type/--issue/--slug combination, or --resume",
            exit_code=2,
        )

    # Resolve base branch
    if not base:
        detected = repo.default_branch()
        if not detected:
            raise GitWtError(
                "could not detect default branch — supply --base explicitly",
                exit_code=2,
            )
        base = detected

    # Check if branch already exists locally
    if repo.branch_exists(branch_name):
        if force:
            repo.fetch(f":{branch_name}")
        else:
            raise GitWtError(
                f"branch '{branch_name}' already exists locally "
                f"— use --resume {branch_name} or --force to re-create"
            )

    # Handle dirty base if it's checked out somewhere
    _handle_dirty_base(repo, base, on_dirty)

    # Fetch base
    repo.fetch(base)

    # Create worktree
    wt_path = resolve_worktree_dir(repo, branch_name, ephemeral)
    wt_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _run_git(repo.root, "worktree", "add", "-b", branch_name, str(wt_path),
                 f"origin/{base}")
    except GitWtError as e:
        # Clean up empty dir on failure
        if wt_path.exists() and not any(wt_path.iterdir()):
            wt_path.rmdir()
        raise

    # Set upstream to the feature branch on origin (no network call).
    # Creates a local remote-tracking ref matching the current HEAD,
    # so git push goes to the feature branch, not the base branch.
    _run_git(wt_path, "update-ref", f"refs/remotes/origin/{branch_name}", "HEAD")
    _run_git(wt_path, "branch", "--set-upstream-to", f"origin/{branch_name}")

    return {
        "worktree_path": wt_path,
        "branch": branch_name,
        "base": base,
        "action": "created",
    }


def cleanup_task(
    repo_path: str | Path | None = None,
    *,
    branch: str,
    force: bool = False,
    delete_branch: bool = False,
    confirm: bool | Callable[[str], bool] | None = None,
) -> dict:
    """Remove a worktree and optionally delete its branch.

    PR-state gate: when the PR/MR is open and ``force`` is unset,
    ``confirm`` decides — True: pre-confirmed (bypass), a callable: asked
    (TTY only), None/False: fail with an actionable message (never blocks
    a non-TTY run).

    Returns a dict with keys: worktree_path, branch, actions (list of strings).
    """
    repo = GitRepo(repo_path)
    repo.root
    actions: list[str] = []

    wt_path_str = repo.worktree_path_for_branch(branch)

    if wt_path_str:
        wt_path = Path(wt_path_str)
        # Check PR state unless forced
        if not force:
            state = _check_pr_state(repo.root, branch)
            if state and state not in ("MERGED", "CLOSED", "closed", "merged"):
                if confirm is True:
                    pass
                elif callable(confirm):
                    if not confirm(f"PR/MR for '{branch}' is {state} — remove the worktree anyway?"):
                        raise GitWtError("aborted — worktree not removed")
                else:
                    raise GitWtError(
                        f"PR/MR for '{branch}' is {state} — use --force (or --yes) to remove anyway"
                    )

        try:
            _run_git(repo.root, "worktree", "remove", wt_path_str)
        except GitWtError:
            _run_git(repo.root, "worktree", "remove", "--force", wt_path_str)

        actions.append(f"removed worktree {wt_path_str}")
    else:
        # Check if there's a stale directory
        stale = resolve_worktree_dir(repo, branch)
        if stale.exists():
            if not force:
                raise GitWtError(
                    f"stale worktree directory exists at {stale} "
                    f"but no git worktree record — use --force to remove"
                )
            shutil.rmtree(stale)
            actions.append(f"removed stale directory {stale}")

    if delete_branch:
        try:
            _run_git(repo.root, "branch", "-D", branch)
            actions.append(f"deleted local branch {branch}")
        except GitWtError:
            pass
        try:
            _run_git(repo.root, "push", "origin", "--delete", branch)
            actions.append(f"deleted remote branch origin/{branch}")
        except GitWtError:
            pass

    return {
        "worktree_path": wt_path_str,
        "branch": branch,
        "actions": actions,
    }


def finish_task(
    worktree_path: str | Path,
    *,
    base: str | None = None,
    title: str | None = None,
    skip_tests: bool = False,
) -> dict:
    """Run tests (if any), push branch, and create a draft PR/MR.

    Returns a dict with keys: branch, pushed, pr_url, tests_ran.
    """
    wt = Path(worktree_path).resolve()
    repo = GitRepo(wt)
    repo.root

    branch = repo.current_branch()

    # Resolve base
    if not base:
        detected = repo.default_branch()
        if not detected:
            raise GitWtError(
                "could not detect default branch — supply --base explicitly",
                exit_code=2,
            )
        base = detected

    # Run tests
    test_results = None
    if not skip_tests:
        test_results = _run_tests(wt)

    # Push
    _run_git(wt, "push", "-u", "origin", branch)

    # Create PR/MR
    cli = _detect_host_cli_or_none(repo.root)
    issue_id = _parse_issue_id(branch)
    pr_title = title or _title_from_branch(branch)
    body = f"Automated PR for branch `{branch}`."
    if issue_id:
        body += f"\n\nCloses #{issue_id}"

    pr_url: str | None = None
    if cli == "gh":
        out = _run_git_gh_or_glab("gh", repo.root, "pr", "create",
                                   "--base", base,
                                   "--head", branch,
                                   "--title", pr_title,
                                   "--body", body,
                                   "--draft")
        if out:
            # Extract URL from output
            import re
            urls = re.findall(r"https://[^\s]+", out)
            pr_url = urls[-1] if urls else "created"
    elif cli == "glab":
        out = _run_git_gh_or_glab("glab", repo.root, "mr", "create",
                                   "--target-branch", base,
                                   "--source-branch", branch,
                                   "--title", pr_title,
                                   "--description", body,
                                   "--draft")
        if out:
            import re
            urls = re.findall(r"https://[^\s]+", out)
            pr_url = urls[-1] if urls else "created"
    else:
        pr_url = "none"

    return {
        "branch": branch,
        "pushed": True,
        "pr_url": pr_url or "none",
        "tests_ran": test_results,
    }


# ── private helpers ─────────────────────────────────────────────────


def _handle_dirty_base(repo: GitRepo, base: str, on_dirty: str | None) -> None:
    """If base is checked out somewhere and dirty, resolve before we fetch."""
    wt_path = repo.worktree_path_for_branch(base)
    if not wt_path:
        return
    if not repo.is_dirty(wt_path):
        return

    choice = on_dirty
    if choice is None:
        raise GitWtError(
            f"'{base}' is checked out at {wt_path} with uncommitted changes.\n"
            "  Pass --on-dirty <stash|commit|push|ignore> to handle it automatically.",
            exit_code=2,
        )

    if choice == "stash":
        repo.stash(wt_path)
    elif choice == "commit":
        repo.commit_worktree(wt_path)
    elif choice == "push":
        repo.commit_worktree(wt_path, push=True)
    elif choice == "ignore":
        pass
    else:
        raise GitWtError(f"unknown --on-dirty choice: {choice}")


def _check_pr_state(repo_root: Path, branch: str) -> str | None:
    """Check PR/MR state via gh or glab. Returns None if not found."""
    cli = _detect_host_cli_or_none(repo_root)
    if cli == "gh":
        out = _run_git_gh_or_glab("gh", repo_root, "pr", "view", branch,
                                   "--json", "state", "-q", ".state",
                                   check=False)
        return out.strip() if out else None
    elif cli == "glab":
        out = _run_git_gh_or_glab("glab", repo_root, "mr", "view", branch,
                                   "-F", "json", check=False)
        if out:
            import re
            m = re.search(r'"state"\s*:\s*"([^"]+)"', out)
            return m.group(1) if m else None
    return None


def _run_tests(worktree_path: Path) -> dict | None:
    """Run known test runners and return results summary.

    Returns dict with success, runner, or None if no test runner detected.
    """
    results: dict | None = None

    # Check for project file markers
    has = {}
    for p in worktree_path.iterdir():
        name = p.name
        has[name] = True

    if "pom.xml" in has:
        results = _run_test_cmd(worktree_path, "mvn -q test", "maven")
    elif has.get("build.gradle") or has.get("build.gradle.kts"):
        results = _run_test_cmd(worktree_path, "./gradlew test", "gradle")
    elif "go.mod" in has:
        results = _run_test_cmd(worktree_path, "go test ./...", "go")
    elif "Cargo.toml" in has:
        results = _run_test_cmd(worktree_path, "cargo test", "cargo")
    elif "pyproject.toml" in has or "setup.py" in has or "requirements.txt" in has:
        results = _run_test_cmd(worktree_path, "pytest", "python")
    elif "package.json" in has:
        results = _run_test_cmd(worktree_path, "npm test", "node")

    return results


def _run_test_cmd(worktree_path: Path, cmd_str: str, runner: str) -> dict:
    """Run a test command and return results."""
    import subprocess
    import shlex

    try:
        result = subprocess.run(
            shlex.split(cmd_str),
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except FileNotFoundError:
        return {"runner": runner, "success": False, "error": f"command not found: {cmd_str.split()[0]}"}
    except subprocess.TimeoutExpired:
        return {"runner": runner, "success": False, "error": "timed out after 600s"}

    return {
        "runner": runner,
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _detect_host_cli_or_none(repo_root: Path) -> str | None:
    """Return 'gh' or 'glab' if available and authenticated for this repo."""
    from .git_utils import detect_host_cli
    return detect_host_cli(repo_root)


def _parse_issue_id(branch: str) -> str | None:
    from .git_utils import _parse_issue_id
    return _parse_issue_id(branch)


def _title_from_branch(branch: str) -> str:
    """Convert a branch name to a human-readable title."""
    # Strip type prefix, replace hyphens/underscores with spaces, capitalize
    name = branch.split("/", 1)[-1] if "/" in branch else branch
    name = name.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split())


def _run_git_gh_or_glab(
    cli: str, cwd: Path, *args: str, check: bool = True
) -> str | None:
    """Run gh or glab command and return stdout."""
    from .git_utils import _run_cmd
    return _run_cmd(cli, *args, cwd=cwd, check=check, timeout=60)