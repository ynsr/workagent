"""CI pipeline status for PR/MR (split from refs.py; re-exported via refs facade)."""
from __future__ import annotations

import fnmatch
import json
import os
import re
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
# Maximum pipelines of an MR inspected for a job URL (newest first).
_CI_PIPELINE_SCAN = 5


_SEC_JOB_PREFIXES = (
    "trivy:", "trivy-", "security", "sast", "dast",
    "secret_detection", "dependency_scanning", "container_scanning",
)


def _matches_ci_ignore(value: str, patterns: list[str]) -> bool:
    """Case-insensitive fnmatch against any pattern in *patterns*."""
    v = (value or "").strip().lower()
    if not v or not patterns:
        return False
    return any(fnmatch.fnmatchcase(v, p.strip().lower()) for p in patterns if p and p.strip())


def _repo_ci_ignore(cwd: str | None) -> list[str]:
    """Return per-repo ci_ignore patterns for *cwd* (repo or worktree), else []."""
    if not cwd:
        return []
    try:
        from . import store as _store
        norm = os.path.realpath(os.path.expanduser(str(cwd)))
        repos_map = _store.load_repos() or {}
        best: tuple[int, list[str]] | None = None
        for entry in repos_map.values():
            rp = entry.get("path") or ""
            if not rp:
                continue
            nrp = os.path.realpath(os.path.expanduser(str(rp)))
            if norm == nrp or norm.startswith(nrp + os.sep):
                if best is None or len(nrp) > best[0]:
                    best = (len(nrp), list(entry.get("ci_ignore") or []))
        if best is not None:
            return best[1]
        for wt in (_store.load_links() or {}).values():
            wp = wt.get("worktree") or wt.get("path") or ""
            if wp and os.path.realpath(os.path.expanduser(str(wp))) == norm:
                repo_p = wt.get("repo") or ""
                if repo_p:
                    nrp = os.path.realpath(os.path.expanduser(str(repo_p)))
                    for entry in repos_map.values():
                        rp = entry.get("path") or ""
                        if rp and os.path.realpath(os.path.expanduser(str(rp))) == nrp:
                            return list(entry.get("ci_ignore") or [])
    except Exception:
        return []
    return []


def _is_security_only_jobs(jobs: list) -> bool:
    """True when *jobs* is non-empty and every job is a security/trivy scan."""
    dicts = [j for j in jobs if isinstance(j, dict)]
    if not dicts:
        return False
    for j in dicts:
        stage = str(j.get("stage") or "").strip().lower()
        name = str(j.get("name") or "").strip().lower()
        if stage == "security":
            continue
        if any(name.startswith(pref) for pref in _SEC_JOB_PREFIXES):
            continue
        return False
    return True


def _is_side_ci_glab(pipeline: dict, jobs: list, ignore_patterns: list[str]) -> bool:
    """True when a GitLab pipeline is a side-CI (explicit ignore or security-only MR)."""
    source = str(pipeline.get("source") or "").strip().lower()
    if ignore_patterns:
        if source and _matches_ci_ignore(source, ignore_patterns):
            return True
        dicts = [j for j in jobs if isinstance(j, dict)]
        if dicts and all(
            _matches_ci_ignore(str(j.get("name") or ""), ignore_patterns)
            or _matches_ci_ignore(str(j.get("stage") or ""), ignore_patterns)
            for j in dicts
        ):
            return True
    if source == "merge_request_event" and _is_security_only_jobs(jobs):
        return True
    return False


def _select_main_glab_pipeline(
    data: list[dict],
    path: str,
    host: str,
    cwd: str | None,
    ref_label: str,
    ignore_patterns: list[str],
) -> tuple[dict, dict[int, list]]:
    """Select the main CI pipeline for the latest commit SHA, ignoring side-CIs."""
    newest = max(data, key=lambda p: p.get("id", 0))
    target_sha = str(newest.get("sha") or "")
    ordered = sorted(data, key=lambda p: p.get("id", 0), reverse=True)[:_CI_PIPELINE_SCAN]
    same_sha = [p for p in ordered if str(p.get("sha") or "") == target_sha] if target_sha else ordered
    jobs_by_id: dict[int, list] = {}
    if len(same_sha) <= 1 and not ignore_patterns:
        return newest, jobs_by_id
    for p in same_sha:
        pid = p.get("id")
        try:
            jobs = _pipeline_jobs(path, pid, host, cwd, ref_label)
        except HarnessError:
            # A transient jobs failure must not blank CI: skip this
            # candidate and keep looking for the main pipeline.
            continue
        if pid is not None:
            jobs_by_id[int(pid)] = jobs
        if not _is_side_ci_glab(p, jobs, ignore_patterns):
            return p, jobs_by_id
    return newest, jobs_by_id


def _ci_gh_details(pr_url: str, cwd: str | None) -> tuple[str, str, list[dict]]:
    """(status, url, checks) for a GitHub PR's statusCheckRollup."""
    out = run_cmd("gh", "pr", "view", pr_url, "--json", "statusCheckRollup",
                  cwd=cwd)
    try:
        rollup = json.loads(out or "{}").get("statusCheckRollup") or []
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh output for {pr_url}")
    ignore_patterns = _repo_ci_ignore(cwd)
    if ignore_patterns:
        filtered = [
            c for c in rollup
            if isinstance(c, dict)
            and not _matches_ci_ignore(str(c.get("name") or c.get("context") or ""), ignore_patterns)
        ]
        if filtered:
            rollup = filtered
    worst = "not_started"
    worst_url = ""
    any_url = ""
    checks: list[dict] = []
    for idx, check in enumerate(rollup):
        if not isinstance(check, dict):
            continue
        concl_raw = (check.get("conclusion") or "").upper()
        status_raw = (check.get("status") or check.get("state") or "").upper()
        if concl_raw in _GH_FAILURE or status_raw in _GH_FAILURE:
            state = "failure"
        elif concl_raw in _GH_SUCCESS or status_raw in _GH_SUCCESS:
            state = "success"
        elif concl_raw in _GH_RUNNING or status_raw in _GH_RUNNING:
            state = "running"
        else:
            state = "not_started"
        url = check.get("detailsUrl") or check.get("targetUrl") or ""
        if url and not any_url:
            any_url = url
        if _CI_RANK[state] > _CI_RANK[worst]:
            worst = state
            if url:
                worst_url = url
        name = str(check.get("name") or check.get("context") or f"check-{idx + 1}").strip()
        checks.append({
            "name": name,
            "commit_sha": str(check.get("commitSha") or check.get("headSha") or ""),
            "status": (status_raw or state).lower(),
            "conclusion": (concl_raw or state).lower(),
            "url": url,
            "log_tail": str(check.get("log_tail") or ""),
        })
    return worst, worst_url or any_url, checks


def _ci_gh(pr_url: str, cwd: str | None) -> tuple[str, str]:
    """(status, url): url is the worst check's details page (latest CI job).

    The worst check's page is preferred; when it carries no URL, any other
    check's page is used so the badge still links somewhere real.
    """
    worst, url, _ = _ci_gh_details(pr_url, cwd)
    return worst, url
def _api_glab(api: str, host: str, cwd: str | None) -> str:
    """glab api with --hostname, falling back to no-hostname on error."""
    try:
        return run_cmd("glab", "api", api, "--hostname", host, cwd=cwd)
    except HarnessError:
        return run_cmd("glab", "api", api, cwd=cwd)


def _ci_glab_details(pr_url: str, cwd: str | None) -> tuple[str, str, list[dict]]:
    """(status, url, checks) for a GitLab MR's newest pipeline."""
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
        return "not_started", "", []
    ignore_patterns = _repo_ci_ignore(cwd)
    main_pipe, jobs_by_id = _select_main_glab_pipeline(
        data, path, host, cwd, pr_url, ignore_patterns
    )
    state = _glab_state((main_pipe.get("status") or "").lower())
    if state == "not_started":
        return "not_started", "", []  # skipped, manual, unknown
    ordered = sorted(data, key=lambda p: p.get("id", 0),
                     reverse=True)[:_CI_PIPELINE_SCAN]
    main_id = main_pipe.get("id")
    scan = [main_pipe] + [p for p in ordered if p.get("id") != main_id]
    main_checks: list[dict] = []
    fallback = ""
    for pipeline in scan:
        pid = pipeline.get("id")
        if pid is not None and int(pid) in jobs_by_id:
            jobs = jobs_by_id[int(pid)]
        else:
            jobs = _pipeline_jobs(path, pid, host, cwd, pr_url)
        if not jobs:
            continue
        if pid == main_id:
            p_sha = str(pipeline.get("sha") or main_pipe.get("sha") or "")
            main_checks = [
                {
                    "name": str(j.get("name") or f"job-{j.get('id') or idx + 1}").strip(),
                    "commit_sha": str((j.get("commit") or {}).get("id") or p_sha),
                    "status": str(j.get("status") or "").lower(),
                    "conclusion": _glab_state(str(j.get("status") or "").lower()),
                    "url": str(j.get("web_url") or ""),
                    "log_tail": "",
                }
                for idx, j in enumerate(jobs)
                if isinstance(j, dict)
            ]
            # Failed-first (#116): the URL must be a failing job of the
            # main pipeline, not max(id) (a later success like .../203730
            # must not shadow the real failure .../203729).
            url = _pick_job_url(jobs)
            if url:
                return state, url, main_checks
            continue
        # Older pipelines are only a fallback when the main pipeline has no
        # job URL (e.g. merge_request_event that failed before any job started).
        # A side-CI never supplies the link: its passing job would point the
        # badge at the wrong pipeline (#184).
        if not fallback and not _is_side_ci_glab(pipeline, jobs, ignore_patterns):
            fallback = _pick_job_url(jobs)
            if fallback:
                break  # one banked fallback is enough; skip older pipelines
    if fallback:
        return state, fallback, main_checks
    # No job urls anywhere: the main pipeline page beats a dead badge.
    return state, main_pipe.get("web_url") or "", main_checks


def _ci_glab(pr_url: str, cwd: str | None) -> tuple[str, str]:
    """(status, url): url is the newest failed job's page (e.g. .../jobs/194493).

    Status comes from the newest pipeline. The URL is the newest *failed*
    job of that pipeline (newest running when none failed, else newest);
    when it has no jobs (a ``merge_request_event`` pipeline that failed
    before any job started), earlier pipelines of the same MR are walked
    back so the badge still points at a real job, and the newest pipeline
    page is the last resort.
    """
    state, url, _ = _ci_glab_details(pr_url, cwd)
    return state, url


def _glab_state(status: str) -> str:
    """Map a GitLab pipeline/job status onto the CI cell vocabulary."""
    if status in ("success", "passed"):
        return "success"
    if status in ("failed", "canceled", "cancelled"):
        return "failure"
    if status in _GLAB_RUNNING:
        return "running"
    return "not_started"


def _pick_job_url(jobs: list) -> str:
    """Newest failed job's page; newest running when none failed, else newest.

    Issue #116: picking max(job id) linked a successful job (e.g. .../203730)
    while the real failure (e.g. .../203729) sat earlier in the same
    pipeline. Only jobs carrying a web_url compete. Cancelled jobs rank in
    the failure tier (both ``canceled``/``cancelled`` spellings), so a
    cancelled pipeline links the cancelled job, not an adjacent
    skipped/manual one.
    """
    dicts = [j for j in jobs if isinstance(j, dict) and j.get("web_url")]
    if not dicts:
        return ""
    failed = [j for j in dicts
              if _glab_state(str(j.get("status") or "").lower()) == "failure"]
    if failed:
        return str(max(failed, key=lambda j: j.get("id", 0)).get("web_url"))
    running = [j for j in dicts
               if _glab_state(str(j.get("status") or "").lower()) == "running"]
    if running:
        return str(max(running, key=lambda j: j.get("id", 0)).get("web_url"))
    return str(max(dicts, key=lambda j: j.get("id", 0)).get("web_url"))


def _ci_branch_glab(host: str, path: str, branch: str,
                    cwd: str | None) -> tuple[str, str]:
    """(status, url) for the main pipeline of *branch* (glab)."""
    api = (f"projects/{quote(path, safe='')}/pipelines"
           f"?ref={quote(branch, safe='')}&per_page=5")
    out = _api_glab(api, host, cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab pipelines for {branch}")
    if not data:
        return "not_started", ""
    ignore_patterns = _repo_ci_ignore(cwd)
    main_pipe, jobs_by_id = _select_main_glab_pipeline(
        data, path, host, cwd, branch, ignore_patterns
    )
    state = _glab_state((main_pipe.get("status") or "").lower())
    if state == "not_started":
        return "not_started", ""
    pid = main_pipe.get("id")
    if pid is not None and int(pid) in jobs_by_id:
        jobs = jobs_by_id[int(pid)]
    else:
        jobs = _pipeline_jobs(path, pid, host, cwd, branch)
    url = _pick_job_url(jobs)
    if url:
        return state, url
    return state, main_pipe.get("web_url") or ""


def _ci_branch_gh(repo: str, branch: str, cwd: str | None) -> tuple[str, str]:
    """(status, url) for the newest run of *branch* (gh)."""
    out = run_cmd("gh", "run", "list", "--repo", repo, "--branch", branch,
                  "--limit", "5", "--json", "conclusion,status,url,name,workflowName", cwd=cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh runs for {branch}")
    if not data:
        return "not_started", ""
    ignore_patterns = _repo_ci_ignore(cwd)
    run = data[0]
    if ignore_patterns:
        for cand in data:
            if not (
                _matches_ci_ignore(str(cand.get("name") or ""), ignore_patterns)
                or _matches_ci_ignore(str(cand.get("workflowName") or ""), ignore_patterns)
            ):
                run = cand
                break
    concl = (run.get("conclusion") or "").upper()
    status = (run.get("status") or "").upper()
    if concl in _GH_FAILURE or status == "FAILED":
        return "failure", run.get("url") or ""
    if concl in _GH_SUCCESS and status in ("COMPLETED", ""):
        return "success", run.get("url") or ""
    if concl in _GH_RUNNING or status in ("IN_PROGRESS", "QUEUED", "PENDING"):
        return "running", run.get("url") or ""
    return "not_started", run.get("url") or ""

def _pipeline_jobs(path: str, pipeline_id: int | None, host: str,
                   cwd: str | None, pr_url: str) -> list:
    """Jobs of one pipeline; [] on a missing id or a non-list response."""
    if not pipeline_id:
        return []
    jobs_api = f"projects/{quote(path, safe='')}/pipelines/{pipeline_id}/jobs"
    try:
        jobs = json.loads(_api_glab(jobs_api, host, cwd) or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab jobs for {pr_url}")
    return jobs if isinstance(jobs, list) else []


def fetch_ci_details(tool: str, pr_url: str,
                     cwd: str | None = None, branch: str | None = None) -> tuple[str | None, str, list[dict]]:
    """(status, job_url, checks) for the latest CI pipeline of a PR/MR.

    ``checks`` is a list of normalized check dicts
    ``{name, commit_sha, status, conclusion, url, log_tail}`` (empty for
    branch-scoped lookups or on soft failure).
    """
    try:
        if branch:
            st, url = fetch_ci(tool, pr_url, cwd, branch)
            return st, url, []
        if tool == "gh":
            return _ci_gh_details(pr_url, cwd)
        if tool == "glab":
            return _ci_glab_details(pr_url, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: ci lookup failed for {pr_url}: {e}", file=sys.stderr)
        return None, "", []


def fetch_ci(tool: str, pr_url: str,
             cwd: str | None = None, branch: str | None = None) -> tuple[str | None, str]:
    """(status, job_url) for the latest CI pipeline of a PR/MR.

    job_url is the newest failed job's page (GitLab `.../-/jobs/<id>`,
    GitHub check details URL); "" when unknown. Soft-failing like
    fetch_ci_status. ``branch`` scopes the lookup to a branch's newest
    pipeline instead of the PR/MR (used by the release card); None
    preserves PR/MR behavior.
    """
    try:
        if branch:
            if tool == "gh":
                from .refs import _GITHUB_PR
                m = _GITHUB_PR.match(pr_url or "")
                repo = m.group(1) if m else ""
                if not repo:
                    raise HarnessError(f"cannot derive repo for branch CI: {pr_url}")
                return _ci_branch_gh(repo, branch, cwd)
            if tool == "glab":
                m = _GITLAB_MR.match(pr_url or "")
                if not m:
                    raise HarnessError(f"not a GitLab MR URL: {pr_url}")
                return _ci_branch_glab(m.group(1), m.group(2), branch, cwd)
            raise HarnessError(f"unknown host CLI: {tool}")
        if tool == "gh":
            return _ci_gh(pr_url, cwd)
        if tool == "glab":
            return _ci_glab(pr_url, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: ci lookup failed for {pr_url}: {e}", file=sys.stderr)
        return None, ""


def fetch_ci_status(tool: str, pr_url: str, cwd: str | None = None,
                    branch: str | None = None) -> str | None:
    """Latest CI pipeline status for a PR/MR: success|failure|running|not_started.

    Soft-failing: returns None on any lookup error (warning to stderr),
    so status table rendering never breaks on a missing/unhappy host CLI.
    ``branch`` scopes to that branch's newest pipeline (release card).
    """
    return fetch_ci(tool, pr_url, cwd, branch)[0]
def _split_remote(url: str) -> tuple[str, str]:
    """(host, path) from an https/ssh git remote URL; ("", "") when unknown."""
    u = (url or "").strip()
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^/@]+@)?([^/:?#]+)/(.+?)(?:\.git)?/*$", u, re.IGNORECASE)
    if m:
        return m.group(1).lower(), m.group(2)
    m = re.match(r"^[^/@]+@([^/:]+):(.+?)(?:\.git)?/*$", u)
    if m:
        return m.group(1).lower(), m.group(2)
    return "", ""


def fetch_branch_ci(tool: str, remote_url: str, branch: str,
                    cwd: str | None = None) -> tuple[str | None, str]:
    """(status, url) for *branch*'s newest pipeline, from a repo remote URL.

    Used by the release card's release-branch CI badge. Soft-failing like
    fetch_ci: None on any lookup error (warning to stderr).
    """
    try:
        host, path = _split_remote(remote_url)
        if not path:
            raise HarnessError(f"cannot parse remote URL: {remote_url}")
        if tool == "gh":
            return _ci_branch_gh(path, branch, cwd)
        if tool == "glab":
            return _ci_branch_glab(host or "gitlab.com", path, branch, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: branch ci lookup failed for {branch}: {e}", file=sys.stderr)
        return None, ""

