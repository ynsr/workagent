"""Read-only git diff inspection and prodtest YAML plan reader for the web UI.

Guards:
- Path traversal: every requested file path is normalized and rejected if it is
  empty, absolute, contains ``..`` segments or NUL bytes, or resolves outside
  the worktree root.
- Binary & size caps: binary diffs return ``binary=True`` with empty hunks;
  oversized diffs are capped at ``max_bytes`` with ``truncated=True``.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from . import repos, store

logger = logging.getLogger(__name__)

DEFAULT_MAX_DIFF_BYTES = 256 * 1024
DEFAULT_MAX_PLAN_BYTES = 512 * 1024
VALID_MODES = ("base", "head")

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<context>.*)$"
)


class DiffPathError(ValueError):
    """Raised when a requested diff path is invalid or escapes the worktree."""


class DiffModeError(ValueError):
    """Raised when a requested diff mode is not 'base' or 'head'."""


def validate_mode(mode: str) -> str:
    m = (mode or "base").strip().lower()
    if m not in VALID_MODES:
        raise DiffModeError(f"invalid mode {mode!r}; expected 'base' or 'head'")
    return m


def validate_rel_path(worktree: Path, rel_path: str) -> str:
    """Validate *rel_path* against traversal and return a POSIX-normalized relative path."""
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise DiffPathError("path is required")
    if "\x00" in rel_path:
        raise DiffPathError("invalid path: NUL byte not allowed")
    cleaned = rel_path.replace("\\", "/").strip()
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:/", cleaned):
        raise DiffPathError(f"absolute path not allowed: {rel_path!r}")
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise DiffPathError(f"path traversal not allowed: {rel_path!r}")
    norm = "/".join(parts)
    root = worktree.resolve()
    candidate = (root / norm).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DiffPathError(f"path escapes worktree: {rel_path!r}") from exc
    return norm


def _run_git(worktree: Path, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _resolve_base_ref(worktree: Path, base_branch: str | None = None) -> str:
    """Resolve the best merge-base ref for ``mode='base'`` (falls back to ``HEAD``)."""
    branch = (base_branch or "").strip()
    if not branch:
        try:
            branch = repos.default_branch(worktree)
        except Exception:
            branch = "main"
    for cand in (f"origin/{branch}", branch):
        if not cand:
            continue
        mb = _run_git(worktree, ["merge-base", cand, "HEAD"])
        if mb.returncode == 0 and mb.stdout.strip():
            return mb.stdout.decode("utf-8", errors="replace").strip()
    return "HEAD"


def _list_untracked(worktree: Path) -> list[str]:
    proc = _run_git(worktree, ["ls-files", "--others", "--exclude-standard", "-z"])
    if proc.returncode != 0 or not proc.stdout:
        return []
    return [p for p in proc.stdout.decode("utf-8", errors="replace").split("\x00") if p]


def _is_binary_bytes(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _count_file_lines(path: Path, max_bytes: int = DEFAULT_MAX_DIFF_BYTES) -> tuple[int, bool]:
    """Return ``(line_count, is_binary)`` for an untracked file."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(max_bytes + 1)
    except OSError:
        return 0, False
    if _is_binary_bytes(chunk):
        return 0, True
    if not chunk:
        return 0, False
    return chunk.count(b"\n") + (0 if chunk.endswith(b"\n") else 1), False


def list_changed_files(
    worktree: Path,
    mode: str = "base",
    base_branch: str | None = None,
) -> dict[str, Any]:
    """List changed files in *worktree* with additions, deletions, and status."""
    m = validate_mode(mode)
    base_ref = _resolve_base_ref(worktree, base_branch) if m == "base" else "HEAD"
    diff_range = [base_ref] if m == "base" else ["HEAD"]

    status_map: dict[str, tuple[str, str | None]] = {}
    ns = _run_git(worktree, ["diff", "--name-status", "-z", *diff_range])
    if ns.returncode == 0 and ns.stdout:
        tokens = ns.stdout.decode("utf-8", errors="replace").split("\x00")
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if not tok:
                i += 1
                continue
            code = tok[0]
            if code in ("R", "C") and i + 2 < len(tokens):
                old_p, new_p = tokens[i + 1], tokens[i + 2]
                status_map[new_p] = (code, old_p)
                i += 3
            elif i + 1 < len(tokens):
                p = tokens[i + 1]
                status_map[p] = (code, None)
                i += 2
            else:
                i += 1

    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    numstat = _run_git(worktree, ["diff", "--numstat", "-z", *diff_range])
    if numstat.returncode == 0 and numstat.stdout:
        raw = numstat.stdout.decode("utf-8", errors="replace")
        entries = raw.split("\x00")
        i = 0
        while i < len(entries):
            entry = entries[i]
            if not entry:
                i += 1
                continue
            parts = entry.split("\t", 2)
            if len(parts) < 3:
                i += 1
                continue
            add_s, del_s, path_part = parts
            old_path: str | None = None
            if path_part == "" and i + 2 < len(entries):
                # Rename in -z numstat: "adds\tdels\t\0old\0new\0"
                old_path = entries[i + 1]
                rel = entries[i + 2]
                i += 3
            else:
                rel = path_part
                i += 1
            binary = add_s == "-" or del_s == "-"
            adds = 0 if binary else int(add_s or 0)
            dels = 0 if binary else int(del_s or 0)
            st_code, st_old = status_map.get(rel, ("M", old_path))
            files.append({
                "path": rel,
                "status": st_code,
                "additions": adds,
                "deletions": dels,
                "binary": binary,
                "old_path": st_old or old_path,
            })
            seen.add(rel)

    for rel in _list_untracked(worktree):
        if rel in seen:
            continue
        adds, binary = _count_file_lines(worktree / rel)
        files.append({
            "path": rel,
            "status": "?",
            "additions": adds,
            "deletions": 0,
            "binary": binary,
            "old_path": None,
        })
        seen.add(rel)

    files.sort(key=lambda f: f["path"])
    return {
        "mode": m,
        "base_ref": base_ref,
        "files": files,
        "total_additions": sum(f["additions"] for f in files),
        "total_deletions": sum(f["deletions"] for f in files),
    }


def parse_unified_diff(diff_text: str) -> list[dict[str, Any]]:
    """Parse unified diff text into structured hunks with old/new line numbers."""
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    old_line = 0
    new_line = 0

    for line in diff_text.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            old_line = int(m.group("old_start"))
            new_line = int(m.group("new_start"))
            current = {
                "header": line,
                "old_start": old_line,
                "old_count": int(m.group("old_count") or "1"),
                "new_start": new_line,
                "new_count": int(m.group("new_count") or "1"),
                "lines": [],
            }
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+"):
            current["lines"].append({
                "type": "add",
                "old_line": None,
                "new_line": new_line,
                "content": line[1:],
            })
            new_line += 1
        elif line.startswith("-"):
            current["lines"].append({
                "type": "del",
                "old_line": old_line,
                "new_line": None,
                "content": line[1:],
            })
            old_line += 1
        elif line.startswith(" "):
            current["lines"].append({
                "type": "context",
                "old_line": old_line,
                "new_line": new_line,
                "content": line[1:],
            })
            old_line += 1
            new_line += 1
        elif line.startswith("\\ No newline at end of file"):
            current["lines"].append({
                "type": "meta",
                "old_line": None,
                "new_line": None,
                "content": line,
            })
    return hunks


def get_file_diff(
    worktree: Path,
    rel_path: str,
    mode: str = "base",
    base_branch: str | None = None,
    max_bytes: int = DEFAULT_MAX_DIFF_BYTES,
) -> dict[str, Any]:
    """Return structured unified diff hunks for *rel_path* in *worktree*."""
    m = validate_mode(mode)
    norm = validate_rel_path(worktree, rel_path)
    base_ref = _resolve_base_ref(worktree, base_branch) if m == "base" else "HEAD"
    diff_range = [base_ref] if m == "base" else ["HEAD"]

    proc = _run_git(worktree, ["diff", "--no-color", "-U3", *diff_range, "--", norm])
    raw_bytes = proc.stdout if proc.returncode == 0 else b""

    if not raw_bytes:
        # Check if untracked file on disk
        full_path = worktree / norm
        if full_path.is_file() and norm in set(_list_untracked(worktree)):
            try:
                file_size = full_path.stat().st_size
                with full_path.open("rb") as fh:
                    content_bytes = fh.read(max_bytes + 1)
            except OSError as exc:
                logger.warning("Failed reading untracked file %s: %s", norm, exc)
                return {
                    "path": norm,
                    "mode": m,
                    "base_ref": base_ref,
                    "binary": False,
                    "truncated": False,
                    "additions": 0,
                    "deletions": 0,
                    "hunks": [],
                }
            if _is_binary_bytes(content_bytes):
                return {
                    "path": norm,
                    "mode": m,
                    "base_ref": base_ref,
                    "binary": True,
                    "truncated": False,
                    "additions": 0,
                    "deletions": 0,
                    "hunks": [],
                }
            truncated = len(content_bytes) > max_bytes or file_size > max_bytes
            text = content_bytes[:max_bytes].decode("utf-8", errors="replace")
            lines = text.splitlines()
            hunk_lines = [
                {"type": "add", "old_line": None, "new_line": idx + 1, "content": ln}
                for idx, ln in enumerate(lines)
            ]
            hunks = (
                [{
                    "header": f"@@ -0,0 +1,{len(lines)} @@",
                    "old_start": 0,
                    "old_count": 0,
                    "new_start": 1,
                    "new_count": len(lines),
                    "lines": hunk_lines,
                }]
                if lines
                else []
            )
            return {
                "path": norm,
                "mode": m,
                "base_ref": base_ref,
                "binary": False,
                "truncated": truncated,
                "additions": len(lines),
                "deletions": 0,
                "hunks": hunks,
            }
        return {
            "path": norm,
            "mode": m,
            "base_ref": base_ref,
            "binary": False,
            "truncated": False,
            "additions": 0,
            "deletions": 0,
            "hunks": [],
        }

    # Detect git binary diff marker
    head_preview = raw_bytes[:2048].decode("utf-8", errors="replace")
    if "Binary files " in head_preview and " differ" in head_preview or "GIT binary patch" in head_preview:
        return {
            "path": norm,
            "mode": m,
            "base_ref": base_ref,
            "binary": True,
            "truncated": False,
            "additions": 0,
            "deletions": 0,
            "hunks": [],
        }

    truncated = len(raw_bytes) > max_bytes
    if truncated:
        cut = raw_bytes[:max_bytes]
        last_nl = cut.rfind(b"\n")
        if last_nl > 0:
            cut = cut[:last_nl]
        diff_text = cut.decode("utf-8", errors="replace")
    else:
        diff_text = raw_bytes.decode("utf-8", errors="replace")

    hunks = parse_unified_diff(diff_text)
    additions = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "add")
    deletions = sum(1 for h in hunks for ln in h["lines"] if ln["type"] == "del")
    return {
        "path": norm,
        "mode": m,
        "base_ref": base_ref,
        "binary": False,
        "truncated": truncated,
        "additions": additions,
        "deletions": deletions,
        "hunks": hunks,
    }


def read_prodtest_plan(
    issue_key: str,
    max_bytes: int = DEFAULT_MAX_PLAN_BYTES,
) -> dict[str, Any]:
    """Read and parse the prodtest YAML plan for *issue_key*.

    Raises ``FileNotFoundError`` when no prodtest item or plan file exists, or
    ``DiffPathError`` if the stored path is not a readable YAML file.
    """
    from .cli_prodtest import _prodtest_key_for_status

    try:
        key = _prodtest_key_for_status(issue_key)
    except Exception:
        key = issue_key
    item = store.get_prodtest(key)
    if item is None:
        raise FileNotFoundError(f"unknown prodtest item: {issue_key!r}")

    plan_path_str = str(item.get("plan_path") or "").strip()
    if not plan_path_str:
        raise FileNotFoundError(f"no plan_path recorded for prodtest {key!r}")

    plan_path = Path(plan_path_str).expanduser()
    if plan_path.suffix.lower() not in (".yaml", ".yml"):
        raise DiffPathError(f"plan_path is not a YAML file: {plan_path_str!r}")
    if not plan_path.is_file():
        raise FileNotFoundError(f"plan file not found on disk: {plan_path_str}")

    with plan_path.open("rb") as fh:
        raw_bytes = fh.read(max_bytes + 1)
    truncated = len(raw_bytes) > max_bytes
    yaml_text = raw_bytes[:max_bytes].decode("utf-8", errors="replace")

    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    if not truncated:
        try:
            import yaml  # type: ignore[import-untyped]
            loaded = yaml.safe_load(yaml_text)
            if isinstance(loaded, dict):
                parsed = loaded
            elif loaded is not None:
                parse_error = "plan YAML root must be a mapping"
        except Exception as exc:
            parse_error = str(exc)

    cases_raw = (parsed or {}).get("cases") if isinstance((parsed or {}).get("cases"), list) else []
    pre_raw = (parsed or {}).get("preconditions") if isinstance((parsed or {}).get("preconditions"), list) else []

    return {
        "issue_key": key,
        "status": str(item.get("status") or ""),
        "plan_path": str(plan_path),
        "plan_revision": item.get("plan_revision"),
        "plan_risk": str(item.get("plan_risk") or (parsed or {}).get("risk") or ""),
        "plan_diff_hash": str(item.get("plan_diff_hash") or (parsed or {}).get("diff_hash") or ""),
        "release_sha": str(item.get("release_sha") or ""),
        "last_run_id": str(item.get("last_run_id") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "truncated": truncated,
        "yaml": yaml_text,
        "parse_error": parse_error,
        "summary": str((parsed or {}).get("summary") or (parsed or {}).get("title") or ""),
        "preconditions": pre_raw,
        "cases": cases_raw,
        "metadata": {
            k: v
            for k, v in (parsed or {}).items()
            if k not in ("cases", "preconditions") and isinstance(v, (str, int, float, bool, list))
        },
    }
