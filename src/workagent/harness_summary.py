"""Harness session summary: last assistant text from a .jsonl transcript.

Tolerant by design: unknown line shapes are skipped, never raised.
"""
from __future__ import annotations

import json
from pathlib import Path

_MAX_CHARS = 4000
_ERR_RE = None

def _texts(content) -> list[str]:
    """Plain-text parts of a message content list."""
    if not isinstance(content, list):
        return []
    out = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            text = str(part.get("text", "") or "").strip()
            if text:
                out.append(text)
    return out



def tool_error_rate(transcript) -> tuple[int, int] | None:
    """(errors, total) over toolResult lines in a .jsonl transcript.

    None when no path/unreadable. Tolerant by design: unparseable lines
    are skipped; zero toolResults pass (None, never a 0/0 fail).

    A toolResult counts as an error when its payload carries an explicit
    error flag or its text starts with an error-like word."""
    import json as _json
    import re as _re
    global _ERR_RE
    if _ERR_RE is None:
        _ERR_RE = _re.compile(r"^\s*(error|failed?|exception|traceback)\b", _re.IGNORECASE)
    if not transcript:
        return None
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    def _is_error(data: dict) -> bool:
        msg = data.get("message")
        if not isinstance(msg, dict):
            return False
        content = msg.get("content")
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            if isinstance(part, dict):
                for key in ("isError", "is_error", "error"):
                    if part.get(key) is True:
                        return True
                for key in ("status", "level"):
                    if str(part.get(key) or "").strip().lower() in ("error", "failed"):
                        return True
                for key in ("text", "content", "output", "message"):
                    val = part.get(key)
                    if isinstance(val, str) and _ERR_RE.search(val):
                        return True
            elif isinstance(part, str) and _ERR_RE.search(part):
                return True
        return data.get("isError", data.get("is_error")) is True

    total = errors = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = _json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        msg = data.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        kinds = set()
        if isinstance(content, list):
            kinds = {p.get("type") for p in content if isinstance(p, dict)}
        if msg.get("role") != "toolResult" and "toolResult" not in kinds:
            continue
        total += 1
        if _is_error(data):
            errors += 1
    return (errors, total)



def last_assistant_text(transcript: str | Path) -> str:
    """Last plain-text assistant message in *transcript* ("" when none).

    Skips toolCall/toolResult/user/system lines and malformed JSON.
    Truncates to 4000 chars.
    """
    try:
        path = Path(transcript)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    last = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        msg = data.get("message")
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        texts = _texts(msg.get("content"))
        if texts:
            last = "\n".join(texts).strip()
    if len(last) > _MAX_CHARS:
        last = last[:_MAX_CHARS].rstrip() + "\n…(truncated)"
    return last
