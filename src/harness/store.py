"""Persistent state: repo registry, tracker links, session links.

Layout (~/.config/harness/, override with HARNESS_CONFIG_DIR):
  config.json   — {"default_harness": "omp", "repos": {...}, "trackers": {...}}
  links.json    — {"<issue-key>": {"issue": ..., "pr_url": ..., "worktree": ...,
                                   "branch": ..., "repo": ...}}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .errors import HarnessError

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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


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
    links = load_links()
    merged = {**links.get(issue_key, {}), **entry}
    links[issue_key] = merged
    save_links(links)


def lookup_link(issue_key: str) -> dict | None:
    return load_links().get(issue_key)


def load_pr_cache() -> dict:
    return _read_json(config_dir() / "pr_cache.json", {})


def save_pr_cache(cache: dict) -> None:
    _write_json(config_dir() / "pr_cache.json", cache)


def cache_pr_status(branch: str, pr: dict | None, tool: str | None = None) -> None:
    cache = load_pr_cache()
    cache[branch] = {"pr": pr, "tool": tool,
                     "checked_at": datetime.now(timezone.utc).isoformat()}
    save_pr_cache(cache)


def get_cached_pr_status(branch: str) -> dict | None:
    return load_pr_cache().get(branch, {}).get("pr")


def get_cached_pr_tool(branch: str) -> str | None:
    return load_pr_cache().get(branch, {}).get("tool")


def get_cached_pr_status(branch: str) -> dict | None:
    return load_pr_cache().get(branch, {}).get("pr")
