"""Merge-capability cache: per-repo strategy list, TTL 10m.

Strategies: "gitlab-mr-ff" (glab mr merge --sha), "ff-push" (ff-only push
of the pinned SHA), "github-pr-merge-commit" (gh pr merge --merge
--match-head-commit). Auth failures never cached; invalidate on rejection.
"""
from __future__ import annotations

import json
import re
import time

from .errors import HarnessError, run_cmd

_TTL = 600
_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}
_AUTH_RE = re.compile(r"401|403|auth|token|unauthorized", re.IGNORECASE)


def _check_auth(e: HarnessError) -> None:
    if _AUTH_RE.search(str(e)):
        raise e


def _origin_remote(path: str) -> str:
    try:
        return run_cmd("git", "-C", path, "remote", "get-url", "origin", echo=False) or ""
    except HarnessError:
        return ""


def _glab_merge_method(path: str) -> str:
    """Project merge_method via glab api; 'ff' or other."""
    from .refs_ci import _split_remote
    host, proj = _split_remote(_origin_remote(path))
    if not proj:
        raise HarnessError(f"cannot parse origin remote for {path}", exit_code=2)
    try:
        out = run_cmd("glab", "api", f"projects/{proj.replace('/', '%2F')}",
                      "--hostname", host or "gitlab.com", echo=False)
    except HarnessError as e:
        _check_auth(e)
        raise
    try:
        return str(json.loads(out or "{}").get("merge_method") or "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _gh_caps(path: str, release_branch: str) -> dict:
    """{'allow_merge_commit': bool, 'linear_required': bool, 'pr_required': bool}."""
    from .refs_ci import _split_remote
    _, proj = _split_remote(_origin_remote(path))
    if not proj:
        raise HarnessError(f"cannot parse origin remote for {path}", exit_code=2)
    try:
        out = run_cmd("gh", "api", f"repos/{proj}", echo=False)
    except HarnessError as e:
        _check_auth(e)
        raise
    try:
        repo = json.loads(out or "{}")
    except json.JSONDecodeError:
        repo = {}
    caps = {"allow_merge_commit": bool(repo.get("allow_merge_commit", True)),
            "linear_required": False, "pr_required": False}
    try:
        prot = run_cmd("gh", "api", f"repos/{proj}/branches/{release_branch}",
                       "--jq", ".protection", echo=False)
        caps["pr_required"] = bool((prot or "").strip() not in ("", "null"))
    except HarnessError as e:
        _check_auth(e)
    return caps


def invalidate(tool: str, path: str, release_branch: str) -> None:
    _CACHE.pop((tool, path, release_branch), None)


def get_caps(tool: str, path: str, release_branch: str) -> dict:
    key = (tool, path, release_branch)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    if tool == "glab":
        method = _glab_merge_method(path)
        strategy = ["gitlab-mr-ff"] if method == "ff" else ["ff-push"]
        raw: dict = {"merge_method": method}
    elif tool == "gh":
        caps = _gh_caps(path, release_branch)
        strategy = ["ff-push"]
        if caps.get("allow_merge_commit"):
            strategy.append("github-pr-merge-commit")
        raw = dict(caps)
    else:
        raise HarnessError(f"unknown host CLI: {tool}", exit_code=2)
    if not strategy:
        raise HarnessError(f"no merge strategy available for {path}", exit_code=1)
    result = {"strategy": strategy, "raw": raw}
    _CACHE[key] = (time.monotonic(), result)
    return result
