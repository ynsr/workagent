"""Persistent state: repo registry, tracker links, session links.

Layout (~/.config/workagent/, override with WORKAGENT_CONFIG_DIR):
  config.json   — {"default_harness": "omp", "repos": {...}, "trackers": {...}}
  links.json    — {"<issue-key>": {"issue": ..., "pr_url": ..., "worktree": ...,
                                   "branch": ..., "repo": ...}}
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_HARNESS = "omp"
SUPPORTED_HARNESSES = ("omp",)


def config_dir() -> Path:
    override = os.environ.get("WORKAGENT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    new = Path.home() / ".config" / "workagent"
    legacy = Path.home() / ".config" / "harness"
    if not new.exists() and legacy.exists():
        # One-shot auto-migration from the pre-rename layout (issue #25).
        import shutil
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy, new)
    return new


def _read_json(path: Path, default: dict) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as e:
        raise HarnessError(f"cannot read {path}: {e}")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name per write: shared "<name>.tmp" collides under
    # concurrent writers (CLI + serve threads) and loses the loser with ENOENT.
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        _locked(lock, lambda: _atomic_replace(path, data))


def _locked(lock, fn):
    import fcntl
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    try:
        return fn()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _atomic_replace(path: Path, data: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(path)
    except BaseException:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_config() -> dict:
    cfg = _read_json(config_dir() / "config.json", {})
    cfg.setdefault("default_harness", DEFAULT_HARNESS)
    cfg.setdefault("repos", {})
    cfg.setdefault("trackers", {})
    return cfg


def save_config(cfg: dict) -> None:
    _write_json(config_dir() / "config.json", cfg)


def _sqlite_path():
    try:
        from . import store_sqlite as sq
        p = sq.db_path()
        return p if p.exists() else None
    except Exception:
        return None


def load_links(include_inactive: bool = False) -> dict:
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        return sq.load_links_rows(db, include_inactive=include_inactive)
    links = _read_json(config_dir() / "links.json", {})
    for key, entry in links.items():
        if isinstance(entry, dict):
            entry.setdefault("ref_key", key)
            entry.setdefault("active", 1)
    if not include_inactive:
        links = {k: v for k, v in links.items()
                 if not isinstance(v, dict) or v.get("active", 1) != 0}
    return links


def set_worktree_active(key: str, active: bool) -> None:
    """Flip a worktree's active flag (pure DB; no git/host side effects)."""
    from .errors import HarnessError
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        sq.set_worktree_active(db, key, active)
        return
    path = config_dir() / "links.json"
    links = _read_json(path, {})
    if key not in links:
        raise HarnessError(f"no worktree link for {key}", exit_code=2)
    links[key]["active"] = 1 if active else 0
    save_links(links)


def delete_worktree_row(key: str) -> None:
    """Delete a worktree row only; files/branch/PR untouched."""
    from .errors import HarnessError
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        sq.delete_worktree_row(db, key)
        return
    links = load_links(include_inactive=True)
    if key not in links:
        raise HarnessError(f"no worktree link for {key}", exit_code=2)
    del links[key]
    save_links(links)

def save_links(links: dict) -> None:
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        sq.save_links_rows(db, links)
        return
    _write_json(config_dir() / "links.json", links)

def record_link(issue_key: str, entry: dict) -> None:
    if _sqlite_path() is not None:
        links = load_links()
        merged = {**links.get(issue_key, {}), **entry}
        merged.setdefault("added_at", _now_iso())
        links[issue_key] = merged
        save_links(links)
        return
    path = config_dir() / "links.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        _locked(lock, lambda: _record_link_locked(path, issue_key, entry))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_link_locked(path: Path, issue_key: str, entry: dict) -> None:
    links = _read_json(path, {})
    merged = {**links.get(issue_key, {}), **entry}
    # First-seen stamp; never bumped by later updates (Task 7 sorts by it).
    merged.setdefault("added_at", _now_iso())
    links[issue_key] = merged
    _atomic_replace(path, links)

def load_trackers() -> dict:
    """{tracker_id: {"repos", "vendor", "remote_url}} — SQLite post-migration, else config.json."""
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        return sq.load_trackers(db)
    return load_config().get("trackers", {})


def load_repos() -> dict:
    """{name: {path, trackers, tracker, remote, tool}} — SQLite post-migration, else config.json."""
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        return sq.load_repos(db)
    cfg = load_config()
    tmap: dict = {}
    for tid, entry in (cfg.get("trackers", {}) or {}).items():
        for r in (entry or {}).get("repos", []):
            try:
                key = str(Path(r).expanduser().resolve())
            except Exception:
                continue
            cur = tmap.get(key, "")
            if not cur or (cur.startswith("jira:") and str(tid).startswith(("github:", "gitlab:"))):
                tmap[key] = tid
    out: dict = {}
    for name, v in (cfg.get("repos", {}) or {}).items():
        raw = str((v or {}).get("path", ""))
        try:
            key = str(Path(raw).expanduser().resolve()) if raw else ""
        except Exception:
            key = ""
        out[name] = {"path": raw, "tracker": tmap.get(key, ""),
                     "remote": str((v or {}).get("remote", "") or ""),
                     "tool": (v or {}).get("tool")}
    return out


def add_tracker_repo(tid: str, repo_path: str) -> bool:
    """Link *repo_path* under *tid*; True when newly recorded (dual-write pre-migration)."""
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        return sq.add_tracker_repo(db, tid, repo_path)
    cfg = load_config()
    entry = cfg.setdefault("trackers", {}).setdefault(tid, {"repos": []})
    norm = str(Path(repo_path).expanduser().resolve())
    if norm in [str(Path(r).expanduser().resolve()) for r in entry.get("repos", [])]:
        return False
    entry.setdefault("repos", []).append(norm)
    save_config(cfg)
    return True


def remove_tracker_repo(tid: str, repo_path: str | None = None) -> bool:
    """Drop one repo link (or the whole tracker); dual-write pre-migration."""
    db = _sqlite_path()
    if db is not None:
        from . import store_sqlite as sq
        return sq.remove_tracker_repo(db, tid, repo_path)
    cfg = load_config()
    trackers = cfg.get("trackers", {})
    if tid not in trackers:
        return False
    if repo_path is None:
        del trackers[tid]
        save_config(cfg)
        return True
    norm = str(Path(repo_path).expanduser().resolve())
    kept = [r for r in trackers[tid].get("repos", [])
            if str(Path(r).expanduser().resolve()) != norm]
    if len(kept) == len(trackers[tid].get("repos", [])):
        return False
    if kept:
        trackers[tid]["repos"] = kept
    else:
        del trackers[tid]
    save_config(cfg)
    return True

def lookup_link(issue_key: str) -> dict | None:
    return load_links().get(issue_key)


def load_pr_cache() -> dict:
    db = _sqlite_path()
    if db is not None:
        import json as _json
        import sqlite3 as _sqlite3
        try:
            with _sqlite3.connect(str(db)) as conn:
                conn.row_factory = _sqlite3.Row
                out = {}
                for row in conn.execute("SELECT * FROM pr_cache"):
                    r = dict(row)
                    try:
                        payload = _json.loads(r.get("payload") or "{}")
                    except ValueError:
                        payload = {}
                    entry = payload if isinstance(payload, dict) else {"pr": payload}
                    entry.setdefault("pr", None)
                    entry.setdefault("checked_at", r.get("checked_at", ""))
                    out[r["branch"]] = entry
                return out
        except _sqlite3.OperationalError:
            return _read_json(config_dir() / "pr_cache.json", {})
    return _read_json(config_dir() / "pr_cache.json", {})


def _write_pr_cache(cache: dict) -> None:
    import json as _json
    import sqlite3 as _sqlite3
    from . import store_sqlite as sq
    db = _sqlite_path()
    assert db is not None
    sq.init_db(db)
    with _sqlite3.connect(str(db)) as conn:
        conn.execute("DELETE FROM pr_cache")
        for branch, entry in cache.items():
            payload = dict(entry) if isinstance(entry, dict) else {"pr": entry}
            checked = str(payload.pop("checked_at", "")
                          or datetime.now(timezone.utc).isoformat())
            conn.execute(
                "INSERT INTO pr_cache (branch, payload, checked_at)"
                " VALUES (?, ?, ?)",
                (branch, _json.dumps(payload), checked))


def save_pr_cache(cache: dict) -> None:
    if _sqlite_path() is not None:
        _write_pr_cache(cache)
        return
    _write_json(config_dir() / "pr_cache.json", cache)


def pr_cache_key(repo: str = "", branch: str = "") -> str:
    """pr_cache key namespaced by repo: ``<repo>\\x1f<branch>`` when both known.

    Branch names repeat across repos (IPG-1002 in projectx + ipg-commons),
    so a bare branch key would let one repo's PR/CI/review stats leak into
    the other's status cells. Bare-branch reads stay as fallback so
    pre-migration cache entries keep working.
    """
    return f"{repo}\x1f{branch}" if repo and branch else branch


def lookup_pr_cache(branch: str, repo: str = "") -> dict | None:
    """Cache entry for (repo, branch), falling back to the bare branch key."""
    if not branch:
        return None
    cache = load_pr_cache()
    return cache.get(pr_cache_key(repo, branch), cache.get(branch))

def cache_pr_status(branch: str, pr: dict | None, tool: str | None = None,
                    base_branch: str | None = None, branch_tip: str | None = None,
                    base_tip: str | None = None, behind: int | None = None,
                    ahead: int | None = None, repo: str = "") -> None:
    key = pr_cache_key(repo, branch)
    entry = {"pr": pr, "tool": tool,
             "checked_at": datetime.now(timezone.utc).isoformat()}
    if base_branch:
        entry["base_branch"] = base_branch
    for k, v in (("branch_tip", branch_tip), ("base_tip", base_tip),
                 ("behind", behind), ("ahead", ahead)):
        if v is not None:
            entry[k] = v
    if _sqlite_path() is not None:
        cache = load_pr_cache()
        prev = cache.get(key) or {}
        merged = dict(entry)
        for k in ("ci", "ci_checked_at", "ci_sha", "ci_url",
                  "reviews", "unresolved", "resolved",
                  "reviews_checked_at", "reviews_sha"):
            if k in prev and k not in merged:
                merged[k] = prev[k]
        cache[key] = merged
        _write_pr_cache(cache)
        return
    path = config_dir() / "pr_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        def _update() -> None:
            cache = _read_json(path, {})
            prev = cache.get(key) or {}
            merged = dict(entry)
            for k in ("ci", "ci_checked_at", "ci_sha", "ci_url",
                      "reviews", "unresolved", "resolved",
                      "reviews_checked_at", "reviews_sha"):
                if k in prev and k not in merged:
                    merged[k] = prev[k]
            cache[key] = merged
            _atomic_replace(path, cache)
        _locked(lock, _update)


def get_cached_pr_status(branch: str, repo: str = "") -> dict | None:
    cached = lookup_pr_cache(branch, repo)
    return (cached or {}).get("pr")


def get_cached_pr_tool(branch: str, repo: str = "") -> str | None:
    cached = lookup_pr_cache(branch, repo)
    return (cached or {}).get("tool")

def cache_ci_status(branch: str, ci: str | None, sha: str | None = None,
                    ci_url: str | None = None, repo: str = "") -> None:
    """Record CI pipeline status (+ latest job URL) on a branch's entry.

    Reads-modifies the branch entry under the cache lock so the PR fields
    written by ``cache_pr_status`` survive. A ``None`` ci (lookup failed)
    writes nothing, so the next run retries the lookup.
    """
    if ci is None:
        return
    key = pr_cache_key(repo, branch)
    if _sqlite_path() is not None:
        cache = load_pr_cache()
        entry = cache.get(key) or dict(cache.get(branch) or {})
        cache[key] = entry
        entry["ci"] = ci
        entry["ci_checked_at"] = datetime.now(timezone.utc).isoformat()
        if sha:
            entry["ci_sha"] = sha
        if ci_url is not None:
            entry["ci_url"] = ci_url
        _write_pr_cache(cache)
        return
    path = config_dir() / "pr_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        def _update() -> None:
            cache = _read_json(path, {})
            prev = cache.get(branch) or {}
            entry = cache.get(key)
            if entry is None:
                entry = dict(prev)
                cache[key] = entry
            entry["ci"] = ci
            entry["ci_checked_at"] = datetime.now(timezone.utc).isoformat()
            if sha:
                entry["ci_sha"] = sha
            if ci_url is not None:
                entry["ci_url"] = ci_url
            _atomic_replace(path, cache)
        _locked(lock, _update)

def cache_review_stats(branch: str, stats: dict | None, sha: str | None = None,
                       repo: str = "") -> None:
    """Record {reviews, unresolved, resolved} on a branch's pr_cache entry.

    Mirrors cache_ci_status: a None stats (lookup failed) writes nothing so
    the next run retries.
    """
    if stats is None:
        return
    key = pr_cache_key(repo, branch)
    if _sqlite_path() is not None:
        cache = load_pr_cache()
        entry = cache.get(key) or dict(cache.get(branch) or {})
        cache[key] = entry
        entry["reviews"] = int(stats.get("reviews") or 0)
        entry["unresolved"] = int(stats.get("unresolved") or 0)
        entry["resolved"] = int(stats.get("resolved") or 0)
        entry["reviews_checked_at"] = datetime.now(timezone.utc).isoformat()
        if sha:
            entry["reviews_sha"] = sha
        _write_pr_cache(cache)
        return
    path = config_dir() / "pr_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        def _update() -> None:
            cache = _read_json(path, {})
            prev = cache.get(branch) or {}
            entry = cache.get(key)
            if entry is None:
                entry = dict(prev)
                cache[key] = entry
            entry["reviews"] = int(stats.get("reviews") or 0)
            entry["unresolved"] = int(stats.get("unresolved") or 0)
            entry["resolved"] = int(stats.get("resolved") or 0)
            entry["reviews_checked_at"] = datetime.now(timezone.utc).isoformat()
            if sha:
                entry["reviews_sha"] = sha
            _atomic_replace(path, cache)
        _locked(lock, _update)




def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _harness_path() -> Path:
    return config_dir() / "harnesses.json"


def save_harnesses_raw(data: dict) -> None:
    _write_json(_harness_path(), data)


def _sweep(data: dict) -> tuple[dict, bool]:
    alive = {k: v for k, v in data.items() if _live_rec(v) is not None}
    return alive, len(alive) != len(data)


def _live_rec(rec: object) -> dict | None:
    """Return the record if it describes a live harness, else None."""
    if not isinstance(rec, dict):
        return None
    pid = rec.get("pid", -1)
    if not isinstance(pid, int) or not _pid_alive(pid):
        return None
    return rec


def load_harnesses() -> dict:
    data = _read_json(_harness_path(), {})
    alive, changed = _sweep(data)
    if changed:
        save_harnesses_raw(alive)
    return alive


def record_harness_run(key: str, harness: str, worktree: str = "",
                       origin: str = "headless", pid: int | None = None) -> dict | None:
    """Claim the per-key harness slot; returns the live record that blocks us.

    A stale record whose pid is dead is reclaimed silently (crash-orphan
    fix); a live record on the same key — or the same worktree path under
    a different key — blocks the claim and is returned so callers can
    report it without a second lookup. `origin` is 'headless' (the CLI/
    web spawn this process owns) or 'terminal' (a detached terminal run;
    stoppable via stop_terminal_run).
    """
    path = _harness_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        return _locked(lock, lambda: _record_harness_locked(
            path, key, harness, worktree, origin, pid))


def _record_harness_locked(path: Path, key: str, harness: str, worktree: str,
                           origin: str = "headless", pid: int | None = None) -> dict | None:
    data, _ = _sweep(_read_json(path, {}))
    live = _live_rec(data.get(key))
    if live is None and worktree:
        for k, v in data.items():
            if k != key and isinstance(v, dict) and v.get("worktree") == worktree:
                live = _live_rec(v)
                if live is not None:
                    break
    if live is not None:
        return live
    data[key] = {"harness": harness, "pid": pid if isinstance(pid, int) else os.getpid(),
                 "started_at": time.time(), "worktree": worktree,
                 "origin": origin or "headless"}
    _atomic_replace(path, data)
    return None


def stop_terminal_run(key: str) -> bool:
    """SIGTERM→SIGKILL the terminal PID behind *key*; False when no live terminal lock.

    PID-reuse guard note: on Linux, /proc/<pid> start-time comparison vs
    started_at is added at the serve boot reap (Task 5); this helper
    relies on the lock file's short lifetime.
    """
    import signal as _signal
    rec = load_harnesses().get(key)
    if not isinstance(rec, dict) or rec.get("origin") != "terminal":
        return False
    pid = rec.get("pid", -1)
    if not isinstance(pid, int) or pid <= 1 or not _pid_alive(pid):
        clear_harness_run(key)
        return False
    try:
        os.kill(pid, _signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        clear_harness_run(key)
        return False
    for _ in range(50):
        if not _pid_alive(pid):
            break
        time.sleep(0.2)
    else:
        try:
            os.kill(pid, _signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    clear_harness_run(key)
    return True


def clear_harness_run(key: str) -> None:
    path = _harness_path()
    if not path.exists():
        return
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        _locked(lock, lambda: _clear_harness_locked(path, key))


def _clear_harness_locked(path: Path, key: str) -> None:
    data = _read_json(path, {})
    if key in data:
        del data[key]
        _atomic_replace(path, data)


def active_harness(key: str) -> dict | None:
    return load_harnesses().get(key)
