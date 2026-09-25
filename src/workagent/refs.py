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


def latest_pr(prs: list[dict]) -> dict | None:
    """Most recent PR by creation date, or None."""
    if not prs:
        return None
    return max(prs, key=lambda p: p.get("created_at", ""))



_GH_FAILURE = {"FAILURE", "ACTION_REQUIRED", "TIMED_OUT", "STARTUP_FAILURE",
               "CANCELLED"}
_GH_SUCCESS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_GH_RUNNING = {"IN_PROGRESS", "QUEUED", "PENDING", "STARTING"}
_CI_RANK = {"not_started": 0, "success": 1, "running": 2, "failure": 3}
_GLAB_RUNNING = {"running", "pending", "created", "waiting_for_resource",
                 "preparing"}


def _ci_gh(pr_url: str, cwd: str | None) -> str:
    out = run_cmd("gh", "pr", "view", pr_url, "--json", "statusCheckRollup",
                  cwd=cwd)
    try:
        rollup = json.loads(out or "{}").get("statusCheckRollup") or []
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    worst = "not_started"
    for check in rollup:
        concl = (check.get("conclusion") or "").upper()
        if concl in _GH_FAILURE:
            state = "failure"
        elif concl in _GH_SUCCESS:
            state = "success"
        elif concl in _GH_RUNNING:
            state = "running"
        else:
            state = "not_started"
        if _CI_RANK[state] > _CI_RANK[worst]:
            worst = state
    return worst


def _ci_glab(pr_url: str, cwd: str | None) -> str:
    m = _GITLAB_MR.match(pr_url or "")
    if not m:
        raise HarnessError(f"not a GitLab MR URL: {pr_url}")
    host, path, iid = m.group(1), m.group(2), m.group(3)
    api = f"projects/{quote(path, safe='')}/merge_requests/{iid}/pipelines"
    try:
        out = run_cmd("glab", "api", api, "--hostname", host, cwd=cwd)
    except HarnessError:
        out = run_cmd("glab", "api", api, cwd=cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab output for {pr_url}")
    if not data:
        return "not_started"
    latest = max(data, key=lambda p: p.get("id", 0))
    status = (latest.get("status") or "").lower()
    if status in ("success", "passed"):
        return "success"
    if status in ("failed", "canceled"):
        return "failure"
    if status in _GLAB_RUNNING:
        return "running"
    return "not_started"  # skipped, manual, unknown


def fetch_ci_status(tool: str, pr_url: str, cwd: str | None = None) -> str | None:
    """Latest CI pipeline status for a PR/MR: success|failure|running|not_started.

    Soft-failing: returns None on any lookup error (warning to stderr),
    so status table rendering never breaks on a missing/unhappy host CLI.
    """
    try:
        if tool == "gh":
            return _ci_gh(pr_url, cwd)
        if tool == "glab":
            return _ci_glab(pr_url, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: ci lookup failed for {pr_url}: {e}", file=sys.stderr)
        return None

def _review_comments_gh(pr_url: str, cwd: str | None) -> dict:
    """{reviews, unresolved, resolved} for a GitHub PR.

    reviews = issue comments whose body starts with `# Code Review`
    (each one marks a completed harness review). unresolved/resolved come
    from the GraphQL reviewThreads connection.
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
    reviews = sum(1 for c in comments
                  if str((c or {}).get("body") or "").lstrip().startswith("# Code Review"))
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
    unresolved = sum(1 for n in nodes if not (n or {}).get("isResolved"))
    resolved = sum(1 for n in nodes if (n or {}).get("isResolved"))
    return {"reviews": reviews, "unresolved": unresolved, "resolved": resolved}


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
        if (disc or {}).get("resolved") is True:
            resolved += 1
        elif notes and all((n or {}).get("resolved") for n in notes):
            resolved += 1
        elif notes:
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
