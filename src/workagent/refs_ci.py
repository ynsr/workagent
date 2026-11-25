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
        best_wt: tuple[int, list[str]] | None = None
        for wt in (_store.load_links() or {}).values():
            wp = wt.get("worktree") or wt.get("path") or ""
            if not wp:
                continue
            nwp = os.path.realpath(os.path.expanduser(str(wp)))
            if norm == nwp or norm.startswith(nwp + os.sep):
                repo_p = wt.get("repo") or ""
                if repo_p:
                    nrp = os.path.realpath(os.path.expanduser(str(repo_p)))
                    for entry in repos_map.values():
                        rp = entry.get("path") or ""
                        if rp and os.path.realpath(os.path.expanduser(str(rp))) == nrp:
                            if best_wt is None or len(nwp) > best_wt[0]:
                                best_wt = (len(nwp), list(entry.get("ci_ignore") or []))
                            break
        if best_wt is not None:
            return best_wt[1]
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


def _glab_mr_head_sha(path: str, iid: str, host: str, cwd: str | None) -> str:
    """Return the MR's remote head commit SHA, or '' on error/missing."""
    try:
        out = _api_glab(f"projects/{quote(path, safe='')}/merge_requests/{iid}", host, cwd)
        data = json.loads(out or "{}")
    except (HarnessError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    diff_refs = data.get("diff_refs")
    head_sha = data.get("sha") or (diff_refs.get("head_sha") if isinstance(diff_refs, dict) else "")
    return str(head_sha or "")


def _glab_branch_head_sha(path: str, branch: str, host: str, cwd: str | None) -> str:
    """Return *branch*'s remote head commit SHA, or '' on error/missing."""
    try:
        out = _api_glab(
            f"projects/{quote(path, safe='')}/repository/branches/{quote(branch, safe='')}",
            host,
            cwd,
        )
        data = json.loads(out or "{}")
    except (HarnessError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    commit = data.get("commit")
    return str(commit.get("id") or "") if isinstance(commit, dict) else ""


def _gh_branch_head_sha(repo: str, branch: str, cwd: str | None, host: str = "") -> str:
    """Return *branch*'s remote head commit SHA on GitHub, or '' on error/missing."""
    try:
        out = _api_gh(f"repos/{repo}/branches/{quote(branch, safe='')}", host, cwd)
        data = json.loads(out or "{}")
    except (HarnessError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    commit = data.get("commit")
    return str(commit.get("sha") or "") if isinstance(commit, dict) else ""


def _filter_commit_items(items: list, head_sha: str, sha_key: str) -> list[dict]:
    """Filter *items* (already newest-first) to those matching *head_sha*."""
    dicts = [it for it in items if isinstance(it, dict)]
    if not dicts:
        return []
    has_shas = any(str(it.get(sha_key) or "") for it in dicts)
    if head_sha:
        if not has_shas:
            return dicts
        return [it for it in dicts if str(it.get(sha_key) or "") == head_sha]
    target_sha = str(dicts[0].get(sha_key) or "")
    if target_sha:
        return [it for it in dicts if str(it.get(sha_key) or "") == target_sha]
    return dicts


def _filter_glab_commit_pipelines(data: list, head_sha: str) -> list[dict]:
    """Return pipelines (newest first) belonging to the active branch's last commit."""
    dicts = [p for p in data if isinstance(p, dict)]
    ordered = sorted(dicts, key=lambda p: p.get("id") or 0, reverse=True)
    return _filter_commit_items(ordered, head_sha, "sha")


def _evaluate_glab_commit_pipelines(
    same_sha: list[dict],
    path: str,
    host: str,
    cwd: str | None,
    ref_label: str,
    ignore_patterns: list[str],
) -> tuple[str, str, list[dict]]:
    """Aggregate all non-ignored GitLab pipelines for the last commit."""
    if not same_sha:
        return "not_started", "", []
    jobs_by_id: dict[int, list] = {}
    main_pipes: list[dict] = []
    for p in same_sha:
        pid = p.get("id")
        try:
            jobs = _pipeline_jobs(path, pid, host, cwd, ref_label)
        except HarnessError:
            source = str(p.get("source") or "").strip().lower()
            if not (ignore_patterns and source and _matches_ci_ignore(source, ignore_patterns)):
                main_pipes.append(p)
            continue
        if pid is not None:
            jobs_by_id[int(pid)] = jobs
        if not _is_side_ci_glab(p, jobs, ignore_patterns):
            main_pipes.append(p)
    selected = main_pipes if main_pipes else [same_sha[0]]
    worst_state = "not_started"
    worst_pipe = selected[0]
    for p in selected:
        p_state = _glab_state((p.get("status") or "").lower())
        if _CI_RANK[p_state] > _CI_RANK[worst_state]:
            worst_state = p_state
            worst_pipe = p
    if worst_state == "not_started":
        return "not_started", "", []
    worst_jobs: list[dict] = []
    all_jobs: list[dict] = []
    checks: list[dict] = []
    for p in selected:
        pid = p.get("id")
        jobs = jobs_by_id.get(int(pid), []) if pid is not None else []
        p_sha = str(p.get("sha") or "")
        p_state = _glab_state((p.get("status") or "").lower())
        for idx, j in enumerate(jobs):
            if not isinstance(j, dict):
                continue
            all_jobs.append(j)
            if p_state == worst_state:
                worst_jobs.append(j)
            checks.append({
                "name": str(j.get("name") or f"job-{j.get('id') or idx + 1}").strip(),
                "commit_sha": str((j.get("commit") or {}).get("id") or p_sha),
                "status": str(j.get("status") or "").lower(),
                "conclusion": _glab_state(str(j.get("status") or "").lower()),
                "url": str(j.get("web_url") or ""),
                "log_tail": "",
            })
    url = _pick_job_url(worst_jobs) or (
        "" if worst_state in ("failure", "running") else _pick_job_url(all_jobs)
    )
    if url:
        return worst_state, url, checks
    fallback_url = str(worst_pipe.get("web_url") or "")
    if not fallback_url:
        for p in selected:
            if p.get("web_url"):
                fallback_url = str(p["web_url"])
                break
    return worst_state, fallback_url, checks


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


def _api_gh(api: str, host: str, cwd: str | None) -> str:
    """gh api with --hostname when non-github.com, falling back on error."""
    if host and host.lower() != "github.com":
        try:
            return run_cmd("gh", "api", api, "--hostname", host, cwd=cwd) or ""
        except HarnessError:
            pass
    return run_cmd("gh", "api", api, cwd=cwd) or ""


CI_LOG_TAIL_LINES = 50
CI_LOG_MAX_CHARS = 12000

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;:?]*[A-Za-z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_GL_JOB_URL_RE = re.compile(r"^https?://([^/]+)/(.+?)/-/jobs/(\d+)(?:[/?#].*)?$")
_GL_PIPELINE_URL_RE = re.compile(r"^https?://([^/]+)/(.+?)/-/pipelines/(\d+)(?:[/?#].*)?$")
_GH_RUN_JOB_URL_RE = re.compile(
    r"^https?://([^/]+)/([^/]+/[^/]+)/actions/runs/(\d+)/job/(\d+)(?:[/?#].*)?$"
)
_GH_RUN_URL_RE = re.compile(
    r"^https?://([^/]+)/([^/]+/[^/]+)/actions/runs/(\d+)(?:[/?#].*)?$"
)
_GH_CHECK_RUN_URL_RE = re.compile(
    r"^https?://([^/]+)/([^/]+/[^/]+)/(?:runs/(\d+)(?:[/?#].*)?|pull/\d+/checks\?[^#]*\bcheck_run_id=(\d+)(?:[&#].*)?)$"
)
_GHES_PR_URL_RE = re.compile(
    r"^https?://[^/]+/[^/]+/[^/]+/pull/\d+(?:[/?#].*)?$"
)


def _strip_ansi_and_cr(text: str) -> str:
    """Strip ANSI escape sequences and collapse CR progress overwrites."""
    cleaned = _ANSI_OSC_RE.sub("", text or "")
    cleaned = _ANSI_CSI_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n")
    if "\r" in cleaned:
        cleaned = "\n".join(
            ln.rstrip("\r").rsplit("\r", 1)[-1] for ln in cleaned.split("\n")
        )
    return cleaned


def _tail_log_lines(
    text: str,
    max_lines: int = CI_LOG_TAIL_LINES,
    max_chars: int = CI_LOG_MAX_CHARS,
) -> str:
    """Return the cleaned last *max_lines* lines (capped at *max_chars*)."""
    cleaned = _strip_ansi_and_cr(text).rstrip()
    if not cleaned:
        return ""
    lines = cleaned.splitlines()
    if max_lines > 0:
        lines = lines[-max_lines:]
    tail = "\n".join(lines).strip()
    if max_chars > 0 and len(tail) > max_chars:
        tail = tail[-max_chars:]
        if "\n" in tail:
            tail = tail.split("\n", 1)[1]
        tail = tail.strip()
    return tail


def infer_pr_tool(pr_url: str, cached: dict | None = None) -> str:
    """Derive host CLI (``gh`` or ``glab``) for a PR/MR URL, preferring *cached*."""
    tool = str((cached or {}).get("tool") or "").strip().lower()
    if tool in ("gh", "glab"):
        return tool
    u = (pr_url or "").strip()
    if not u:
        return ""
    from .refs import _GITHUB_PR
    if _GITHUB_PR.match(u) or _GHES_PR_URL_RE.match(u):
        return "gh"
    if _GITLAB_MR.match(u):
        return "glab"
    return "gh" if "github.com" in u.lower() else "glab"


def _infer_ci_tool(tool: str, job_url: str) -> str:
    t = (tool or "").strip().lower()
    if t in ("gh", "glab"):
        return t
    u = (job_url or "").strip()
    if (_GH_RUN_JOB_URL_RE.match(u) or _GH_RUN_URL_RE.match(u)
            or _GH_CHECK_RUN_URL_RE.match(u) or "github.com" in u.lower()):
        return "gh"
    if _GL_JOB_URL_RE.match(u) or _GL_PIPELINE_URL_RE.match(u):
        return "glab"
    return ""


def fetch_ci_job_log(
    tool: str,
    job_url: str,
    *,
    cwd: str | None = None,
    tail_lines: int = CI_LOG_TAIL_LINES,
) -> str:
    """Return the last *tail_lines* of terminal output for a failing CI job URL.

    Supports GitHub Actions (run/job/check-run URLs) and GitLab CI
    (job/pipeline URLs). Soft-fails to ``""`` on any error or unsupported URL.
    """
    url = (job_url or "").strip()
    if not url:
        return ""
    eff_tool = _infer_ci_tool(tool, url)
    if not eff_tool:
        return ""
    raw = ""
    try:
        if eff_tool == "glab":
            m = _GL_JOB_URL_RE.match(url)
            if m:
                host, project, job_id = m.group(1), m.group(2), m.group(3)
                raw = _api_glab(
                    f"projects/{quote(project, safe='')}/jobs/{job_id}/trace",
                    host,
                    cwd,
                ) or ""
            else:
                pm = _GL_PIPELINE_URL_RE.match(url)
                if not pm:
                    return ""
                host, project, pid = pm.group(1), pm.group(2), pm.group(3)
                jobs = _pipeline_jobs(project, int(pid), host, cwd, url)
                failed = [
                    j for j in jobs
                    if isinstance(j, dict)
                    and j.get("id") is not None
                    and _glab_state(str(j.get("status") or "").lower()) == "failure"
                ]
                if not failed:
                    return ""
                job_id = str(max(failed, key=lambda j: j.get("id", 0))["id"])
                raw = _api_glab(
                    f"projects/{quote(project, safe='')}/jobs/{job_id}/trace",
                    host,
                    cwd,
                ) or ""
        else:
            m = _GH_RUN_JOB_URL_RE.match(url)
            if m:
                host, project, run_id, job_id = m.group(1), m.group(2), m.group(3), m.group(4)
                repo_arg = f"{host}/{project}" if host.lower() != "github.com" else project
                try:
                    raw = run_cmd(
                        "gh", "run", "view", run_id, "--job", job_id,
                        "--log-failed", "--repo", repo_arg, cwd=cwd,
                    ) or ""
                except HarnessError:
                    raw = ""
                if not raw.strip():
                    raw = _api_gh(
                        f"repos/{project}/actions/jobs/{job_id}/logs",
                        host,
                        cwd,
                    )
            else:
                rm = _GH_RUN_URL_RE.match(url)
                if rm:
                    host, project, run_id = rm.group(1), rm.group(2), rm.group(3)
                    repo_arg = f"{host}/{project}" if host.lower() != "github.com" else project
                    raw = run_cmd(
                        "gh", "run", "view", run_id,
                        "--log-failed", "--repo", repo_arg, cwd=cwd,
                    ) or ""
                else:
                    cm = _GH_CHECK_RUN_URL_RE.match(url)
                    if not cm:
                        return ""
                    host, project = cm.group(1), cm.group(2)
                    job_id = cm.group(3) or cm.group(4)
                    raw = _api_gh(
                        f"repos/{project}/actions/jobs/{job_id}/logs",
                        host,
                        cwd,
                    )
    except Exception as e:
        print(f"warning: CI log fetch failed for {url}: {e}", file=sys.stderr)
        return ""
    return _tail_log_lines(raw, max_lines=tail_lines)


def populate_failing_check_logs(
    tool: str,
    checks: list[dict],
    *,
    cwd: str | None = None,
    tail_lines: int = CI_LOG_TAIL_LINES,
    max_checks: int = 5,
    fallback_url: str = "",
) -> list[dict]:
    """Populate ``log_tail`` (last *tail_lines*) on failing checks missing logs.

    When *fallback_url* is provided, failing checks with an empty ``url`` are
    backfilled with it. Distinct ``(tool, url)`` targets are fetched at most
    once so shared URLs (e.g. a pipeline fallback) reuse the cached tail
    without consuming extra *max_checks* slots.
    """
    if not checks:
        return checks
    from . import store_timeline
    fb_url = (fallback_url or "").strip()
    fetched = 0
    tail_cache: dict[tuple[str, str], str] = {}
    for chk in checks:
        if not isinstance(chk, dict):
            continue
        if not store_timeline.is_failing_check(chk):
            continue
        url = str(chk.get("url") or "").strip()
        if not url and fb_url:
            url = fb_url
            chk["url"] = fb_url
        if str(chk.get("log_tail") or "").strip():
            continue
        if not url:
            continue
        cache_key = (tool, url)
        if cache_key in tail_cache:
            tail = tail_cache[cache_key]
        else:
            if fetched >= max_checks:
                continue
            fetched += 1
            tail = fetch_ci_job_log(tool, url, cwd=cwd, tail_lines=tail_lines)
            tail_cache[cache_key] = tail
        if tail:
            chk["log_tail"] = tail
    return checks


def _ci_glab_details(pr_url: str, cwd: str | None) -> tuple[str, str, list[dict]]:
    """(status, url, checks) for all CI pipelines of a GitLab MR's last commit."""
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
    if not isinstance(data, list) or not data:
        return "not_started", "", []
    head_sha = _glab_mr_head_sha(path, iid, host, cwd)
    same_sha = _filter_glab_commit_pipelines(data, head_sha)
    if not same_sha:
        return "not_started", "", []
    ignore_patterns = _repo_ci_ignore(cwd)
    return _evaluate_glab_commit_pipelines(
        same_sha, path, host, cwd, pr_url, ignore_patterns
    )


def _ci_glab(pr_url: str, cwd: str | None) -> tuple[str, str]:
    """(status, url) for all non-ignored pipelines of a GitLab MR's last commit."""
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
    """(status, url) for all CI pipelines of *branch*'s last commit (glab)."""
    api = (f"projects/{quote(path, safe='')}/pipelines"
           f"?ref={quote(branch, safe='')}&per_page=20")
    out = _api_glab(api, host, cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse glab pipelines for {branch}")
    if not isinstance(data, list) or not data:
        return "not_started", ""
    head_sha = _glab_branch_head_sha(path, branch, host, cwd)
    same_sha = _filter_glab_commit_pipelines(data, head_sha)
    if not same_sha:
        return "not_started", ""
    ignore_patterns = _repo_ci_ignore(cwd)
    state, url, _ = _evaluate_glab_commit_pipelines(
        same_sha, path, host, cwd, branch, ignore_patterns
    )
    return state, url


def _gh_run_state(run: dict) -> str:
    """Map one GitHub Actions run dict onto the CI cell vocabulary."""
    concl = (run.get("conclusion") or "").upper()
    status = (run.get("status") or "").upper()
    if concl in _GH_FAILURE or status == "FAILED":
        return "failure"
    if concl in _GH_SUCCESS and status in ("COMPLETED", ""):
        return "success"
    if concl in _GH_RUNNING or status in ("IN_PROGRESS", "QUEUED", "PENDING"):
        return "running"
    return "not_started"


def _ci_branch_gh(repo: str, branch: str, cwd: str | None, host: str = "") -> tuple[str, str]:
    """(status, url) for all workflow runs of *branch*'s last commit (gh)."""
    out = run_cmd("gh", "run", "list", "--repo", repo, "--branch", branch,
                  "--limit", "20", "--json", "conclusion,status,url,name,workflowName,headSha", cwd=cwd)
    try:
        data = json.loads(out or "[]")
    except json.JSONDecodeError:
        raise HarnessError(f"cannot parse gh runs for {branch}")
    if not isinstance(data, list) or not data:
        return "not_started", ""
    head_sha = _gh_branch_head_sha(repo, branch, cwd, host)
    same_sha = _filter_commit_items(data, head_sha, "headSha")
    if not same_sha:
        return "not_started", ""
    ignore_patterns = _repo_ci_ignore(cwd)
    if ignore_patterns:
        kept = [
            r for r in same_sha
            if not (
                _matches_ci_ignore(str(r.get("name") or ""), ignore_patterns)
                or _matches_ci_ignore(str(r.get("workflowName") or ""), ignore_patterns)
            )
        ]
        selected = kept if kept else [same_sha[0]]
    else:
        selected = same_sha
    worst = "not_started"
    worst_url = ""
    any_url = ""
    for run in selected:
        state = _gh_run_state(run)
        url = str(run.get("url") or "")
        if url and not any_url:
            any_url = url
        if _CI_RANK[state] > _CI_RANK[worst]:
            worst = state
            worst_url = url
        elif state == worst and not worst_url and url:
            worst_url = url
    return worst, worst_url or any_url

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
            return _ci_branch_gh(path, branch, cwd, host)
        if tool == "glab":
            return _ci_branch_glab(host or "gitlab.com", path, branch, cwd)
        raise HarnessError(f"unknown host CLI: {tool}")
    except HarnessError as e:
        print(f"warning: branch ci lookup failed for {branch}: {e}", file=sys.stderr)
        return None, ""

