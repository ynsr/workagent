"""Redaction + harness result parsing for prodtest (ports of skill scripts)."""
from __future__ import annotations
import json, re
from pathlib import Path

_RULES = [
    (re.compile(r"(?i)(authorization|cookie|set-cookie|x-api-key)\s*[:=]\s*[^\r\n]+"), r"\1: [REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/=]{10,}"), "Bearer [REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED-JWT]"),
    (re.compile(r'(?i)("?(?:password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token|cvv2?|pin)"?\s*[:=]\s*)("[^"]*"|[^\s,;}&]+)'), r"\1[REDACTED]"),
    (re.compile(r"\bIR\d{24}\b"), "IR[REDACTED-SHEBA]"),
]
_CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

def _luhn(n: str) -> bool:
    t, alt = 0, False
    for ch in reversed(n):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        t += d
        alt = not alt
    return t % 10 == 0

def redact(text: str) -> str:
    for rx, rep in _RULES:
        text = rx.sub(rep, text)
    return _CARD.sub(lambda m: ("*" * (len(m.group()) - 4) + m.group()[-4:]) if _luhn(m.group()) else m.group(), text)
