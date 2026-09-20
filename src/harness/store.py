"""Persistent state: repo registry, tracker links, session links.

Layout (~/.config/harness/, override with HARNESS_CONFIG_DIR):
  config.json   — {"default_harness": "omp", "repos": {...}, "trackers": {...}}
  links.json    — {"<issue-key>": {"issue": ..., "pr_url": ..., "worktree": ...,
                                   "branch": ..., "repo": ...}}
"""

import json
import os
import tempfile
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
    return _read_json(config_dir() / "links.json", {})


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


def get_cached_pr_status(branch: str) -> dict | None:
    return load_pr_cache().get(branch, {}).get("pr")
