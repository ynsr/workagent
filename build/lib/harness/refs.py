"""Issue/PR link parsing and fetching via gh/glab.

Supported inputs:
  - GitHub issue URL:  https://github.com/OWNER/REPO/issues/22
  - GitHub PR URL:     https://github.com/OWNER/REPO/pull/33
  - GitLab issue URL:  https://<host>/GROUP/REPO/-/issues/430
  - GitLab MR URL:     https://<host>/GROUP/REPO/-/merge_requests/55
  - Shorthand:         OWNER/REPO#22 (GitHub issue/PR number)
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from .errors import HarnessError, run_cmd

_GITHUB_ISSUE = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/issues/(\d+)/*$")
_GITHUB_PR = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/pull/(\d+)/*$")
_GITLAB_ISSUE = re.compile(r"^https?://([^/]+)/(.+)/-/issues/(\d+)/*$")
_GITLAB_MR = re.compile(r"^https?://([^/]+)/(.+)/-/merge_requests/(\d+)/*$")
_SHORTHAND = re.compile(r"^([^/\s]+/[^/\s#]+)#(\d+)$")


def parse_ref(ref: str) -> dict:
    """Parse an issue/PR ref into {kind, tool, repo, number, url}."""
    ref = ref.strip()
    m = _GITHUB_ISSUE.match(ref)
    if m:
        return {"kind": "issue", "tool": "gh", "repo": m.group(1), "number": m.group(2), "url": ref}
    m = _GITHUB_PR.match(ref)
    if m:
        return {"kind": "pr", "tool": "gh", "repo": m.group(1), "number": m.group(2), "url": ref}
    m = _GITLAB_ISSUE.match(ref)
    if m:
        return {"kind": "issue", "tool": "glab", "repo": m.group(2), "number": m.group(3), "url": ref}
    m = _GITLAB_MR.match(ref)
    if m:
        return {"kind": "mr", "tool": "glab", "repo": m.group(2), "number": m.group(3), "url": ref}
    m = _SHORTHAND.match(ref)
    if m:
        return {"kind": "issue_or_pr", "tool": "gh", "repo": m.group(1), "number": m.group(2), "url": ref}
    # Bare number: GitHub issue/PR in cwd repo context
    if re.fullmatch(r"\d+", ref):
        return {"kind": "issue_or_pr", "tool": "gh", "repo": "", "number": ref, "url": ref}
    raise HarnessError(
        f"cannot parse issue/PR ref: {ref}\n"
        "  Supported: GitHub issue/PR URL, GitLab issue/MR URL, OWNER/REPO#NUM, or a bare number",
        exit_code=2,
    )


def issue_key(parsed: dict) -> str:
    """Stable key for links.json, e.g. 'github:owner/repo#22'."""
    host = "gitlab" if parsed["tool"] == "glab" else "github"
    repo = parsed["repo"] or "local"
    return f"{host}:{repo}#{parsed['number']}"


def fetch_issue(parsed: dict) -> dict:
    """Return {title, body} for an issue ref via gh/glab."""
    if parsed["tool"] == "gh":
        target = parsed["url"] if parsed["repo"] else parsed["number"]
        args = ["gh", "issue", "view", target, "--json", "title,body"]
        if parsed["repo"] and parsed["kind"] == "issue_or_pr":
            args = ["gh", "issue", "view", parsed["number"], "--repo", parsed["repo"],
                    "--json", "title,body"]
        out = run_cmd(*args)
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise HarnessError(f"cannot parse gh output for {parsed['url']}")
        return {"title": data.get("title", ""), "body": data.get("body", "") or ""}
    # glab
    target = parsed["url"] if parsed["repo"] else parsed["number"]
    out = run_cmd("glab", "issue", "view", target, "-F", "json")
    try:
        data = json.loads(out or "{}")
        if isinstance(data, list):
            data = data[0] if data else {}
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab output for {parsed['url']}")
    return {"title": data.get("title", ""), "body": data.get("description", "") or ""}


def fetch_pr_info(parsed: dict) -> dict:
    """Return {title, head_ref, issue_key} for a PR/MR ref (best effort)."""
    if parsed["tool"] == "gh":
        out = run_cmd("gh", "pr", "view", parsed["url"], "--json", "title,headRefName,body")
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise HarnessError(f"cannot parse gh output for {parsed['url']}")
        return {"title": data.get("title", ""),
                "head_ref": data.get("headRefName", ""),
                "body": data.get("body", "") or ""}
    out = run_cmd("glab", "mr", "view", parsed["url"], "-F", "json")
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab output for {parsed['url']}")
    return {"title": data.get("title", ""),
            "head_ref": data.get("source_branch", ""),
            "body": data.get("description", "") or ""}


def hostname(url: str) -> str:
    return urlparse(url).netloc.lower()
