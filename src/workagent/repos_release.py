"""Release helpers: branch detection, diff count, backfill, SWR status.

Split out of repos.py to keep both modules under the 500-line project
limit. Public surface is re-exported by repos.py for compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import repos as _repos
from . import store
from .errors import HarnessError, run_cmd


def detect_release_branch(path: Path) -> str:
    """Release branch for *path*: '' when none configured.

    Jibit (glab-tooled) repos prefer `master` when it exists, else `main`
    when it exists, else ''. Non-glab repos return '' (release support is
    opt-in via `repo update --release-branch`). Never raises: probe
    failures mean ''.
    """
    try:
        host_cli = _repos._detect_host_cli(path)  # noqa: SLF001 — same package
    except Exception:
        return ""
    if host_cli != "glab":
        return ""
    run = _repos.run_cmd if hasattr(_repos, "run_cmd") else run_cmd
    for candidate in ("master", "main"):
        for ref in (f"origin/{candidate}", candidate):
            try:
                run("git", "-C", str(path), "rev-parse", "--verify", ref)
                return candidate
            except HarnessError:
                continue
            except Exception:
                return ""
    return ""


def release_diff_count(path: Path, default_branch: str, release_branch: str) -> int:
    """Commits *default_branch* has that *release_branch* lacks (int >= 0).

    Fetches both tips first so shallow clones (depth 7) count correctly;
    falls back to --unshallow when the release ref is missing locally.
    Raises HarnessError when offline or when either ref is unknown.
    """
    if not default_branch or not release_branch:
        raise HarnessError("release diff needs both default and release branches", exit_code=2)
    if default_branch == release_branch:
        raise HarnessError(f"default and release branch are the same ({default_branch!r})",
                           exit_code=2)
    run_cmd("git", "-C", str(path), "fetch", "origin", default_branch, release_branch)
    try:
        out = run_cmd("git", "-C", str(path), "rev-list", "--count",
                      f"origin/{release_branch}..origin/{default_branch}")
    except HarnessError:
        run_cmd("git", "-C", str(path), "fetch", "--unshallow", "origin")
        out = run_cmd("git", "-C", str(path), "rev-list", "--count",
                      f"origin/{release_branch}..origin/{default_branch}")
    return int((out or "0").strip())
def _fetch_refs(path: str, default_branch: str, release_branch: str, diff_mode: str) -> None:
    """Fetch both refs; diff_mode is remote (per-branch fetch) or local (full fetch)."""
    if diff_mode == "local":
        run_cmd("git", "-C", path, "fetch", "origin")
    else:
        run_cmd("git", "-C", path, "fetch", "origin", default_branch, release_branch)


def _count_both(path: str, default_branch: str, release_branch: str) -> tuple[int, int]:
    """(ahead, behind): commits default has that release lacks, and vice versa."""
    ahead = int((run_cmd("git", "-C", path, "rev-list", "--count",
                         f"origin/{release_branch}..origin/{default_branch}") or "0").strip())
    behind = int((run_cmd("git", "-C", path, "rev-list", "--count",
                          f"origin/{default_branch}..origin/{release_branch}") or "0").strip())
    return ahead, behind


def _origin_tip(path: str, branch: str) -> str:
    """SHA of origin/<branch>."""
    return (run_cmd("git", "-C", path, "rev-parse", f"origin/{branch}") or "").strip()


def _auth_ok(tool: str, path: str) -> bool:
    """Host CLI installed and authenticated."""
    from .cli_repo_util import _repo_tool  # noqa: PLC0415 — avoid import cycle
    try:
        if tool == "gh":
            run_cmd("gh", "auth", "status", cwd=path, echo=False)
        elif tool == "glab":
            run_cmd("glab", "auth", "status", cwd=path, echo=False)
        else:
            _repo_tool(path)
        return True
    except Exception:
        return False


def validate_release(name: str, diff_mode: str = "remote") -> dict:
    """Step 1 validation returning the release plan context (raises via _fail-free raises).

    Keys: repo, path, tool, default_branch, release_branch, pinned_sha,
    behind, ahead, diff_count. ahead == 0 returns {"note": "already in sync"}.
    Raises HarnessError: exit 2 for config errors (1a/1b), exit 1 for
    diverged/auth/strategy failures.
    """
    from . import release_caps as _caps
    from .release_util import validate_unique_repo_names
    registry = store.load_repos()
    info = validate_unique_repo_names(registry, name)
    path = str(info.get("path", "") or "")
    default = str(info.get("default_branch", "") or "")
    release = str(info.get("release_branch", "") or "")
    if not default or not release:
        raise HarnessError(f"{name}: missing default or release branch "
                           f"(repo update {name} --release-branch MASTER).", exit_code=2)
    if default == release:
        raise HarnessError(f"{name}: default and release branch are the same.", exit_code=2)
    tool = str(info.get("tool", "") or "glab")
    _fetch_refs(path, default, release, diff_mode)
    ahead, behind = _count_both(path, default, release)
    if behind > 0:
        raise HarnessError(f"{name}: release branch is ahead of default ({behind} commits); "
                           "merge release back into default first.", exit_code=1)
    if ahead == 0:
        return {"repo": name, "merged": False, "mr_url": "",
                "note": "already in sync, nothing to release"}
    if not _auth_ok(tool, path):
        raise HarnessError(f"{name}: {tool} missing or unauthenticated.", exit_code=1)
    pinned = _origin_tip(path, default)
    caps = _caps.get_caps(tool, path, release)
    if not caps.get("strategy"):
        raise HarnessError(f"{name}: no merge strategy available.", exit_code=1)
    return {"repo": name, "path": path, "tool": tool,
            "default_branch": default, "release_branch": release,
            "pinned_sha": pinned, "behind": behind, "ahead": ahead,
            "diff_count": ahead, "strategy": caps["strategy"]}


_RELEASE_DIFF_TTL_SECONDS = 3600
_RELEASE_DIFF_SWR_SECONDS = max(_RELEASE_DIFF_TTL_SECONDS * 10, 24 * 3600)


def backfill_release_branches() -> None:
    """Seed default/release branches for repos missing them. Never raises.

    Per-repo failures (unreachable host, missing checkout) leave '' and
    emit a stderr warning — backfill must never break startup.
    """
    import sys
    for name, info in store.load_repos().items():
        if info.get("default_branch") and "release_branch" in info:
            continue
        p = Path(str(info.get("path", ""))).expanduser()
        try:
            default = info.get("default_branch") or _repos.default_branch(p)
        except HarnessError as e:
            print(f"warning: cannot detect default branch for {name}: {e}", file=sys.stderr)
            default = ""
        except Exception as e:
            print(f"warning: cannot detect default branch for {name}: {e}", file=sys.stderr)
            default = ""
        try:
            release = info.get("release_branch", "")
            if not release and default:
                release = detect_release_branch(p)
        except HarnessError as e:
            print(f"warning: cannot detect release branch for {name}: {e}", file=sys.stderr)
            release = ""
        except Exception as e:
            print(f"warning: cannot detect release branch for {name}: {e}", file=sys.stderr)
            release = ""
        try:
            from . import store_sqlite as _sq
            db = _sq.db_path()
            conn = _sq.connect(db)
            try:
                conn.execute(
                    "UPDATE repos SET default_branch = ?, release_branch = ? WHERE key_ref = ?",
                    (default, release, name))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"warning: cannot persist branches for {name}: {e}", file=sys.stderr)


def release_status(repo_name: str, force: bool = False, background: bool = False) -> dict:
    """Read-only releasability for *repo_name* (SWR over release_diff_cache).

    Fresh (<1h) serves instantly; expired-but-present (<1d) serves stale +
    one background revalidation; empty/force fetches live. With
    ``background=True`` a fresh cached row is also served instantly while
    one background revalidation is kicked (the 30 s ReleaseCard poll path —
    the component never blocks, fresh data swaps in on the next poll).
    Offline fetch with stale rows serves stale + reason; offline with no
    rows reports reason "cannot reach remote". Never raises for unknown
    repo — returns releasable False with reason.
    """
    from . import swr as _swr
    info = store.load_repos().get(repo_name)
    base = {"repo": repo_name, "path": "", "default_branch": "", "release_branch": "",
            "diff_count": 0, "releasable": False, "reason": "", "stale": False,
            "ci": "", "ci_url": "", "fetched_at": None}
    if not info:
        base["reason"] = "unknown repo"
        return base
    default = str(info.get("default_branch", "") or "")
    release = str(info.get("release_branch", "") or "")
    base.update(path=str(info.get("path", "")), default_branch=default, release_branch=release)
    if not release:
        base["reason"] = "no release branch"
        return base
    if not default:
        base["reason"] = "no default branch"
        return base
    if default == release:
        base["reason"] = "default and release branch are the same"
        return base
    try:
        from . import refs_ci as _ci
        tool = str(info.get("tool", "") or "")
        remote = str(info.get("remote", "") or "")
        ci, ci_url = _ci.fetch_branch_ci(tool, remote, release, str(info.get("path", "")))
        base["ci"], base["ci_url"] = ci or "", ci_url or ""
    except Exception:
        pass
    from . import store_sqlite as _sq
    try:
        db = _sq.db_path()
    except Exception:
        db = None
    cached, fetched_at = (None, "")
    if db is not None and not force:
        try:
            cached, fetched_at = _sq.get_release_diff_cache(db, repo_name)
        except Exception:
            cached, fetched_at = (None, "")
    def _live() -> int:
        return release_diff_count(Path(str(info["path"])).expanduser(), default, release)
    def _finish(count: int, stale: bool, reason: str = "") -> dict:
        base.update(diff_count=count, releasable=count > 0, stale=stale,
                    reason="" if count > 0 else "already in sync",
                    fetched_at=base.get("fetched_at") or (fetched_at or "") or None)
        if reason:
            base["reason"] = reason
        return base
    if cached is not None and not force:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
        except Exception:
            age = float("inf")
        if age < _RELEASE_DIFF_TTL_SECONDS:
            if background and db is not None:
                def _refresh() -> None:
                    try:
                        _sq.set_release_diff_cache(db, repo_name, _live())
                    except Exception:
                        pass
                _swr.serve_stale_or_refresh(stale=True, is_fresh=False, refresh=_refresh,
                                            key=f"release:{repo_name}")
            return _finish(cached, False)
        if age < _RELEASE_DIFF_SWR_SECONDS:
            def _refresh() -> None:
                try:
                    _sq.set_release_diff_cache(db, repo_name, _live())
                except Exception:
                    pass
            _swr.serve_stale_or_refresh(stale=True, is_fresh=False, refresh=_refresh,
                                        key=f"release:{repo_name}")
            return _finish(cached, True)
    try:
        count = _live()
    except HarnessError as e:
        if cached is not None:
            base.update(diff_count=cached, releasable=cached > 0, stale=True,
                        reason=f"cannot reach remote ({e}); showing cached count")
            return base
        base["reason"] = f"cannot reach remote ({e})"
        return base
    except Exception as e:
        if cached is not None:
            base.update(diff_count=cached, releasable=cached > 0, stale=True,
                        reason=f"cannot reach remote ({e}); showing cached count")
            return base
        base["reason"] = f"cannot reach remote ({e})"
        return base
    if db is not None:
        try:
            _sq.set_release_diff_cache(db, repo_name, count)
            _, fetched_at = _sq.get_release_diff_cache(db, repo_name)
        except Exception:
            pass
    return _finish(count, False)
