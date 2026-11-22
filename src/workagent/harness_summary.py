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



def tool_error_rate(transcript, since_line: int = 0) -> tuple[int, int] | None:
    """(errors, total) over toolResult lines in a .jsonl transcript.

    None when no path/unreadable. Tolerant by design: unparseable lines
    are skipped; zero toolResults pass (None, never a 0/0 fail).

    ``since_line`` scopes the scan to lines appended after that 0-based
    file offset — retries resuming the same transcript must only score
    the current attempt's tool calls, never earlier attempts' failures.

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
    lines = lines[max(0, since_line):]

    def _part_is_error(part: dict) -> bool:
        for key in ("isError", "is_error", "error"):
            val = part.get(key)
            if val is True or (key == "error" and ((isinstance(val, str) and bool(val.strip())) or isinstance(val, dict))):
                return True
        for key in ("status", "level"):
            if str(part.get(key) or "").strip().lower() in ("error", "failed", "failure"):
                return True
        for key in ("text", "content", "output", "message"):
            val = part.get(key)
            if isinstance(val, str) and _ERR_RE.search(val):
                return True
        return False

    def _is_error(data: dict) -> bool:
        msg = data.get("message")
        if not isinstance(msg, dict):
            return False
        content = msg.get("content")
        parts = content if isinstance(content, list) else [content]
        for part in parts:
            if isinstance(part, dict):
                if _part_is_error(part):
                    return True
            elif isinstance(part, str) and _ERR_RE.search(part):
                return True
        return data.get("isError", data.get("is_error")) is True

    acp_tools: dict[str, bool] = {}
    acp_anon_seq = 0
    total = errors = 0
    stream_failed = False
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
        acp_t = data.get("t")
        if isinstance(acp_t, str):
            if acp_t == "tool_call":
                st = str(data.get("status") or "").strip().lower()
                is_err_status = st in ("error", "failed", "failure")
                is_ok_status = st in ("completed", "done", "success", "ok")
                has_explicit_err = (
                    data.get("isError") is True
                    or data.get("is_error") is True
                    or bool(data.get("error"))
                )
                if is_err_status or is_ok_status or has_explicit_err:
                    tid = str(data.get("id") or "")
                    if not tid:
                        acp_anon_seq += 1
                        tid = f"__anon_{acp_anon_seq}"
                    acp_tools[tid] = is_err_status or _part_is_error(data)
            continue

        if data.get("type") == "GENERIC":
            status = str(data.get("status") or "").strip().upper()
            if status in ("DONE", "ERROR"):
                total += 1
                if status == "ERROR" or _is_error(data):
                    errors += 1
                elif _ERR_RE.search(str(data.get("content") or "")):
                    errors += 1
                continue

        etype = data.get("event")
        if etype == "tool_result":
            payload = data.get("tool_result") if isinstance(data.get("tool_result"), dict) else data
            total += 1
            if _part_is_error(payload) or _part_is_error(data):
                errors += 1
            continue
        if etype == "tool_call":
            payload = data.get("tool_call") if isinstance(data.get("tool_call"), dict) else data
            st = str(payload.get("status") or "").strip().upper()
            has_outcome = (
                st in ("DONE", "SUCCESS", "OK", "ERROR", "FAILED", "FAILURE")
                or isinstance(payload.get("isError"), bool)
                or isinstance(payload.get("is_error"), bool)
                or bool(payload.get("error"))
            )
            if has_outcome:
                total += 1
                if _part_is_error(payload) or _part_is_error(data):
                    errors += 1
                continue
        if etype == "result":
            res = data.get("result") if isinstance(data.get("result"), dict) else data
            st = str(res.get("status") or "").strip().upper()
            if st in ("ERROR", "FAILED", "FAILURE") or res.get("is_error") is True or res.get("isError") is True:
                stream_failed = True
            elif st in ("SUCCESS", "DONE", "OK"):
                stream_failed = False
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
    for failed in acp_tools.values():
        total += 1
        if failed:
            errors += 1
    if total:
        return (errors, total)
    return (1, 1) if stream_failed else None



def last_assistant_text(transcript: str | Path, max_chars: int | None = 4000) -> str:
    """Last plain-text assistant message in *transcript* ("" when none).

    Skips toolCall/toolResult/user/system lines and malformed JSON.
    Truncates to ``max_chars`` for display (default 4000); pass None
    for the full text (validation must see the true final line, where
    the verdict token lives — truncating the head drops it).
    """
    limit = max_chars
    try:
        path = Path(transcript)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    last = ""
    stream_deltas: list[str] = []
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
        acp_t = data.get("t")
        if isinstance(acp_t, str):
            if acp_t == "message":
                chunk = data.get("text")
                if isinstance(chunk, str) and chunk:
                    stream_deltas.append(chunk)
            elif acp_t == "turn_end":
                if stream_deltas:
                    last = "".join(stream_deltas).strip()
                    stream_deltas.clear()
            elif acp_t == "answer":
                stream_deltas.clear()
                last = ""
            continue

        etype = data.get("event")
        msg = data.get("message")

        # Clear stream deltas on new user/system input
        if (isinstance(msg, dict) and msg.get("role") in ("user", "system")) or \
           data.get("type") in ("USER", "SYSTEM", "user") or \
           etype in ("user_message", "init"):
            stream_deltas.clear()

        # Omp assistant message
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            texts = _texts(msg.get("content"))
            if texts:
                last = "\n".join(texts).strip()
                stream_deltas.clear()
            continue

        # Antigravity brain log PLANNER_RESPONSE
        if data.get("type") == "PLANNER_RESPONSE":
            content = str(data.get("content") or "").strip()
            if content:
                last = content
                stream_deltas.clear()
            continue

        # Antigravity stream-json events
        if etype == "result":
            res = data.get("result") or {}
            resp = str(res.get("response") or "").strip()
            if resp:
                last = resp
                stream_deltas.clear()
            continue
        elif etype == "step_update":
            su = data.get("step_update") or {}
            if su.get("step_type") == "agent_response":
                delta = su.get("text_delta")
                if isinstance(delta, str) and delta:
                    stream_deltas.append(delta)
            continue

    stream_text = "".join(stream_deltas).strip()
    final_text = stream_text if stream_text else last
    if limit is not None and len(final_text) > limit:
        final_text = final_text[:limit].rstrip() + "\n…(truncated)"
    return final_text


def _first_int(d: dict, keys: tuple[str, ...]) -> int | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _first_cost(d: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            tot = v.get("total")
            if isinstance(tot, (int, float)) and not isinstance(tot, bool):
                return float(tot)
    return None


def _first_str(d: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


_INPUT_KEYS = ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens", "input")
_OUTPUT_KEYS = ("output_tokens", "outputTokens", "completion_tokens", "completionTokens", "output")
_CACHED_KEYS = (
    "cached_tokens", "cachedTokens", "cache_read_input_tokens",
    "cacheReadInputTokens", "cacheRead", "cache_read", "cached",
)
_COST_KEYS = ("cost_usd", "costUsd", "total_cost_usd", "totalCostUsd", "cost")
_MODEL_KEYS = ("model_id", "modelId", "model")


def extract_usage_from_lines(lines: list[str]) -> dict[str, int | float | str | None]:
    """Aggregate token, cost, and model telemetry from ACP JSONL or stream-json lines.

    Tolerant by design: unparseable lines are skipped, never raised.
    Accepts raw JSONL lines as well as `ACP: {...}` prefixed log lines.
    """
    in_sum = out_sum = cache_sum = 0
    has_in = has_out = has_cache = False
    cost_sum = 0.0
    has_cost = False
    model_id = ""

    def _consume(payload: dict, extra: dict | None = None) -> None:
        nonlocal in_sum, out_sum, cache_sum, has_in, has_out, has_cache
        nonlocal cost_sum, has_cost, model_id
        iv = _first_int(payload, _INPUT_KEYS)
        if iv is not None:
            in_sum += iv
            has_in = True
        ov = _first_int(payload, _OUTPUT_KEYS)
        if ov is not None:
            out_sum += ov
            has_out = True
        cv = _first_int(payload, _CACHED_KEYS)
        if cv is not None:
            cache_sum += cv
            has_cache = True
        cost = _first_cost(payload, _COST_KEYS)
        if cost is None and extra is not None:
            cost = _first_cost(extra, _COST_KEYS)
        if cost is not None:
            cost_sum += cost
            has_cost = True
        m = _first_str(payload, _MODEL_KEYS)
        if not m and extra is not None:
            m = _first_str(extra, _MODEL_KEYS)
        if m:
            model_id = m

    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if text.startswith("ACP:"):
            text = text[4:].strip()
        if not text.startswith("{"):
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue

        # 1. ACP usage event: {"t": "usage", "usage": {...}}
        if data.get("t") == "usage":
            u = data.get("usage")
            if isinstance(u, dict):
                _consume(u, data)
            else:
                _consume(data)
            continue

        # 2. Headless stream-json result: {"event": "result", "result": {...}}
        if data.get("event") == "result":
            res = data.get("result")
            if isinstance(res, dict):
                u = res.get("usage") or res.get("tokens")
                if isinstance(u, dict):
                    _consume(u, res)
                else:
                    _consume(res, data)
            continue

        # 3. Claude/generic stream-json result: {"type": "result", "usage": {...}, "total_cost_usd": ...}
        if data.get("type") == "result":
            u = data.get("usage")
            if isinstance(u, dict):
                _consume(u, data)
            else:
                _consume(data)
            continue

        # 4. OMP assistant message: {"message": {"role": "assistant", "usage": {...}, "model": "..."}}
        msg = data.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            _consume(msg["usage"], msg)
            continue

        # 5. Generic top-level usage dict
        if isinstance(data.get("usage"), dict):
            _consume(data["usage"], data)

    return {
        "input_tokens": in_sum if has_in else None,
        "output_tokens": out_sum if has_out else None,
        "cached_tokens": cache_sum if has_cache else None,
        "cost_usd": round(cost_sum, 6) if has_cost else None,
        "model_id": model_id,
    }


def extract_usage(transcript: str | Path | None, since_line: int = 0) -> dict[str, int | float | str | None]:
    """Aggregate token, cost, and model telemetry from a .jsonl transcript file."""
    empty: dict[str, int | float | str | None] = {
        "input_tokens": None,
        "output_tokens": None,
        "cached_tokens": None,
        "cost_usd": None,
        "model_id": "",
    }
    if not transcript:
        return empty
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return empty
    return extract_usage_from_lines(lines[max(0, since_line):])
