"""Install-sync self-check for git-wt.

pipx/uv snapshot the source into an isolated venv — workspace edits never
reach the installed binary until reinstall. install.sh therefore writes an
install receipt (~/.local/share/git-wt/install-receipt.json) recording a
hash over the source tree; `git-wt doctor` compares that receipt against
the live tree so "edited source, forgot to reinstall" is caught before it
confuses anyone. The hashing here MUST stay identical to the snippet in
install.sh.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TOOL = "git-wt"

OK, STALE, MISSING = "ok", "stale", "missing"


def share_dir() -> Path:
    """State dir (~/.local/share/git-wt); honors $HOME so tests can redirect."""
    return Path.home() / ".local" / "share" / TOOL


def receipt_path() -> Path:
    return share_dir() / "install-receipt.json"


def source_hash(pkg_dir: Path) -> str:
    """SHA-256 over source *.py (relative posix paths + bytes), 12 hex chars."""
    digest = hashlib.sha256()
    for f in sorted(pkg_dir.rglob("*.py")):
        if ".venv" in f.parts:
            continue
        digest.update(f.relative_to(pkg_dir).as_posix().encode())
        digest.update(f.read_bytes())
    return digest.hexdigest()[:12]


def load_receipt() -> dict | None:
    """Parsed receipt, or None when absent/unreadable/invalid JSON."""
    try:
        return json.loads(receipt_path().read_text())
    except (OSError, ValueError):
        return None


def _source_tree(source_dir: Path) -> Path | None:
    """Package dir inside a recorded source checkout (src/ or flat layout)."""
    name = Path(__file__).resolve().parent.name
    for candidate in (source_dir / "src" / name, source_dir / name):
        if candidate.is_dir():
            return candidate
    return None


def check() -> tuple[str, str]:
    """Compare the install receipt against the live source tree.

    Returns (status, message): status is "ok", "stale", or "missing";
    the message names the fix command for non-ok statuses.
    """
    receipt = load_receipt()
    if receipt is None:
        return MISSING, "no install receipt — fix with: ./install.sh"
    tree = _source_tree(Path(receipt.get("source_dir", "")))
    if tree is None:
        return MISSING, (
            f"recorded source dir {receipt.get('source_dir', '?')!r} is gone — "
            "fix with: ./install.sh from a valid checkout"
        )
    live = source_hash(tree)
    if live != receipt.get("source_hash"):
        return STALE, (
            f"source changed since install ({receipt.get('source_hash')} → {live}) — "
            "fix with: ./install.sh"
        )
    return OK, f"install in sync ({live})"


def dev_warning(here: Path | None = None) -> str | None:
    """Warning when running from a working tree that differs from the receipt.

    Fires only when __file__ resolves outside the installed locations
    (site-packages / ~/.local/share) — the installed copy never warns about
    itself — and the running tree's hash differs from the install receipt.
    None otherwise.
    """
    pkg_dir = (here or Path(__file__).resolve()).parent
    if "site-packages" in pkg_dir.parts or ".local/share" in pkg_dir.parts:
        return None
    receipt = load_receipt()
    if receipt is None:
        return None
    if source_hash(pkg_dir) != receipt.get("source_hash"):
        return (
            "warning: running git-wt from the source tree, but the installed "
            "copy is stale — fix with: ./install.sh"
        )
    return None
