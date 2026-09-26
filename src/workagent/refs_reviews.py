"""PR/MR review-comment stats (split from refs.py; re-exported via refs facade)."""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

from .errors import HarnessError


def run_cmd(*a, **k):
    """run_cmd via the refs namespace so tests patching refs.run_cmd apply."""
    from . import refs as _r
    return _r.run_cmd(*a, **k)
from .refs import _GITHUB_PR, _GITLAB_MR

def _gh_bot_comment_resolved(body: str) -> bool:
    """True when a GitHub bot `# Code Review` comment is marked resolved.

    Plain PR comments carry no resolution state, so by convention (issue #32)
    a bot review comment whose second non-empty line (directly below the
    `# Code Review` header) is exactly `Status: RESOLVED` counts as
    resolved; anything else is unresolved.
    GitLab MRs keep their native resolved flags — this rule is GitHub-only.
    """
    lines = [ln.strip() for ln in str(body or "").splitlines() if ln.strip()]
    return len(lines) >= 2 and lines[1] == "Status: RESOLVED"


def _review_comments_gh(pr_url: str, cwd: str | None) -> dict:
    """{reviews, unresolved, resolved} for a GitHub PR.

    reviews = issue comments whose body starts with `# Code Review`
    (each one marks a completed harness review). Inline reviewThreads carry
    a native isResolved flag; plain (non-inline) bot comments have no
    resolution state, so a bot comment counts as resolved only when its
    second non-empty line (below the header) is exactly `Status: RESOLVED`
    (issue #32, GitHub-only).
    """
    m = _GITHUB_PR.match(pr_url or "")
    if not m:
        raise HarnessError(f"not a GitHub PR URL: {pr_url}")
    out = run_cmd("gh", "pr", "view", pr_url, "--json", "comments",
                  cwd=cwd)
    try:
        comments = json.loads(out or "{}").get("comments") or []
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    bot_comments = [str((c or {}).get("body") or "")
                    for c in comments
                    if str((c or {}).get("body") or "").lstrip().startswith("# Code Review")]
    reviews = len(bot_comments)
    bot_resolved = sum(1 for b in bot_comments if _gh_bot_comment_resolved(b))
    bot_unresolved = reviews - bot_resolved
    owner, name = m.group(1).split("/", 1)
    try:
        number = int(m.group(2))
    except ValueError:
        raise HarnessError(f"not a GitHub PR URL: {pr_url}")
    query = ('{repository(owner:"%s",name:"%s"){pullRequest(number:%d)'
             '{reviewThreads(first:100){nodes{isResolved}}}}}' % (owner, name, number))
    tout = run_cmd("gh", "api", "graphql", "-f", f"query={query}", cwd=cwd)
    try:
        nodes = (json.loads(tout or "{}").get("data", {}).get("repository", {})
                 .get("pullRequest", {}).get("reviewThreads", {}).get("nodes") or [])
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    thread_unresolved = sum(1 for n in nodes if not (n or {}).get("isResolved"))
    thread_resolved = sum(1 for n in nodes if (n or {}).get("isResolved"))
    return {"reviews": reviews,
            "unresolved": thread_unresolved + bot_unresolved,
            "resolved": thread_resolved + bot_resolved}


def _review_comments_glab(pr_url: str, cwd: str | None) -> dict:
    """{reviews, unresolved, resolved} for a GitLab MR via discussions API.

    reviews = notes whose body starts with `# Code Review`. A discussion is
    resolved when every note in it is marked resolved (or the discussion
    itself carries resolved=True).
    """
    m = _GITLAB_MR.match(pr_url or "")
    if not m:
        raise HarnessError(f"not a GitLab MR URL: {pr_url}")
    host, path, iid = m.group(1), m.group(2), m.group(3)
    api = (f"projects/{quote(path, safe='')}/merge_requests/{iid}"
           "/discussions?per_page=100")
    try:
        out = run_cmd("glab", "api", api, "--hostname", host, cwd=cwd)
    except HarnessError:
        out = run_cmd("glab", "api", api, cwd=cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab output for {pr_url}")
    reviews = 0
    unresolved = 0
    resolved = 0
    for disc in data or []:
        notes = (disc or {}).get("notes") or []
        for n in notes:
            if str((n or {}).get("body") or "").lstrip().startswith("# Code Review"):
                reviews += 1
        # Only review threads are resolvable; system/activity notes
        # ("added N commits", "marked as draft", …) are individual
        # discussions GitLab's UI never counts as unresolved.
        resolvable = [n for n in notes if (n or {}).get("resolvable")]
        if not resolvable:
            continue
        if (disc or {}).get("resolved") is True:
            resolved += 1
        elif all((n or {}).get("resolved") for n in resolvable):
            resolved += 1
        else:
            unresolved += 1
    return {"reviews": reviews, "unresolved": unresolved, "resolved": resolved}


def fetch_pr_comment_stats(tool: str, pr_url: str, cwd: str | None = None) -> dict | None:
    """{reviews, unresolved, resolved} for a PR/MR URL.

    Soft-failing like fetch_ci_status: None on any lookup error (warning
    to stderr) so status rendering never breaks on an unhappy host CLI.
    """
    try:
        if tool == "gh":
            return _review_comments_gh(pr_url, cwd)
        if tool == "glab":
            return _review_comments_glab(pr_url, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: review-comment lookup failed for {pr_url}: {e}", file=sys.stderr)
        return None
