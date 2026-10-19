"""Harness session summary: last assistant text from a .jsonl transcript.

Tolerant by design: unknown line shapes are skipped, never raised.
"""
from __future__ import annotations

import json
from pathlib import Path

_MAX_CHARS = 4000


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
