"""CI pipeline status for PR/MR (split from refs.py; re-exported via refs facade)."""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

from .errors import HarnessError
from .refs import _GITLAB_MR


def run_cmd(*a, **k):
    """run_cmd via the refs namespace so tests patching refs.run_cmd apply."""
    from . import refs as _r
    return _r.run_cmd(*a, **k)

_GH_FAILURE = {"FAILURE", "ACTION_REQUIRED", "TIMED_OUT", "STARTUP_FAILURE",
               "CANCELLED"}
_GH_SUCCESS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
_GH_RUNNING = {"IN_PROGRESS", "QUEUED", "PENDING", "STARTING"}
_CI_RANK = {"not_started": 0, "success": 1, "running": 2, "failure": 3}
_GLAB_RUNNING = {"running", "pending", "created", "waiting_for_resource",
                 "preparing"}


def _ci_gh(pr_url: str, cwd: str | None) -> tuple[str, str]:
    """(status, url): url is the worst check's details page (latest CI job)."""
    out = run_cmd("gh", "pr", "view", pr_url, "--json", "statusCheckRollup",
                  cwd=cwd)
    try:
        rollup = json.loads(out or "{}").get("statusCheckRollup") or []
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    worst = "not_started"
    worst_url = ""
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
            worst_url = check.get("detailsUrl") or check.get("targetUrl") or ""
    return worst, worst_url


def _api_glab(api: str, host: str, cwd: str | None) -> str:
    """glab api with --hostname, falling back to no-hostname on error."""
    try:
        return run_cmd("glab", "api", api, "--hostname", host, cwd=cwd)
    except HarnessError:
        return run_cmd("glab", "api", api, cwd=cwd)


def _ci_glab(pr_url: str, cwd: str | None) -> tuple[str, str]:
    """(status, url): url is the latest job's page (e.g. .../-/jobs/194493)."""
    m = _GITLAB_MR.match(pr_url or "")
    if not m:
        raise HarnessError(f"not a GitLab MR URL: {pr_url}")
    host, path, iid = m.group(1), m.group(2), m.group(3)
    api = f"projects/{quote(path, safe='')}/merge_requests/{iid}/pipelines"
    out = _api_glab(api, host, cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab output for {pr_url}")
    if not data:
        return "not_started", ""
    latest = max(data, key=lambda p: p.get("id", 0))
    status = (latest.get("status") or "").lower()
    if status in ("success", "passed"):
        state = "success"
    elif status in ("failed", "canceled"):
        state = "failure"
    elif status in _GLAB_RUNNING:
        state = "running"
    else:
        return "not_started", ""  # skipped, manual, unknown
    jobs_api = (f"projects/{quote(path, safe='')}/pipelines/"
                f"{latest.get('id')}/jobs")
    try:
        jobs = json.loads(_api_glab(jobs_api, host, cwd) or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab jobs for {pr_url}")
    if not jobs:
        return state, ""
    job = max(jobs, key=lambda j: j.get("id", 0))
    return state, job.get("web_url") or ""


def fetch_ci(tool: str, pr_url: str,
             cwd: str | None = None) -> tuple[str | None, str]:
    """(status, job_url) for the latest CI pipeline of a PR/MR.

    job_url is the latest job's page (GitLab `.../-/jobs/<id>`, GitHub check
    details URL); "" when unknown. Soft-failing like fetch_ci_status.
    """
    try:
        if tool == "gh":
            return _ci_gh(pr_url, cwd)
        if tool == "glab":
            return _ci_glab(pr_url, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: ci lookup failed for {pr_url}: {e}", file=sys.stderr)
        return None, ""


def fetch_ci_status(tool: str, pr_url: str, cwd: str | None = None) -> str | None:
    """Latest CI pipeline status for a PR/MR: success|failure|running|not_started.

    Soft-failing: returns None on any lookup error (warning to stderr),
    so status table rendering never breaks on a missing/unhappy host CLI.
    """
    return fetch_ci(tool, pr_url, cwd)[0]

