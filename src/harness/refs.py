"""Issue/PR link parsing and fetching via jira-cli/gh/glab.

Supported inputs:
  - Jira key:          IPG-980
  - Jira URL:          https://tribe.jibit.cloud/browse/IPG-980
  - GitHub issue URL:  https://github.com/OWNER/REPO/issues/22
  - GitHub PR URL:     https://github.com/OWNER/REPO/pull/33
  - GitLab issue URL:  https://<host>/GROUP/REPO/-/issues/430
  - GitLab MR URL:     https://<host>/GROUP/REPO/-/merge_requests/55
  - Shorthand:         OWNER/REPO#22 (GitHub issue/PR number)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .errors import HarnessError, run_cmd

_GITHUB_ISSUE = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/issues/(\d+)/*$")
_GITHUB_PR = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/pull/(\d+)/*$")
_GITLAB_ISSUE = re.compile(r"^https?://([^/]+)/(.+)/-/issues/(\d+)/*$")
_GITLAB_MR = re.compile(r"^https?://([^/]+)/(.+)/-/merge_requests/(\d+)/*$")
_JIRA_URL = re.compile(r"^https?://[^/]+/browse/([A-Z][A-Z0-9_]*-\d+)/*$")
_JIRA_KEY = re.compile(r"^([A-Z][A-Z0-9_]*-\d+)$")
_SHORTHAND = re.compile(r"^([^/\s]+/[^/\s#]+)#(\d+)$")


def parse_ref(ref: str) -> dict:
    """Parse an issue/PR ref into {kind, tool, repo, number, url}."""
    m = _JIRA_URL.match(ref)
    if m:
        return {"kind": "issue", "tool": "jira-cli", "repo": "", "number": m.group(1), "url": ref}
    m = _JIRA_KEY.match(ref)
    if m:
        return {"kind": "issue", "tool": "jira-cli", "repo": "", "number": m.group(1), "url": ref}
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
        "  Supported: Jira key/URL, GitHub issue/PR URL, GitLab issue/MR URL, OWNER/REPO#NUM, or a bare number",
        exit_code=2,
    )


def issue_key(parsed: dict) -> str:
    """Stable key for links.json, e.g. 'jira:IPG-980' or 'github:owner/repo#22'."""
    if parsed["tool"] == "jira-cli":
        return f"jira:{parsed['number']}"
    host = "gitlab" if parsed["tool"] == "glab" else "github"
    repo = parsed["repo"] or "local"
    return f"{host}:{repo}#{parsed['number']}"


_JIRA_CONFIGS = (
    Path.home() / ".config" / "jira-cli" / "config.json",
    Path.home() / ".jira-cli.json",
)


def jira_site() -> str | None:
    """Base site URL from jira-cli's config; None when unset/invalid."""
    for path in _JIRA_CONFIGS:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        site = str(data.get("url") or "").strip().rstrip("/")
        if site:
            return site
    return None


def issue_url(key: str, stored: str | None = None) -> str | None:
    """Full issue URL for a session key; a stored http URL wins.

    jira:KEY   → <site>/browse/KEY (site from jira-cli config)
    github:r#N → https://github.com/r/issues/N
    gitlab:…#N → None (the key does not carry the host)
    """
    if stored and stored.startswith("http"):
        return stored
    host, _, ident = key.partition(":")
    if host == "jira" and ident:
        site = jira_site()
        return f"{site}/browse/{ident}" if site else None
    if host == "github" and "#" in ident:
        repo, num = ident.rsplit("#", 1)
        if repo and repo != "local":
            return f"https://github.com/{repo}/issues/{num}"
    return None


def fetch_issue(parsed: dict) -> dict:
    """Return {title, body} for an issue ref via jira-cli/gh/glab."""
    if parsed["tool"] == "jira-cli":
        out = run_cmd("jira-cli", "issue", parsed["number"], "--format", "json")
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise HarnessError(f"cannot parse jira-cli output for {parsed['number']}")
        return {"title": data.get("summary", ""), "body": data.get("description", "") or ""}
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


def fetch_pr_list_for_branch(tool: str, branch: str, cwd: str | None = None) -> list[dict]:
    """All PRs/MRs for a head branch (any state), normalized.

    Returns [{number, state, title, author, created_at, url, target_branch}]
    with state in open|merged|closed. `cwd` should be the repo path so
    gh/glab find the right host/remote.
    """
    if tool == "gh":
        out = run_cmd("gh", "pr", "list", "--head", branch, "--state", "all",
                      "--limit", "50", "--json",
                      "number,state,title,author,createdAt,url,baseRefName",
                      cwd=cwd)
        try:
            data = json.loads(out or "[]")
        except json.JSONDecodeError:
            raise HarnessError("cannot parse gh pr list output")
        state_map = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
        return [{"number": p["number"], "state": state_map.get(p["state"], "open"),
                 "title": p.get("title", ""), "author": (p.get("author") or {}).get("login", ""),
                 "created_at": p.get("createdAt", ""), "url": p.get("url", ""),
                 "target_branch": p.get("baseRefName", "")} for p in data]
    out = run_cmd("glab", "mr", "list", "--source-branch", branch, "-F", "json",
                  cwd=cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError("cannot parse glab mr list output")
    state_map = {"opened": "open", "merged": "merged", "closed": "closed"}
    return [{"number": m["iid"], "state": state_map.get(m["state"], "open"),
             "title": m.get("title", ""), "author": (m.get("author") or {}).get("username", ""),
             "created_at": m.get("created_at", ""), "url": m.get("web_url", ""),
             "target_branch": m.get("target_branch", "")} for m in data]


def latest_pr(prs: list[dict]) -> dict | None:
    """Most recent PR by creation date, or None."""
    if not prs:
        return None
    return max(prs, key=lambda p: p.get("created_at", ""))

