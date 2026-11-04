"""Prodtest gate helpers for cleanup (moved verbatim from cli_cleanup)."""
from __future__ import annotations

from pathlib import Path

from . import repos, store
from .cli_core import eprint

def run_validate(plan: str, manifest: str | None) -> dict:
    """Validate a prodtest plan (delegates to prodtest_scripts; monkeypatch seam)."""
    from . import prodtest_scripts as _ps
    return _ps.run_validate(plan, manifest)


def manifest_path_for(key: str, explicit: str | None = None, repo_dir: str = "") -> str | None:
    """Manifest path for prodtest validation: explicit, else repo .workagent file."""
    if explicit:
        return explicit
    if repo_dir:
        return str(Path(repo_dir) / ".workagent" / "prodtest.yaml")
    return None


def prodtest_gate(key: str, item: dict | None, current_diff: str = "",
                  config: dict | None = None, manifest_path: str | None = None) -> str | None:
    """Prodtest plan gate: None = pass, 'warn:…' / 'blocked:…' otherwise."""
    from . import cli_cleanup as _cc  # shim: tests patch cli_cleanup.run_validate
    cfg = config or {}
    if item and "plan_diff_hash" in item and (item.get("plan_diff_hash") or "") != (current_diff or ""):
        return "blocked:stale"
    if not item or not item.get("plan_path"):
        return "warn:missing-plan" if cfg.get("mode", "warn") else None
    if (item.get("plan_diff_hash") or "") != (current_diff or ""):
        return "blocked:stale"
    try:
        v = _cc.run_validate(item["plan_path"], manifest_path if manifest_path is not None
                           else manifest_path_for(key))
    except Exception:
        return "blocked:invalid-plan"
    if not (v or {}).get("valid"):
        return "blocked:invalid-plan"
    return None


def current_prodtest_diff(repo_dir: str, default_branch: str) -> str:
    """Best-effort current plan diff hash ('' on any failure; never raises)."""
    import subprocess
    if not repo_dir or not Path(repo_dir).is_dir():
        return ""
    script = Path(__file__).parent / "prodtest_files" / "diff_hash.sh"
    try:
        proc = subprocess.run(["bash", str(script), default_branch or "main"],
                              cwd=repo_dir, capture_output=True,
                              text=True, timeout=60)
    except Exception:
        return ""
    return (proc.stdout or "").strip() if proc.returncode == 0 else ""


def row_prodtest_gate(key: str, entry: dict) -> str | None:
    """Per-row prodtest gate for cleanup: blocked/invalid yields a status, warn passes."""
    from . import cli_cleanup as _cc  # shim: tests patch cli_cleanup.current_prodtest_diff etc.
    try:
        item = store.get_prodtest(_cc._prodtest_issue_key(key, entry))
    except Exception:
        item = None
    mode = "block"
    try:
        mode = str((store.load_config().get("prodtest") or {}).get("mode", "block"))
    except Exception:
        mode = "block"
    # Hash the linked worktree, not the main checkout: _finish_plan stores
    # current_prodtest_diff(worktree, …), so hashing repo here mismatches
    # whenever the worktree diff differs from the checkout (IPG-1069).
    wt = str((entry or {}).get("worktree", "") or "")
    base = wt if wt and Path(wt).is_dir() else str((entry or {}).get("repo", "") or "")
    default = ""
    try:
        default = repos.default_branch(Path(base)) if base and Path(base).is_dir() else ""
    except Exception:
        default = ""
    try:
        current = _cc.current_prodtest_diff(base, default or "main")
    except Exception:
        current = ""
    manifest = _cc.manifest_path_for(_cc._prodtest_issue_key(key, entry),
                                 repo_dir=str((entry or {}).get("repo", "") or ""))
    gate = _cc.prodtest_gate(_cc._prodtest_issue_key(key, entry), item,
                         current_diff=current, config={"mode": mode}, manifest_path=manifest)
    if gate and gate.startswith("warn:"):
        eprint(f"warning: prodtest {gate[5:]} for {_cc._prodtest_issue_key(key, entry)}")
        return None
    return gate


def _prodtest_issue_key(key: str, entry: dict | None = None) -> str:
    """Issue key for prodtest-block checks (`issue` field, else ref key)."""
    e = entry or {}
    stored = str(e.get("issue", "") or key or "")
    if stored.startswith("jira:"):
        stored = stored.split(":", 1)[1].split("@", 1)[0]
    return stored


def _prodtest_blocked(key: str, entry: dict | None = None) -> bool:
    """True when a pending/running prodtest item blocks GC/destroy."""
    try:
        return bool(store.gc_blocked_by_prodtest(_prodtest_issue_key(key, entry)))
    except Exception:
        return False
