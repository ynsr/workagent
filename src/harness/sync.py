"""Branch synchronization engine: local merge, conflict helpers, rebase.

`harness sync` supports two strategies:
  - remote rebase (default): `gh pr update-branch --rebase` / `glab mr rebase`;
    the remote host pushes the rebased branch server-side.
  - local merge: fetch, fast-forward the local default branch, merge it into
    the session branch inside the worktree; push only on request.

Sole-conflict CHANGELOG.md auto-resolve: when every conflicted hunk lives
inside `## Unreleased` on both sides, union the bullet lists.
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import HarnessError, run_cmd


def dirty_files(worktree: Path) -> list[str]:
    """Tracked+untracked paths with pending changes (git status --porcelain)."""
    out = run_cmd("git", "-C", str(worktree), "status", "--porcelain") or ""
    files = []
    for line in out.splitlines():
        if not line.strip():
            continue
        m = re.match(r"\S\S?\s+(.+)", line)  # run_cmd strips the first line's pad
        if m:
            files.append(m.group(1).strip('"'))
    return files


def local_merge(worktree: Path, default_branch: str) -> dict:
    """Merge origin/<default> into the current branch inside *worktree*.

    Returns {"status": "merged"|"up-to-date"|"conflict", "conflicts": [...]}.
    On conflict the merge is left in progress for the caller to resolve.
    """
    run_cmd("git", "-C", str(worktree), "fetch", "origin", default_branch)
    # Fast-forward the local default branch to origin (ignore failure: local
    # default may not exist or may be checked out elsewhere).
    try:
        run_cmd("git", "-C", str(worktree), "fetch", "origin",
                f"{default_branch}:{default_branch}")
    except HarnessError:
        pass
    base = run_cmd("git", "-C", str(worktree), "rev-parse", "--verify", "-q",
                   f"origin/{default_branch}") or default_branch
    try:
        run_cmd("git", "-C", str(worktree), "merge", base)
    except HarnessError:
        conflicts = _conflicted_files(worktree)
        if not conflicts:
            raise
        return {"status": "conflict", "conflicts": conflicts}
    # Up-to-date: merging an already-merged base is a no-op (no new commit).
    head = run_cmd("git", "-C", str(worktree), "rev-parse", "HEAD")
    if base == head:
        return {"status": "up-to-date", "conflicts": []}
    return {"status": "merged", "conflicts": []}


def _last_merge_msg(worktree: Path) -> str:
    try:
        return run_cmd("git", "-C", str(worktree), "log", "-1", "--format=%B")
    except HarnessError:
        return ""


def _conflicted_files(worktree: Path) -> list[str]:
    try:
        out = run_cmd("git", "-C", str(worktree), "diff", "--name-only",
                      "--diff-filter=U")
    except HarnessError:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


_UNRELEASED = re.compile(r"^(#{1,3}\s+.*)$", re.MULTILINE)


def _split_changelog(text: str) -> tuple[list[str], dict[str, list[str]], list[str]]:
    """Split CHANGELOG text into (leading lines, section→bullet lines, trailing).

    Sections are `#`/`##`/`###` headings; bullets are `- ` lines directly
    under them. Any non-bullet line inside a section breaks the parse.
    """
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    prefix: list[str] = []
    for line in lines:
        m = re.match(r"^(#{1,3}\s+.+)$", line)
        if m:
            current = m.group(1)
            order.append(current)
            sections[current] = []
            continue
        if current is None:
            if line.strip():
                prefix.append(line)
            continue
        if line.startswith("- "):
            sections[current].append(line)
        elif line.strip():
            return text, {}, ["non-bullet"]
    return "\n".join(prefix), sections, order


def unreleased_union(ours: str, theirs: str, base: str | None = None) -> str | None:
    """Union CHANGELOGs keeping only-Unreleased differences.

    Returns ours with the Unreleased bullet list replaced by the union of
    both sides (ours order first, deduped). With base=None (default) the
    files must be identical outside that section; with base, changes in
    released sections are allowed as long as OURS didn't make them.
    """
    ours_prefix, ours_sections, ours_order = _split_changelog(ours)
    t_prefix, t_sections, t_order = _split_changelog(theirs)
    if base is None:
        if ours_prefix != t_prefix or ours_order != t_order:
            return None
        if set(ours_sections) != set(t_sections):
            return None
        for name in ours_sections:
            if name == "## Unreleased":
                continue
            if ours_sections[name] != t_sections.get(name):
                return None
        template = ours
    else:
        b_prefix, b_sections, b_order = _split_changelog(base)
        if ours_prefix != b_prefix or ours_order != b_order:
            return None
        if set(ours_sections) != set(b_sections):
            return None
        for name in ours_sections:
            if name == "## Unreleased":
                continue
            if ours_sections[name] != b_sections.get(name):
                return None
        template = theirs
    if "## Unreleased" not in ours_sections or "## Unreleased" not in t_sections:
        return ours if ours == theirs else None
    bullets: list[str] = []
    for b in ours_sections["## Unreleased"] + t_sections["## Unreleased"]:
        if b not in bullets:
            bullets.append(b)
    # Splice the union into the template, preserving its formatting elsewhere.
    lines = template.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "## Unreleased")
    i = start + 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    end = i
    while end < len(lines) and lines[end].startswith("- "):
        end += 1
    return "\n".join(lines[:i] + bullets + lines[end:]) + (
        "\n" if template.endswith("\n") else "")


def auto_resolve_changelog(worktree: Path, conflicts: list[str]) -> bool:
    """Auto-resolve when the sole conflict is CHANGELOG.md inside Unreleased."""
    if conflicts != ["CHANGELOG.md"]:
        return False
    try:
        ours = run_cmd("git", "-C", str(worktree), "show", ":2:CHANGELOG.md")
        theirs = run_cmd("git", "-C", str(worktree), "show", ":3:CHANGELOG.md")
    except HarnessError:
        return False
    try:
        base = run_cmd("git", "-C", str(worktree), "show", ":1:CHANGELOG.md")
    except HarnessError:
        base = None
    merged = unreleased_union(ours, theirs, base=base)
    if merged is not None and not merged.endswith("\n"):
        merged += "\n"
    if merged is None:
        return False
    (worktree / "CHANGELOG.md").write_text(merged, encoding="utf-8")
    run_cmd("git", "-C", str(worktree), "add", "CHANGELOG.md")
    run_cmd("git", "-C", str(worktree), "commit", "--no-edit")
    return True


def push(worktree: Path, branch: str) -> None:
    run_cmd("git", "-C", str(worktree), "push", "origin", branch)


def _is_ancestor(worktree: Path, a: str, b: str) -> bool:
    try:
        run_cmd("git", "-C", str(worktree), "merge-base", "--is-ancestor", a, b)
        return True
    except HarnessError:
        return False


def pull_rebased(worktree: Path, branch: str) -> str:
    """Bring the local branch in line with its server-rebased remote twin.

    A server-side rebase rewrites history, so a plain pull would try to
    re-merge the pre-rebase commits. Fetch, fast-forward when the remote
    branch is a descendant, else hard-reset when every local commit's
    patch-id exists on the rebased branch (equivalent rebased commits);
    otherwise leave the worktree untouched and report.
    """
    run_cmd("git", "-C", str(worktree), "fetch", "origin", branch)
    if _is_ancestor(worktree, "HEAD", f"origin/{branch}"):
        run_cmd("git", "-C", str(worktree), "merge", "--ff-only",
                f"origin/{branch}")
        return "fast-forward"
    fresh = run_cmd("git", "-C", str(worktree), "cherry",
                    f"origin/{branch}", "HEAD") or ""
    if any(line.startswith("+") for line in fresh.splitlines()):
        return "skipped-local-commits"
    run_cmd("git", "-C", str(worktree), "reset", "--hard", f"origin/{branch}")
    return "reset"


def rebase_remote(tool: str, pr: dict, worktree: Path) -> None:
    """Rebase the PR/MR branch on its base — remotely (host pushes server-side)."""
    if tool == "gh":
        run_cmd("gh", "pr", "update-branch", "--rebase", str(pr["url"]), cwd=worktree)
    else:
        run_cmd("glab", "mr", "rebase", str(pr["number"]), cwd=worktree)
