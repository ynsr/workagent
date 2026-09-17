"""Detect issue tracker from URL, fetch title, and parse into branch components.

Supports:
  - Jira (jira-cli)   — tribe.jibit.cloud/browse/IPG-930, *.atlassian.net/browse/...
  - GitHub (gh)       — github.com/OWNER/REPO/issues/22
  - GitLab (glab)     — git.jibit.cloud/.../issues/430, gitlab.com/.../issues/...
  - Todoist (td)      — todoist.com/showTask?id=xxx, todoist.com/app/task/xxx
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from .git_utils import GitWtError, _run_cmd

# ── URL detection ─────────────────────────────────────────────────────


def detect_tool_from_link(link: str) -> str:
    """Return the CLI tool name ('gh', 'glab', 'jira-cli', 'td') for a link."""
    parsed = urlparse(link)
    host = parsed.netloc.lower()
    path = parsed.path

    # Jira
    if "/browse/" in path and ("jibit" in host or "atlassian" in host):
        return "jira-cli"

    # GitHub
    if host.endswith("github.com") and "/issues/" in path:
        return "gh"

    # GitLab — any host with /issues/ in path (or /-/issues/ for gitlab.com)
    if "/issues/" in path:
        return "glab"

    # Todoist
    if "todoist" in host:
        return "td"

    raise GitWtError(
        f"cannot determine issue tracker from link: {link}\n"
        "  Supported: Jira, GitHub, GitLab, Todoist"
    )


def extract_issue_id(link: str, tool: str) -> str:
    """Extract the raw issue/task identifier from a link."""
    parsed = urlparse(link)
    path = parsed.path
    query = parsed.query

    if tool == "jira-cli":
        # /browse/IPG-930
        m = re.search(r"/browse/([A-Z]+-\d+)", path)
        if m:
            return m.group(1)
        raise GitWtError(f"cannot extract Jira issue key from: {link}")

    if tool == "gh":
        # github.com/OWNER/REPO/issues/22
        m = re.search(r"/issues/(\d+)", path)
        if m:
            return m.group(1)
        raise GitWtError(f"cannot extract GitHub issue number from: {link}")

    if tool == "glab":
        # gitlab.com/.../issues/430  or  git.jibit.cloud/.../issues/430
        m = re.search(r"/issues/(\d+)", path)
        if m:
            return m.group(1)
        raise GitWtError(f"cannot extract GitLab issue number from: {link}")

    if tool == "td":
        # todoist.com/showTask?id=xxx  or  todoist.com/app/task/xxx
        m = re.search(r"id=([a-zA-Z0-9_]+)", query)
        if m:
            return m.group(1)
        m = re.search(r"/app/task/([a-zA-Z0-9_]+)", path)
        if m:
            return m.group(1)
        raise GitWtError(f"cannot extract Todoist task id from: {link}")

    raise GitWtError(f"unknown tool: {tool}")


# ── title fetching ────────────────────────────────────────────────────


def fetch_issue_title(link: str, tool: str) -> str:
    """Fetch the issue/task title from the tracker via the detected CLI tool.

    Returns the raw title as shown by the tool (e.g. 'Feat: Add login').
    Raises GitWtError on failure.
    """
    if tool == "jira-cli":
        issue_id = extract_issue_id(link, tool)
        out = _run_cmd("jira-cli", "issue", issue_id, check=True, timeout=30)
        return _parse_jira_title(out) if out else ""

    if tool == "gh":
        out = _run_cmd("gh", "issue", "view", link, "--json", "title",
                       check=True, timeout=30)
        if out:
            try:
                data = json.loads(out)
                return data.get("title", "")
            except json.JSONDecodeError:
                pass
        return ""

    if tool == "glab":
        out = _run_cmd("glab", "issue", "view", link, check=False, timeout=30)
        if out and "ERROR" not in out.upper():
            return _parse_glab_title(out)
        # Try with --repo flag as fallback
        parsed = urlparse(link)
        path = parsed.path
        # Extract OWNER/REPO from path: /OWNER/REPO/-/issues/NUM
        m = re.match(r"/([^/]+/[^/]+)/-/issues/\d+", path)
        if m:
            repo_ref = m.group(1)
            issue_id = extract_issue_id(link, tool)
            out2 = _run_cmd("glab", "issue", "view", issue_id, "-R", repo_ref,
                            check=False, timeout=30)
            if out2 and "ERROR" not in out2.upper():
                return _parse_glab_title(out2)
        raise GitWtError(f"failed to fetch GitLab issue title from: {link}")

    if tool == "td":
        task_id = extract_issue_id(link, tool)
        out = _run_cmd("td", "task", "view", task_id, "--json",
                       check=True, timeout=30)
        if out:
            try:
                data = json.loads(out)
                return data.get("content", "")
            except json.JSONDecodeError:
                pass
        return ""

    raise GitWtError(f"unknown tool: {tool}")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)


def _parse_jira_title(output: str) -> str:
    """Extract the title from jira-cli issue output.

    Output format (may contain ANSI escapes):
      ============================================================
        IPG-930  [PPG] Improve Feature Flag Module ...
      ============================================================
    """
    clean = _strip_ansi(output)
    for line in clean.split("\n"):
        m = re.match(r"^\s{2}([A-Z]+-\d+)\s{2}(.+)$", line)
        if m:
            return m.group(2).strip()
    return ""


def _parse_glab_title(output: str) -> str:
    """Extract the title from glab issue view output.

    First non-empty content line is usually the title.
    Strips ANSI codes before parsing.
    """
    clean = _strip_ansi(output)
    lines = clean.strip().split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped and not all(c in "=- " for c in stripped):
            return stripped
    return ""


# ── title-to-branch parsing ───────────────────────────────────────────


# Map full type names to short branch prefix
TYPE_SHORT_MAP = {
    "feat": "feat",
    "fix": "fix",
    "chore": "chor",
    "docs": "docs",
    "refactor": "refactor",
}

# Full type names (both exact and with colon) for detection
TYPE_PATTERNS = re.compile(
    r"^(feat|fix|chore|docs|refactor)\s*:\s*",
    re.IGNORECASE,
)

# Tag pattern: [Anything] at start of title
TAG_PATTERN = re.compile(r"^\[[\w\s]+\]\s*")


def parse_title_to_branch_components(title: str, issue_id: str) -> dict:
    """Parse a task/issue title into branch name components.

    Steps:
      1. Strip leading [tag] prefixes like [PPG] or [Saman]
      2. Detect type prefix (feat:, fix:, chore:, docs:, refactor:)
      3. Slugify the remaining text
      4. Map type to short form (chore -> chor)

    Returns dict with keys: type_, issue, slug
    """
    if not title:
        return {"type_": "feat", "issue": issue_id, "slug": "untitled"}

    # Strip [tag] prefixes
    clean = TAG_PATTERN.sub("", title).strip()
    if not clean:
        clean = title

    # Detect type
    type_ = "feat"
    m = TYPE_PATTERNS.match(clean)
    if m:
        type_ = m.group(1).lower()
        clean = clean[m.end():].strip()

    # Map to short form
    type_short = TYPE_SHORT_MAP.get(type_, type_)

    # Slugify
    slug = _slugify(clean)

    return {
        "type_": type_short,
        "issue": issue_id,
        "slug": slug,
    }


def _slugify(text: str, max_len: int = 60) -> str:
    """Convert free text to a slug suitable for branch names."""
    # Strip non-ASCII/non-alphanumeric (keep ASCII letters, digits, spaces, hyphens)
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.ASCII)
    # Collapse whitespace/underscores to hyphens
    slug = re.sub(r"[\s_]+", "-", slug)
    # Lowercase
    slug = slug.lower().strip("-")
    # Collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    # Truncate
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug