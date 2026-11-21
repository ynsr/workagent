"""PR/MR review-comment stats (split from refs.py; re-exported via refs facade)."""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import quote

from .errors import HarnessError


def run_cmd(*a, **k):
    """run_cmd via the refs namespace so tests patching refs.run_cmd apply."""
    from . import refs as _r
    return _r.run_cmd(*a, **k)
from .refs import _GITHUB_PR, _GITLAB_MR
# Case-insensitive `Status:<spaces>RESOLVED` second-line marker.
_STATUS_RESOLVED_RE = re.compile(r"Status:\s+RESOLVED", re.IGNORECASE)


def _bot_comment_resolved(body: str) -> bool:
    """True when a bot `# Code Review` comment is marked resolved.

    Plain PR/MR comments carry no reliable resolution state across hosts, so
    by convention a bot review comment whose second non-empty line (directly
    below the `# Code Review` header) matches `Status:\\s+RESOLVED`
    (case-insensitive) counts as resolved; anything else is unresolved.
    This rule applies to both GitHub and GitLab.
    """
    lines = [ln.strip() for ln in str(body or "").splitlines() if ln.strip()]
    return len(lines) >= 2 and bool(_STATUS_RESOLVED_RE.fullmatch(lines[1]))


# Kept for backward compatibility (tests/patches target this name).
_gh_bot_comment_resolved = _bot_comment_resolved


def _review_comments_gh_details(pr_url: str, cwd: str | None) -> tuple[dict, list[dict]]:
    """({reviews, unresolved, resolved}, threads) for a GitHub PR."""
    m = _GITHUB_PR.match(pr_url or "")
    if not m:
        raise HarnessError(f"not a GitHub PR URL: {pr_url}")
    out = run_cmd("gh", "pr", "view", pr_url, "--json", "comments",
                  cwd=cwd)
    try:
        comments = json.loads(out or "{}").get("comments") or []
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    bot_objs = [c for c in comments
                if isinstance(c, dict)
                and str(c.get("body") or "").lstrip().startswith("# Code Review")]
    reviews = len(bot_objs)
    threads: list[dict] = []
    bot_resolved = 0
    for idx, c in enumerate(bot_objs):
        body = str(c.get("body") or "")
        is_res = _bot_comment_resolved(body)
        if is_res:
            bot_resolved += 1
        author = (c.get("author") or {}).get("login") if isinstance(c.get("author"), dict) else str(c.get("author") or "")
        threads.append({
            "thread_id": str(c.get("id") or f"gh-bot-{idx + 1}"),
            "author": str(author or ""),
            "file": "",
            "line": None,
            "body": body[:2000],
            "resolved": is_res,
            "is_bot": True,
        })
    bot_unresolved = reviews - bot_resolved
    owner, name = m.group(1).split("/", 1)
    try:
        number = int(m.group(2))
    except ValueError:
        raise HarnessError(f"not a GitHub PR URL: {pr_url}")
    query = ('{repository(owner:"%s",name:"%s"){pullRequest(number:%d)'
             '{reviewThreads(first:100){nodes{id isResolved '
             'comments(first:1){nodes{body path line author{login}}}}}}}}'
             % (owner, name, number))
    tout = run_cmd("gh", "api", "graphql", "-f", f"query={query}", cwd=cwd)
    try:
        nodes = (json.loads(tout or "{}").get("data", {}).get("repository", {})
                 .get("pullRequest", {}).get("reviewThreads", {}).get("nodes") or [])
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    thread_unresolved = 0
    thread_resolved = 0
    for idx, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        is_res = bool(n.get("isResolved"))
        if is_res:
            thread_resolved += 1
        else:
            thread_unresolved += 1
        cnodes = ((n.get("comments") or {}).get("nodes") or [])
        first = cnodes[0] if cnodes and isinstance(cnodes[0], dict) else {}
        author = (first.get("author") or {}).get("login") if isinstance(first.get("author"), dict) else ""
        threads.append({
            "thread_id": str(n.get("id") or f"gh-thread-{idx + 1}"),
            "author": str(author or ""),
            "file": str(first.get("path") or ""),
            "line": first.get("line"),
            "body": str(first.get("body") or "")[:2000],
            "resolved": is_res,
            "is_bot": False,
        })
    stats = {"reviews": reviews,
             "unresolved": thread_unresolved + bot_unresolved,
             "resolved": thread_resolved + bot_resolved}
    return stats, threads


def _review_comments_gh(pr_url: str, cwd: str | None) -> dict:
    """{reviews, unresolved, resolved} for a GitHub PR."""
    return _review_comments_gh_details(pr_url, cwd)[0]


def _review_comments_glab_details(pr_url: str, cwd: str | None) -> tuple[dict, list[dict]]:
    """({reviews, unresolved, resolved}, threads) for a GitLab MR via discussions API."""
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
    threads: list[dict] = []
    for idx, disc in enumerate(data or []):
        if not isinstance(disc, dict):
            continue
        notes = disc.get("notes") or []
        bot = [n for n in notes
               if str((n or {}).get("body") or "").lstrip().startswith("# Code Review")]
        reviews += len(bot)
        if bot:
            bot_resolvable = [n for n in bot if (n or {}).get("resolvable")]
            native = disc.get("resolved") is True or all(
                (n or {}).get("resolved") for n in bot_resolvable)
            bodies = [str((n or {}).get("body") or "") for n in bot]
            is_res = bool(native or all(_bot_comment_resolved(b) for b in bodies))
            if is_res:
                resolved += 1
            else:
                unresolved += 1
            first = bot[0] if isinstance(bot[0], dict) else {}
            author = (first.get("author") or {}).get("username") if isinstance(first.get("author"), dict) else ""
            threads.append({
                "thread_id": str(disc.get("id") or f"glab-bot-{idx + 1}"),
                "author": str(author or ""),
                "file": "",
                "line": None,
                "body": str(first.get("body") or "")[:2000],
                "resolved": is_res,
                "is_bot": True,
            })
            continue
        resolvable = [n for n in notes if (n or {}).get("resolvable")]
        if not resolvable:
            continue
        if disc.get("resolved") is True or all((n or {}).get("resolved") for n in resolvable):
            is_res = True
            resolved += 1
        else:
            is_res = False
            unresolved += 1
        first = resolvable[0] if isinstance(resolvable[0], dict) else {}
        author = (first.get("author") or {}).get("username") if isinstance(first.get("author"), dict) else ""
        pos = first.get("position") if isinstance(first.get("position"), dict) else {}
        threads.append({
            "thread_id": str(disc.get("id") or f"glab-thread-{idx + 1}"),
            "author": str(author or ""),
            "file": str(pos.get("new_path") or pos.get("old_path") or ""),
            "line": pos.get("new_line") or pos.get("old_line"),
            "body": str(first.get("body") or "")[:2000],
            "resolved": is_res,
            "is_bot": False,
        })
    return {"reviews": reviews, "unresolved": unresolved, "resolved": resolved}, threads


def _review_comments_glab(pr_url: str, cwd: str | None) -> dict:
    """{reviews, unresolved, resolved} for a GitLab MR via discussions API."""
    return _review_comments_glab_details(pr_url, cwd)[0]


def fetch_pr_review_details(tool: str, pr_url: str,
                            cwd: str | None = None) -> tuple[dict | None, list[dict]]:
    """({reviews, unresolved, resolved} | None, threads) for a PR/MR URL.

    Soft-failing like fetch_pr_comment_stats: (None, []) on any lookup error.
    """
    try:
        if tool == "gh":
            return _review_comments_gh_details(pr_url, cwd)
        if tool == "glab":
            return _review_comments_glab_details(pr_url, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: review-comment lookup failed for {pr_url}: {e}", file=sys.stderr)
        return None, []


def fetch_pr_comment_stats(tool: str, pr_url: str, cwd: str | None = None) -> dict | None:
    """{reviews, unresolved, resolved} for a PR/MR URL.

    Soft-failing like fetch_ci_status: None on any lookup error (warning
    to stderr) so status rendering never breaks on an unhappy host CLI.
    """
    return fetch_pr_review_details(tool, pr_url, cwd)[0]
