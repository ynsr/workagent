"""Persistent state: repo registry, tracker links, session links.

Layout (~/.config/harness/, override with HARNESS_CONFIG_DIR):
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
    override = os.environ.get("HARNESS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "harness"


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


def load_links() -> dict:
    links = _read_json(config_dir() / "links.json", {})
    for key, entry in links.items():
        if isinstance(entry, dict):
            entry.setdefault("ref_key", key)
    return links


def save_links(links: dict) -> None:
    _write_json(config_dir() / "links.json", links)


def record_link(issue_key: str, entry: dict) -> None:
    path = config_dir() / "links.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        _locked(lock, lambda: _record_link_locked(path, issue_key, entry))


def _record_link_locked(path: Path, issue_key: str, entry: dict) -> None:
    links = _read_json(path, {})
    merged = {**links.get(issue_key, {}), **entry}
    links[issue_key] = merged
    _atomic_replace(path, links)


def lookup_link(issue_key: str) -> dict | None:
    return load_links().get(issue_key)


def load_pr_cache() -> dict:
    return _read_json(config_dir() / "pr_cache.json", {})


def save_pr_cache(cache: dict) -> None:
    _write_json(config_dir() / "pr_cache.json", cache)


def cache_pr_status(branch: str, pr: dict | None, tool: str | None = None,
                    base_branch: str | None = None, branch_tip: str | None = None,
                    base_tip: str | None = None, behind: int | None = None,
                    ahead: int | None = None) -> None:
    entry = {"pr": pr, "tool": tool,
             "checked_at": datetime.now(timezone.utc).isoformat()}
    if base_branch:
        entry["base_branch"] = base_branch
    for k, v in (("branch_tip", branch_tip), ("base_tip", base_tip),
                 ("behind", behind), ("ahead", ahead)):
        if v is not None:
            entry[k] = v
    path = config_dir() / "pr_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        def _update() -> None:
            cache = _read_json(path, {})
            cache[branch] = entry
            _atomic_replace(path, cache)
        _locked(lock, _update)


def get_cached_pr_status(branch: str) -> dict | None:
    return load_pr_cache().get(branch, {}).get("pr")


def get_cached_pr_tool(branch: str) -> str | None:
    return load_pr_cache().get(branch, {}).get("tool")


def cache_ci_status(branch: str, ci: str | None, sha: str | None = None) -> None:
    """Record CI pipeline status on a branch's pr_cache entry, in place.

    Reads-modifies the branch entry under the cache lock so the PR fields
    written by ``cache_pr_status`` survive. A ``None`` ci (lookup failed)
    writes nothing, so the next run retries the lookup.
    """
    if ci is None:
        return
    path = config_dir() / "pr_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        def _update() -> None:
            cache = _read_json(path, {})
            entry = cache.setdefault(branch, {})
            entry["ci"] = ci
            entry["ci_checked_at"] = datetime.now(timezone.utc).isoformat()
            if sha:
                entry["ci_sha"] = sha
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
    alive = {k: v for k, v in data.items()
             if isinstance(v, dict) and _pid_alive(v.get("pid", -1))}
    return alive, len(alive) != len(data)


def load_harnesses() -> dict:
    data = _read_json(_harness_path(), {})
    alive, changed = _sweep(data)
    if changed:
        save_harnesses_raw(alive)
    return alive


def record_harness_run(key: str, harness: str, worktree: str = "") -> None:
    path = _harness_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        _locked(lock, lambda: _record_harness_locked(path, key, harness, worktree))


def _record_harness_locked(path: Path, key: str, harness: str, worktree: str) -> None:
    data, _ = _sweep(_read_json(path, {}))
    data[key] = {"harness": harness, "pid": os.getpid(),
                 "started_at": time.time(), "worktree": worktree}
    _atomic_replace(path, data)


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
