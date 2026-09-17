"""Persistent state: repo registry, tracker links, session links.

Layout (~/.config/harness/, override with HARNESS_CONFIG_DIR):
  config.json   — {"default_harness": "omp", "repos": {...}, "trackers": {...}}
  links.json    — {"<issue-key>": {"issue": ..., "pr_url": ..., "worktree": ...,
                                   "branch": ..., "repo": ...}}
"""

from __future__ import annotations

import json
import os
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
