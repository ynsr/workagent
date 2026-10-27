"""`release fix-default`: rebase the default branch onto release / fix its CI.

`plan_fix_default` is the dry-run planner; `run_fix_default` executes it. The
rebase step asks the host to rewrite origin/<default> through the
default->release MR (`glab mr rebase`, `gh api .../rebase`) and falls back to a
harness prompt when the host cannot do it — a fallback is always available, so
a failing host API never aborts the run. The CI step hands the failing pipeline
URL plus a trimmed log tail to the harness.

Run modes mirror the rest of the CLI: `preview` (default) prints the exact
harness command — never `-p`, which is non-TTY-only — and executes nothing;
`headless` runs it blocking; `terminal` spawns it in the OS terminal. Both
steps of `--action both` share one harness session: their prompts are
concatenated rebase-first when the run actually launches.
"""
from __future__ import annotations

import re
import sys
from typing import Optional
from urllib.parse import quote

from . import refs_ci, store
from . import repos_release as _rr
from .cli_core import (
    EXIT_USAGE,
    _catch_harness_errors,
    _fail,
    _print_result,
    eprint,
)
from .cli_harness import _run_harness, append_extra_prompt
from .errors import HarnessError, run_cmd
from .release_util import validate_unique_repo_names

#: Run modes shared with the rest of the CLI (preview prints, never executes).
RUN_MODES = ("preview", "headless", "terminal")
#: Tail of the failing job log injected into the CI prompt (chars).
CI_LOG_CHARS = 4000

_GH_PR_URL = re.compile(r"^https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)")
_GL_MR_URL = re.compile(r"^https?://[^/]+/(.+?)/-/merge_requests/(\d+)")
_GH_RUN_ID = re.compile(r"/actions/runs/(\d+)")
_GL_JOB_ID = re.compile(r"/jobs/(\d+)")


# ── registry / git / CI lookups ───────────────────────────────────────


def _repo_ctx(name: str) -> dict:
    """Registry context for one repo (raises exit-2 when unusable)."""
    registry = store.load_repos()
    info = validate_unique_repo_names(registry, name)
    path = str(info.get("path", "") or "")
    default = str(info.get("default_branch", "") or "")
    release_branch = str(info.get("release_branch", "") or "")
    if not default or not release_branch:
        raise HarnessError(
            f"{name}: missing default or release branch "
            f"(repo update {name} --release-branch MASTER).", exit_code=2)
    if default == release_branch:
        raise HarnessError(f"{name}: default and release branch are the same.",
                           exit_code=2)
    return {"name": name, "path": path, "default_branch": default,
            "release_branch": release_branch,
            "tool": str(info.get("tool", "") or "glab"),
            "remote": str(info.get("remote", "") or "")}


def _ci_status(tool: str, path: str, remote: str,
               branch: str) -> tuple[str, str]:
    """(status, job_url) of *branch*'s newest pipeline; ("unknown", "") on error.

    This is the release card's branch lookup: a PR/MR URL is not available for
    a standalone default branch, so the host lookup needs the git remote.
    """
    status, url = refs_ci.fetch_branch_ci(tool, remote, branch, path)
    return (status or "unknown"), (url or "")


def _branch_sha(path: str, branch: str) -> str:
    """origin/<branch> SHA ("" when the ref cannot be resolved)."""
    try:
        return _rr._origin_tip(path, branch) or ""
    except HarnessError:
        return ""


def _ci_logs(tool: str, path: str, remote: str, job_url: str,
             limit: int = CI_LOG_CHARS) -> str:
    """Tail of the failing job's log; "" on any failure (never raises)."""
    if not job_url:
        return ""
    host, project = refs_ci._split_remote(remote)
    if not project:
        return ""
    try:
        if tool == "gh":
            m = _GH_RUN_ID.search(job_url)
            if not m:
                return ""
            out = run_cmd("gh", "run", "view", m.group(1), "--log-failed",
                          "--repo", project, cwd=path, echo=False)
        else:
            m = _GL_JOB_ID.search(job_url)
            if not m:
                return ""
            out = refs_ci._api_glab(
                f"projects/{quote(project, safe='')}/jobs/{m.group(1)}/trace",
                host or "gitlab.com", path)
    except Exception as e:  # logs are advisory: the URL alone is actionable
        eprint(f"warning: CI log fetch failed: {e}")
        return ""
    text = (out or "").strip()
    return text[-limit:] if len(text) > limit else text


# ── host rebase ───────────────────────────────────────────────────────


def _remote_rebase(tool: str, path: str, mr_url: str) -> None:
    """Ask the host to rebase the MR source (<default>) onto its target (<release>).

    Raises HarnessError when the host cannot honour the request; the caller
    always has the harness fallback, so this never aborts the run.
    """
    if tool == "gh":
        m = _GH_PR_URL.match(mr_url or "")
        if not m:
            raise HarnessError(f"not a GitHub PR URL: {mr_url!r}")
        run_cmd("gh", "api", "-X", "PUT",
                f"repos/{m.group(1)}/pulls/{m.group(2)}/rebase", cwd=path)
    else:
        m = _GL_MR_URL.match(mr_url or "")
        if not m:
            raise HarnessError(f"not a GitLab MR URL: {mr_url!r}")
        run_cmd("glab", "mr", "rebase", m.group(2), cwd=path)


# ── prompts ───────────────────────────────────────────────────────────


def _at(sha: str) -> str:
    """` (<sha>)` when known, else nothing (never a bare empty parens)."""
    return f" ({sha})" if sha else ""


def _rebase_prompt(ctx: dict, default_sha: str, release_sha: str) -> str:
    default, release = ctx["default_branch"], ctx["release_branch"]
    return (f"Rebase {default}{_at(default_sha)} onto {release}"
            f"{_at(release_sha)} using rebase strategy; push {default}; "
            "do not create merge commits")


def _ci_prompt(default: str, url: str, logs: str) -> str:
    head_line = f"Fix the failing CI on {default}: {url}"
    return f"{head_line}\n{logs}" if logs else head_line


def _rebase_step(ctx: dict, result: dict,
                 run_mode: str) -> Optional[str]:
    """Rebase remotely; return the harness prompt only when that failed."""
    default, release = ctx["default_branch"], ctx["release_branch"]
    prompt = _rebase_prompt(ctx, _branch_sha(ctx["path"], default),
                            _branch_sha(ctx["path"], release))
    if run_mode == "preview":
        result["rebase"] = "harness-preview"
        return prompt
    try:
        # The MR is source=<default> -> target=<release> (same MR the release
        # flow ships): a host rebase of that MR rewrites the default branch
        # onto the release tip — exactly the fix wanted here.
        mr_url = _find_or_open_mr(ctx["tool"], ctx["path"], default, release,
                                  f"Fix default {default} -> {release}")
        result["mr_url"] = mr_url
        _remote_rebase(ctx["tool"], ctx["path"], mr_url)
    except Exception as e:  # fallback is always available: never raise here
        result["rebase"] = "harness"
        result["rebase_fallback"] = True
        eprint(f"{ctx['name']}: remote rebase unavailable ({e}) — "
               "falling back to the harness")
        return prompt
    result["rebase"] = "remote-rebase"
    eprint(f"{ctx['name']}: rebased {default} onto {release} via {ctx['tool']} "
           f"({result.get('mr_url', '')})")
    return None


def _ci_step(ctx: dict, result: dict) -> str:
    url = str(result.get("ci_url", "") or "")
    logs = _ci_logs(ctx["tool"], ctx["path"], ctx["remote"], url)
    if logs:
        result["ci_log_chars"] = len(logs)
    return _ci_prompt(ctx["default_branch"], url or "(pipeline url unknown)",
                      logs)


def _confirm_fix(ctx: dict, yes: bool) -> None:
    """Confirm before a rewrite: rebasing the default branch needs a force-push."""
    if yes:
        return
    default, release = ctx["default_branch"], ctx["release_branch"]
    msg = (f"fix default for {ctx['name']}: rewrite origin/{default} by "
           f"rebasing it onto {release} (force-push of {default}) and/or let "
           "the harness fix CI?")
    if not sys.stdin.isatty():
        _fail(f"{msg} needs --yes in non-interactive mode", EXIT_USAGE)
    print(f"{msg} [y/N] ", file=sys.stderr, end="")
    try:
        answer = sys.stdin.readline().strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        _fail("aborted", EXIT_USAGE)


# ── plan / execute ────────────────────────────────────────────────────


def plan_fix_default(name: str, action: str) -> dict:
    """Dry-run plan for fixing the default branch (never opens an MR).
    Returns exactly: repo, action, default_branch, release_branch, ahead,
    behind, ci, ci_url, plan. Raises HarnessError (exit 2) when the repo is
    unknown or its default/release branches are missing or identical —
    mirroring validate_release. The default-branch CI lookup soft-fails to
    "unknown" (warning to stderr), never raises.
    """
    if action not in ("rebase", "ci", "both"):
        raise HarnessError(f"bad --action {action!r}: use rebase, ci, or both",
                           exit_code=2)
    ctx = _repo_ctx(name)
    default, release_branch = ctx["default_branch"], ctx["release_branch"]
    _rr._fetch_refs(ctx["path"], default, release_branch, "remote")
    ahead, behind = _rr._count_both(ctx["path"], default, release_branch)
    try:
        ci, ci_url = _ci_status(ctx["tool"], ctx["path"], ctx["remote"], default)
    except Exception as e:  # never raise: CI is advisory to the plan
        print(f"warning: CI lookup failed for {default}: {e}", file=sys.stderr)
        ci, ci_url = "unknown", ""
    parts: list[str] = []
    if action in ("rebase", "both") and behind > 0:
        parts.append(f"open {default}->{release_branch} MR + remote rebase, "
                     "fallback local harness")
    if action in ("ci", "both") and ci == "failure":
        parts.append("fix failing default-branch CI via harness")
    if parts:
        plan = "; ".join(parts)
    else:
        why: list[str] = []
        if action in ("rebase", "both"):
            why.append(f"default not behind {release_branch} (behind=0)")
        if action in ("ci", "both"):
            why.append(f"CI is {ci}")
        plan = "noop: " + "; ".join(why)
    return {"repo": name, "action": action, "default_branch": default,
            "release_branch": release_branch, "ahead": ahead,
            "behind": behind, "ci": ci, "ci_url": ci_url, "plan": plan}


@_catch_harness_errors
def run_fix_default(name: str, *, action: str, run_mode: str = "preview",
                    extra_prompt: Optional[str] = None, yes: bool = False,
                    dry_run: bool = False, json_output: bool = False,
                    env_file: Optional[str] = None) -> None:
    """Execute (or preview) the fix-default plan for one repo."""
    if run_mode not in RUN_MODES:
        _fail(f"bad --run-mode {run_mode!r}: use "
              f"{', '.join(RUN_MODES)}", EXIT_USAGE)
    if not isinstance(extra_prompt, str):
        extra_prompt = None
    result = plan_fix_default(name, action)
    ctx = _repo_ctx(name)
    result["command"] = "release"
    result["run_mode"] = run_mode
    # Branch-scoped session dir: the branch this fix rewrites is the default.
    result["branch"] = ctx["default_branch"]
    rebase_wanted = action in ("rebase", "both") and result["behind"] > 0
    ci_wanted = action in ("ci", "both") and result["ci"] == "failure"
    if dry_run or not (rebase_wanted or ci_wanted):
        if not dry_run:
            eprint(f"{name}: nothing to fix (behind={result['behind']}, "
                   f"ci={result['ci']})")
        _print_result(result, json_output)
        return
    if run_mode != "preview":
        _confirm_fix(ctx, yes)
    prompts: list[str] = []
    if rebase_wanted:
        rebase_prompt = _rebase_step(ctx, result, run_mode)
        if rebase_prompt:
            prompts.append(rebase_prompt)
    if ci_wanted:
        prompts.append(_ci_step(ctx, result))
    if not prompts:
        # The host did every step: nothing for the harness to do.
        eprint(f"{name}: nothing left for the harness")
        _print_result(result, json_output)
        return
    prompt = append_extra_prompt("\n\n".join(prompts), extra_prompt)
    result["harness_prompt"] = prompt
    harness_name = store.load_config().get("default_harness", "omp")
    _run_harness(harness_name, prompt, ctx["path"], ctx["path"],
                 run_mode in ("headless", "terminal"),
                 run_mode == "headless", result, json_output,
                 terminal=run_mode == "terminal", env_file=env_file)


def _find_or_open_mr(tool: str, path: str, default_branch: str,
                     release_branch: str, title: str) -> str:
    """Reuse the open default->release MR, else open it (release-flow helper)."""
    from . import cli_release as _rel
    return _rel._find_or_open_mr(tool, path, default_branch, release_branch,
                                 title)
