"""status display cells (split from cli_status; re-exported via facade)."""
from __future__ import annotations

from . import refs
from .errors import HarnessError


def _patched(name: str):
    """Test seam: resolve via the `cli` shim so monkeypatch.setattr(cli, …) applies."""
    from . import cli as _cli
    return getattr(_cli, name)

def _fmt_pr(pr: dict | None) -> str:
    if not pr:
        return "-"
    kind = "MR" if "merge_requests" in (pr.get("url") or "") else "PR"
    state = pr.get("state") or ""
    if state:
        return f"{kind} #{pr['number']} ({state})"
    return f"{kind} #{pr['number']}"


def _recorded_pr(url: str) -> dict | None:
    """Parse a recorded PR/MR URL into a minimal pr dict (state unknown)."""
    for rx, group in ((refs._GITLAB_MR, 3), (refs._GITHUB_PR, 2)):
        m = rx.match(url or "")
        if m:
            return {"number": int(m.group(group)), "state": "", "url": url}
    return None


def _seed_recorded_pr(cells: dict, entry: dict) -> dict:
    """A recorded pr_url wins when the cache/query produced no PR."""
    if not cells.get("pr_data") and entry.get("pr_url"):
        cells["pr_data"] = _recorded_pr(entry["pr_url"])
        cells["pr"] = _fmt_pr(cells["pr_data"])
    return cells


def _fmt_counts(ab: dict | None) -> str:
    if not ab:
        return "-"
    return f"{ab['behind']}|{ab['ahead']}"


def _git_tip(wt: str, ref: str) -> str | None:
    try:
        return _patched("run_cmd")("git", "-C", wt, "rev-parse", "--verify", "-q", ref) or None
    except HarnessError:
        return None


