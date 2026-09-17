"""git repo helpers: detect repo, default branch, resolve target repo."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from . import store
from .errors import HarnessError, run_cmd


def repo_root(cwd: Path | None = None) -> Path | None:
    try:
        out = run_cmd("git", "rev-parse", "--show-toplevel", cwd=cwd or Path.cwd())
    except HarnessError:
        return None
    return Path(out) if out else None


def repo_name(path: Path) -> str:
    return path.resolve().name


def remote_url(path: Path) -> str | None:
    try:
        return run_cmd("git", "-C", str(path), "remote", "get-url", "origin")
    except HarnessError:
        return None


def default_branch(path: Path) -> str:
    """Detect default branch: gh/glab → origin/HEAD → common names."""
    host_cli = _detect_host_cli(path)
    if host_cli == "gh":
        try:
            out = run_cmd("gh", "repo", "view", "--json", "defaultBranchRef",
                          "--jq", ".defaultBranchRef.name", cwd=path)
            if out:
                return out
        except HarnessError:
            pass
    elif host_cli == "glab":
        try:
            out = run_cmd("glab", "repo", "view", "--output", "json", cwd=path)
            if out:
                name = json.loads(out).get("default_branch")
                if name:
                    return name
        except (HarnessError, json.JSONDecodeError, AttributeError):
            pass
    try:
        out = run_cmd("git", "-C", str(path), "symbolic-ref", "refs/remotes/origin/HEAD")
        if out:
            return out.split("/")[-1]
    except HarnessError:
        pass
    for candidate in ("main", "master", "develop"):
        for ref in (f"origin/{candidate}", candidate):
            try:
                run_cmd("git", "-C", str(path), "rev-parse", "--verify", ref)
                return candidate
            except HarnessError:
                continue
    raise HarnessError(f"cannot determine default branch for {path}", exit_code=2)


def current_branch(path: Path) -> str | None:
    try:
        return run_cmd("git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD")
    except HarnessError:
        return None


def resolve_repo(explicit: str | None, cwd: Path, depth: int = 7) -> Path:
    """Resolve which offline repo to use.

    --repo accepts a registered name, a local path, or a clone URL.
    Without it: cwd repo if inside one, else interactive pick of registered
    repos (TTY) or an error (non-TTY).
    """
    cfg = store.load_config()
    if explicit:
        repos = cfg.get("repos", {})
        if explicit in repos:
            p = Path(repos[explicit]["path"]).expanduser()
            if not p.is_dir():
                raise HarnessError(f"registered repo '{explicit}' path missing: {p}")
            return p
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p
        # Treat as clone URL
        return clone_url(explicit, depth)
    root = repo_root(cwd)
    if root is not None:
        return root
    import sys
    names = list(cfg.get("repos", {}))
    if not names:
        raise HarnessError(
            "not in a git repo and no repos registered.\n"
            "  Register one: harness repo add --name <name> --path <path>\n"
            "  Or pass: harness start <issue> --repo <path|url|name>",
            exit_code=2,
        )
    if not sys.stdin.isatty():
        raise HarnessError(
            f"not in a git repo; pass --repo explicitly (registered: {', '.join(names)})",
            exit_code=2,
        )
    print("Select a repo:", file=sys.stderr)
    for i, name in enumerate(names, 1):
        print(f"  {i}. {name} ({cfg['repos'][name].get('path', '?')})", file=sys.stderr)
    try:
        choice = input("repo [number/name, or paste path/URL]: ").strip()
    except EOFError:
        raise HarnessError("no repo selected", exit_code=2)
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        return Path(cfg["repos"][names[int(choice) - 1]]["path"]).expanduser()
    return resolve_repo(choice or None, cwd, depth)


def clone_url(url: str, depth: int = 7) -> Path:
    """Shallow-clone a repo URL next to other checkouts; remember it."""
    dest_base = Path.home() / "projects" / "harness-clones"
    name = url.rstrip("/").split("/")[-1].removesuffix(".git")
    dest = dest_base / name
    if not dest.is_dir():
        dest_base.mkdir(parents=True, exist_ok=True)
        run_cmd("git", "clone", "--depth", str(int(depth)), url, str(dest))
    register_repo(name, dest)
    return dest


def register_repo(name: str, path: Path) -> None:
    cfg = store.load_config()
    repos = cfg.setdefault("repos", {})
    repos[name] = {"path": str(path), "remote": remote_url(path)}
    store.save_config(cfg)


def _detect_host_cli(path: Path) -> str | None:
    for cli in ("gh", "glab"):
        try:
            proc = subprocess.run([cli, "auth", "status"], cwd=str(path),
                                  capture_output=True, timeout=15)
            if proc.returncode == 0:
                return cli
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None
