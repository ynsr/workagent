"""Release helpers: branch detection, diff count, backfill, SWR status.

Split out of repos.py to keep both modules under the 500-line project
limit. Public surface is re-exported by repos.py for compatibility.
"""

import sys
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


def _behind_count(path: str, default_branch: str, release_branch: str) -> int:
    """Commits *release_branch* has that *default_branch* lacks (int >= 0).

    Best-effort local rev-list (0 on unknown refs) — the behind signal is
    advisory to the fix-default button, never a hard gate.
    """
    try:
        return _count_both(path, default_branch, release_branch)[1]
    except Exception:
        return 0


def _subjects(path: str, default_branch: str, release_branch: str) -> list[str]:
    """First-line subjects for origin/<release>..origin/<default> (oldest first)."""
    out = run_cmd("git", "-C", path, "log", "--format=%s", "--reverse",
                  f"origin/{release_branch}..origin/{default_branch}") or ""
    return [line for line in (s.strip() for s in out.splitlines()) if line]

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
            "diff_count": ahead, "strategy": caps["strategy"],
            "commit_subjects": _subjects(path, default, release)}


def _diff_ttl_seconds() -> int:
    """Fresh TTL for the release diff count (default 1h; WORKAGENT_RELEASE_DIFF_TTL_SECONDS)."""
    from .swr import cache_ttl as _ttl
    return _ttl("WORKAGENT_RELEASE_DIFF_TTL_SECONDS", 3600)


def _diff_swr_seconds() -> int:
    """SWR window for the release diff count (env or max(ttl*10, 1d))."""
    from .swr import cache_swr as _swr_window
    return _swr_window(_diff_ttl_seconds(), "WORKAGENT_RELEASE_DIFF_SWR_SECONDS")




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


def _dirty_files(path: str) -> list[str]:
    """Uncommitted status lines for *path* (empty when clean or unreadable)."""
    if not path or not Path(path).expanduser().is_dir():
        return []
    try:
        out = run_cmd("git", "-C", str(Path(path).expanduser()), "status", "--porcelain", echo=False) or ""
    except (HarnessError, OSError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _ref_exists(path: Path, ref: str) -> bool:
    """True when *ref* resolves in *path*."""
    try:
        run_cmd("git", "-C", str(path), "rev-parse", "--verify", "--quiet", ref, echo=False)
        return True
    except (HarnessError, OSError):
        return False


def _has_remote_diff(path: str, default_branch: str) -> bool:
    """True when local HEAD differs from its remote tracking branch (or unknown)."""
    p = Path(path).expanduser()
    if not path or not p.is_dir():
        return True
    try:
        branch = (run_cmd("git", "-C", str(p), "branch", "--show-current", echo=False) or "").strip()
        target = branch or default_branch
        if not target or not _ref_exists(p, f"refs/remotes/origin/{target}"):
            return True
        local_sha = (run_cmd("git", "-C", str(p), "rev-parse", "HEAD", echo=False) or "").strip()
        remote_sha = (run_cmd("git", "-C", str(p), "rev-parse", f"refs/remotes/origin/{target}", echo=False) or "").strip()
        if not local_sha or not remote_sha:
            return True
        return local_sha != remote_sha
    except (HarnessError, OSError):
        return True


def _normalize_tracking(path: Path, branch: str) -> None:
    """Repair ``branch.<branch>`` tracking so ``origin/<branch>`` merges cleanly.

    Fixes duplicated ``branch.<branch>.merge`` entries (unambiguous
    corruption — ``git pull`` refuses with "more than one value"). A remote
    deliberately pointed at something other than ``origin`` is left alone:
    retargeting it would silently change where the user's next push/pull
    goes. The explicit ``merge origin/<branch>`` in :func:`pull_repo`
    still fetches from origin for this pull only.
    """
    expected_merge = f"refs/heads/{branch}"
    try:
        remote = (run_cmd("git", "-C", str(path), "config", "--get", f"branch.{branch}.remote", echo=False) or "").strip()
    except HarnessError:
        remote = ""
    try:
        merges_raw = run_cmd("git", "-C", str(path), "config", "--get-all", f"branch.{branch}.merge", echo=False) or ""
        merges = [line.strip() for line in merges_raw.splitlines() if line.strip()]
    except HarnessError:
        merges = []
    if remote and remote != "origin":
        print(f"warning: branch {branch} tracks remote {remote!r}, leaving it; pulling from origin this time",
              file=sys.stderr)
    elif remote != "origin":
        run_cmd("git", "-C", str(path), "config", f"branch.{branch}.remote", "origin", echo=False)
    if merges != [expected_merge]:
        run_cmd("git", "-C", str(path), "config", "--replace-all", f"branch.{branch}.merge", expected_merge, echo=False)


def pull_repo(repo_name: str) -> dict:
    """Fast-forward pull *repo_name*'s local checkout from origin."""
    info = store.load_repos().get(repo_name)
    if not info:
        raise HarnessError(f"unknown repo: {repo_name}", exit_code=2)
    path = Path(str(info.get("path", "") or "")).expanduser()
    if not path.is_dir():
        raise HarnessError(f"repo path missing: {path}", exit_code=1)
    dirty = _dirty_files(str(path))
    if dirty:
        preview = ", ".join(dirty[:5])
        more = f" (+{len(dirty) - 5} more)" if len(dirty) > 5 else ""
        raise HarnessError(f"repo {repo_name} has uncommitted changes: {preview}{more}", exit_code=1)
    run_cmd("git", "-C", str(path), "fetch", "--prune", "origin")
    branch = (run_cmd("git", "-C", str(path), "branch", "--show-current", echo=False) or "").strip()
    if not branch:
        raise HarnessError(f"repo {repo_name} is in detached HEAD state", exit_code=1)
    default_branch = str(info.get("default_branch", "") or "")
    switched_from = ""
    if not _ref_exists(path, f"refs/remotes/origin/{branch}"):
        if default_branch and branch != default_branch and _ref_exists(path, f"refs/remotes/origin/{default_branch}"):
            if _ref_exists(path, f"refs/heads/{default_branch}"):
                run_cmd("git", "-C", str(path), "checkout", default_branch)
            else:
                run_cmd("git", "-C", str(path), "checkout", "-b", default_branch, f"origin/{default_branch}")
            switched_from = branch
            branch = default_branch
        else:
            raise HarnessError(f"remote branch origin/{branch} not found", exit_code=1)
    _normalize_tracking(path, branch)
    out = run_cmd("git", "-C", str(path), "merge", "--ff-only", f"origin/{branch}") or ""
    from . import store_sqlite as _sq
    try:
        db = _sq.db_path()
        with _sq.connect(db) as conn:
            conn.execute("DELETE FROM release_diff_cache WHERE repo_key = ?", (repo_name,))
    except Exception:
        pass
    return {"pulled": repo_name, "path": str(path), "branch": branch,
            "switched_from": switched_from, "output": out.strip()}


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
    from . import release_lock as _lock
    from .release_util import worktree_base as _wt_base
    try:
        base_dir = str(_wt_base())
    except Exception:
        base_dir = ""
    base = {"repo": repo_name, "path": "", "default_branch": "", "release_branch": "",
            "diff_count": 0, "behind": 0, "releasable": False, "reason": "", "stale": False,
            "ci": "", "ci_url": "", "fetched_at": None, "releasing": False,
            "dirty": False, "dirty_files": [], "has_remote_diff": True}
    if base_dir:
        try:
            base["releasing"] = _lock.locked(_lock.lock_path(base_dir, repo_name))
        except Exception:
            base["releasing"] = False
    if not info:
        base["reason"] = "unknown repo"
        return base
    default = str(info.get("default_branch", "") or "")
    release = str(info.get("release_branch", "") or "")
    dirty = _dirty_files(str(info.get("path", "")))
    has_remote_diff = _has_remote_diff(str(info.get("path", "")), default)
    base.update(path=str(info.get("path", "")), default_branch=default, release_branch=release,
                dirty=bool(dirty), dirty_files=dirty, has_remote_diff=has_remote_diff)
    if not release:
        base["reason"] = "no release branch"
        return base
    if not default:
        base["reason"] = "no default branch"
        return base
    if default == release:
        base["reason"] = "default and release branch are the same"
        return base
    from . import store_sqlite as _sq
    from .cli_repo_util import _ci_swr, _ci_ttl
    import json as _json

    try:
        db = _sq.db_path()
    except Exception:
        db = None
    cached_entry: dict | None = None
    if db is not None:
        try:
            cached_entry = _sq.get_release_diff_cache_entry(db, repo_name)
        except Exception:
            cached_entry = None

    branches_match = (
        cached_entry is not None
        and (not cached_entry.get("default_branch") or cached_entry.get("default_branch") == default)
        and (not cached_entry.get("release_branch") or cached_entry.get("release_branch") == release)
    )
    cached: int | None = cached_entry["count"] if branches_match else None
    fetched_at: str = cached_entry["fetched_at"] if branches_match else ""

    # Branch CI with SWR over release_diff_cache.ci_json / ci_fetched_at
    ci_fetched_live: tuple[str, str, str] | None = None  # (ci_json, ci_fetched_at, ...)
    def _fetch_branch_ci_live() -> tuple[str, str]:
        from . import refs_ci as _ci
        tool = str(info.get("tool", "") or "")
        remote = str(info.get("remote", "") or "")
        ci, ci_url = _ci.fetch_branch_ci(tool, remote, release, str(info.get("path", "")))
        return ci or "", ci_url or ""

    def _refresh_ci_bg() -> None:
        try:
            ci, ci_url = _fetch_branch_ci_live()
            if db is not None:
                _sq.set_release_branch_ci_cache(
                    db,
                    repo_name,
                    _json.dumps({"ci": ci, "ci_url": ci_url}),
                    default_branch=default,
                    release_branch=release,
                )
        except Exception:
            pass

    cached_ci_json = cached_entry.get("ci_json", "") if branches_match else ""
    cached_ci_ts = cached_entry.get("ci_fetched_at", "") if branches_match else ""
    ci_served = False
    if not force and cached_ci_json and cached_ci_ts:
        try:
            ci_age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached_ci_ts)).total_seconds()
            ci_data = _json.loads(cached_ci_json)
            if isinstance(ci_data, dict):
                if ci_age < _ci_ttl():
                    base["ci"] = str(ci_data.get("ci") or ci_data.get("status") or "")
                    base["ci_url"] = str(ci_data.get("ci_url") or "")
                    ci_served = True
                elif ci_age < _ci_swr():
                    base["ci"] = str(ci_data.get("ci") or ci_data.get("status") or "")
                    base["ci_url"] = str(ci_data.get("ci_url") or "")
                    _swr.serve_stale_or_refresh(ci_data, False, _refresh_ci_bg, f"release-ci:{repo_name}")
                    ci_served = True
        except Exception:
            ci_served = False

    if not ci_served:
        try:
            ci, ci_url = _fetch_branch_ci_live()
            base["ci"], base["ci_url"] = ci, ci_url
            now_ci_ts = datetime.now(timezone.utc).isoformat()
            ci_payload = _json.dumps({"ci": ci, "ci_url": ci_url})
            ci_fetched_live = (ci_payload, now_ci_ts)
            if db is not None and branches_match:
                try:
                    _sq.set_release_branch_ci_cache(
                        db, repo_name, ci_payload, now_ci_ts,
                        default_branch=default, release_branch=release,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _live() -> int:
        return release_diff_count(Path(str(info["path"])).expanduser(), default, release)

    def _finish(count: int, stale: bool, reason: str = "") -> dict:
        try:
            behind = _behind_count(str(info["path"]), default, release)
        except Exception:
            behind = 0
        base.update(diff_count=count, behind=behind, releasable=count > 0, stale=stale,
                    reason="" if count > 0 else "already in sync",
                    has_remote_diff=_has_remote_diff(str(info.get("path", "")), default),
                    fetched_at=base.get("fetched_at") or (fetched_at or "") or None)
        if reason:
            base["reason"] = reason
        return base

    if cached is not None and not force:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
        except Exception:
            age = float("inf")
        if age < _diff_ttl_seconds():
            return _finish(cached, False)
        if age < _diff_swr_seconds():
            def _refresh() -> None:
                try:
                    _sq.set_release_diff_cache(
                        db, repo_name, _live(),
                        default_branch=default, release_branch=release,
                    )
                except Exception:
                    pass
            _swr.serve_stale_or_refresh(stale=True, is_fresh=False, refresh=_refresh,
                                        key=f"release:{repo_name}")
            return _finish(cached, True)
    try:
        count = _live()
    except (HarnessError, Exception) as e:
        if cached is not None:
            base.update(diff_count=cached, releasable=cached > 0, stale=True,
                        fetched_at=fetched_at or None,
                        reason=f"cannot reach remote ({e}); showing cached count")
            return base
        base["reason"] = f"cannot reach remote ({e})"
        return base
    if db is not None:
        try:
            if ci_fetched_live is not None:
                _sq.set_release_diff_cache(
                    db, repo_name, count,
                    default_branch=default, release_branch=release,
                    ci_json=ci_fetched_live[0], ci_fetched_at=ci_fetched_live[1],
                )
            else:
                _sq.set_release_diff_cache(
                    db, repo_name, count,
                    default_branch=default, release_branch=release,
                )
            _, fetched_at = _sq.get_release_diff_cache(db, repo_name)
        except Exception:
            pass
    return _finish(count, False)
