"""Issue/PR link parsing and fetching via jira-cli/gh/glab.

Supported inputs:
  - Jira key:          IPG-980 or jira:IPG-980 (issue_key() emits the prefixed form)
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
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

from .errors import HarnessError, run_cmd

_GITHUB_ISSUE = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/issues/(\d+)/*$")
_GITHUB_PR = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/pull/(\d+)/*$")
_GITLAB_ISSUE = re.compile(r"^https?://([^/]+)/(.+)/-/issues/(\d+)/*$")
_GITLAB_MR = re.compile(r"^https?://([^/]+)/(.+)/-/merge_requests/(\d+)/*$")
_JIRA_URL = re.compile(r"^https?://[^/]+/browse/([A-Z][A-Z0-9_]*-\d+)/*$")
_JIRA_KEY = re.compile(r"^(?:jira:)?([A-Z][A-Z0-9_]*-\d+)$")
_SHORTHAND = re.compile(r"^(?:github:)?([^/\s]+/[^/\s#]+)#(\d+)$")


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


def fetch_pr_info(parsed: dict, cwd: str | None = None) -> dict:
    """Return {title, head_ref, issue_key} for a PR/MR ref (best effort).

    ``cwd`` must be the repo dir so gh/glab resolve the right host/remote —
    without it glab fails with "Not a git repository" outside a checkout.
    """
    if parsed["tool"] == "gh":
        out = run_cmd("gh", "pr", "view", parsed["url"], "--json", "title,headRefName,body",
                      cwd=cwd)
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise HarnessError(f"cannot parse gh output for {parsed['url']}")
        return {"title": data.get("title", ""),
                "head_ref": data.get("headRefName", ""),
                "body": data.get("body", "") or ""}
    out = run_cmd("glab", "mr", "view", parsed["url"], "-F", "json", cwd=cwd)
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab output for {parsed['url']}")
    return {"title": data.get("title", ""),
            "head_ref": data.get("source_branch", ""),
            "body": data.get("description", "") or ""}


def require_head_ref(parsed: dict, repo_dir, ref: str) -> tuple[dict, str]:
    """fetch_pr_info + missing-branch guard shared by start/review/sync.

    Returns (info, head_ref); raises HarnessError with the standard
    "git fetch origin … check gh/glab auth" hint when the source branch
    cannot be determined. `repo_dir` may be a Path or str.
    """
    pr_url = parsed["url"]
    cwd = str(repo_dir)
    try:
        info = fetch_pr_info(parsed, cwd=cwd)
    except HarnessError as e:
        raise HarnessError(
            f"could not determine the source branch for {pr_url}: {e}",
            exit_code=2,
        )
    head_ref = (info or {}).get("head_ref", "")
    if not head_ref:
        raise HarnessError(
            f"could not determine the source branch for {pr_url}.\n"
            f"  Run `git fetch origin` in {cwd} and check `gh`/`glab` auth for that host.",
            exit_code=2,
        )
    return info, head_ref


def hostname(url: str) -> str:
    return urlparse(url).netloc.lower()


def pr_key(url: str) -> str:
    """Canonical PR/MR key for *url*: github:o/r#N or gitlab:host/g/r#N;
    "" when the URL is not a PR/MR."""
    m = _GITHUB_PR.match(url or "")
    if m:
        return f"github:{m.group(1)}#{m.group(2)}"
    m = _GITLAB_MR.match(url or "")
    if m:
        return f"gitlab:{m.group(1)}/{m.group(2)}#{m.group(3)}"
    return ""


def fetch_open_prs(tool: str, cwd: str | None = None) -> list[dict]:
    """All open PRs/MRs of the repo at *cwd*, normalized.

    Returns [{number, title, branch, updated, url, state:"OPEN"}].
    Raises HarnessError when the host CLI fails (caller decides).
    """
    if tool == "gh":
        out = run_cmd("gh", "pr", "list", "--state", "open", "--limit", "100",
                      "--json", "number,title,headRefName,updatedAt,url",
                      cwd=cwd)
        try:
            data = json.loads(out or "[]")
        except json.JSONDecodeError:
            raise HarnessError("cannot parse gh pr list output")
        return [{"number": p["number"], "title": p.get("title", ""),
                 "branch": p.get("headRefName", ""),
                 "updated": p.get("updatedAt", ""), "url": p.get("url", ""),
                 "state": "OPEN"} for p in data]
    if tool == "glab":
        try:
            out = run_cmd("glab", "mr", "list", "--state", "opened", "-F", "json",
                          "--per-page", "100", cwd=cwd)
        except HarnessError:
            # glab ≥1.x mr list has no --state flag; it defaults to open MRs.
            out = run_cmd("glab", "mr", "list", "-F", "json",
                          "--per-page", "100", cwd=cwd)
        try:
            data = json.loads(out or "[]")
        except json.JSONDecodeError:
            raise HarnessError("cannot parse glab mr list output")
        return [{"number": m["iid"], "title": m.get("title", ""),
                 "branch": m.get("source_branch", ""),
                 "updated": m.get("updated_at", ""),
                 "url": m.get("web_url", ""), "state": "OPEN"} for m in data]
    raise HarnessError(f"unsupported host CLI: {tool}")


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
    out = run_cmd("glab", "mr", "list", "--source-branch", branch, "--all", "-F", "json",
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



def fetch_pr_merge_state(pr_url: str, cwd: str | None = None) -> dict:
    """Fresh {state, mergeable} for a PR/MR URL; raises HarnessError on failure.

    GitHub: state in OPEN|MERGED|CLOSED, mergeable in MERGEABLE|CONFLICTING|UNKNOWN.
    GitLab: state in opened|merged|closed (+ detailed_merge_status / has_conflicts).
    A deleted/missing PR (404) surfaces as HarnessError with "404"/"not found"
    in the message so callers can keep the remote branch and the link.
    """
    m = _GITHUB_PR.match(pr_url or "")
    if m:
        out = run_cmd("gh", "pr", "view", pr_url, "--json",
                      "state,mergeable,mergeStateStatus", cwd=cwd)
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise HarnessError(f"cannot parse gh output for {pr_url}")
        return {"state": str(data.get("state") or ""),
                "mergeable": str(data.get("mergeable") or ""),
                "merge_state": str(data.get("mergeStateStatus") or "")}
    m = _GITLAB_MR.match(pr_url or "")
    if m:
        out = run_cmd("glab", "mr", "view", pr_url, cwd=cwd)
        try:
            data = json.loads(out or "{}")
        except json.JSONDecodeError:
            raise HarnessError(f"cannot parse glab output for {pr_url}")
        return {"state": str(data.get("state") or ""),
                "mergeable": str(data.get("detailed_merge_status") or ""),
                "merge_state": "conflicts" if data.get("has_conflicts") else ""}
    raise HarnessError(f"not a PR/MR URL: {pr_url}")


def latest_pr(prs: list[dict]) -> dict | None:
    """Most recent PR by creation date, or None."""
    if not prs:
        return None
    return max(prs, key=lambda p: p.get("created_at", ""))


def pick_branch_pr(prs: list[dict]) -> dict | None:
    """PR/MR to act on for a source branch: latest open, else latest.

    A branch can source several PRs/MRs over time (closed superseded ones
    plus the live one). Only an open PR is live — prefer the newest open
    one. With no open PR, the latest (merged/closed) row still identifies
    the branch's fate for remote-branch deletion. Returns None for an
    empty list.
    """
    if not prs:
        return None
    live = [p for p in prs if (p.get("state") or "").lower() == "open"]
    pool = live or prs
    return max(pool, key=lambda p: p.get("created_at", ""))

from .refs_ci import (  # noqa: F401,E402
    _ci_gh,
    _ci_glab,
    fetch_ci_status,
)

from .refs_reviews import (  # noqa: F401,E402
    _gh_bot_comment_resolved,
    _review_comments_gh,
    _review_comments_glab,
    fetch_pr_comment_stats,
)
