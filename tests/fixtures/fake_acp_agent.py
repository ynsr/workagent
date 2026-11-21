"""Fake ACP agent for tests: speaks stdio JSON-RPC like `omp acp` / `agy-guard acp`.

Usage: spawned as a subprocess with argv [sys.executable, this_file, MODE].
MODE selects behavior:
  omp  — recorded omp `session/new` payload, agent text message, end_turn.
  agy  — recorded agy `session/new` payload with availableModels, an
         `ask_question` tool_call, then end_turn.
  chunks — streams agent_message_chunk/agent_thought_chunk + usage_update,
         emits a session/request_permission (client must auto-allow),
         then end_turn.
  hang   — never responds (timeout tests).
  badline — emits a malformed line before a normal session/new response.

Protocol: line-delimited JSON-RPC 2.0. Client requests have `id` + `method`;
agent replies `{id, result}`. Agent → client requests (request_permission)
carry `method` + `id` and expect a client `{id, result}` reply.
Session notifications go to stdout as `{"method": "session/update", ...}`.
"""

from __future__ import annotations

import json
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "omp"
# "agy_once" behaves like "agy" but asks only on the first prompt turn
# (the answer turn completes quietly), so launch_acp terminates.

# Recorded `session/new` result payloads (spec §Verified protocol facts).
OMP_SESSION_NEW = {
    "sessionId": "sess-omp-1",
    "configOptions": [
        {"configId": "mode", "name": "Mode",
         "options": [{"value": "default", "name": "Default"}],
         "currentValue": "default"},
        {"configId": "model", "name": "Model", "category": "model",
         "options": [{"value": "bifrost/cline-pass/cline-pass/muse-spark-1.2", "name": "Muse Spark"}],
         "currentValue": "bifrost/cline-pass/cline-pass/muse-spark-1.2"},
        {"configId": "thinking", "name": "Thinking", "category": "thought_level",
         "options": [{"value": v, "name": v} for v in ("off", "low", "medium", "high", "xhigh")],
         "currentValue": "medium"},
    ],
}

AGY_SESSION_NEW = {
    "sessionId": "sess-agy-1",
    "models": {
        "availableModels": [
            {"modelId": "gemini-3.8-flash-low", "name": "Flash Low"},
            {"modelId": "gemini-3.8-pro-high", "name": "Pro High"},
        ],
        "currentModelId": "gemini-3.8-flash-low",
    },
    "configOptions": [
        {"configId": "model", "name": "Model",
         "options": [{"value": "gemini-3.8-flash-low", "name": "Flash Low"},
                     {"value": "gemini-3.8-pro-high", "name": "Pro High"}],
         "currentValue": "gemini-3.8-flash-low"},
        {"configId": "thought_level", "name": "Effort",
         "options": [{"value": v, "name": v} for v in ("low", "medium", "high", "xhigh")],
         "currentValue": "low"},
        {"configId": "mode", "name": "Mode",
         "options": [{"value": "default", "name": "Default"}],
         "currentValue": "default"},
    ],
}

AGY_ASK_QUESTION = {
    "toolCallId": "call-ask-1",
    "title": "Need input",
    "kind": "ask_question",
    "status": "running",
    "rawInput": {
        "questions": [
            {"question": "Proceed with the fix?",
             "options": [{"label": "Yes"}, {"label": "No"}],
             "is_multi_select": False},
        ],
    },
}

_req_id = [100]


def _notify(update: dict) -> None:
    sys.stdout.write(json.dumps({"method": "session/update", "params": update}) + "\n")
    sys.stdout.flush()


def _reply(rid, result) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def _ask_client(method: str, params: dict):
    _req_id[0] += 1
    rid = _req_id[0]
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
    sys.stdout.flush()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") == rid:
            return msg.get("result")
    return None


def _end_turn(sid: str) -> None:
    _notify({"sessionId": sid, "update": {"sessionUpdate": "end_turn"}})


def main() -> None:
    sid = {"omp": "sess-omp-1", "agy": "sess-agy-1"}.get(MODE, "sess-1")
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            if MODE == "badline":
                # Client already saw our malformed line at startup; answer pings.
                pass
            continue
        if not isinstance(msg, dict) or "method" not in msg:
            continue
        method = msg["method"]
        rid = msg.get("id")
        if MODE == "hang":
            continue  # never respond
        if method == "initialize":
            _reply(rid, {"protocolVersion": 1})
        elif method == "authenticate":
            params = msg.get("params") or {}
            if MODE == "agy" and (params.get("methodId") or "") == "agent":
                sys.stdout.write(json.dumps(
                    {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32602, "message": "unknown method"}}) + "\n")
                sys.stdout.flush()
            else:
                _reply(rid, {})
        elif method == "session/new":
            if MODE == "agy":
                _reply(rid, AGY_SESSION_NEW)
            else:
                _reply(rid, dict(OMP_SESSION_NEW, sessionId=sid))
        elif method == "session/set_config_option":
            _reply(rid, {})
        elif method == "session/prompt":
            if MODE in ("agy", "agy_once") and not getattr(main, "_asked", False):
                main._asked = True  # type: ignore[attr-defined]
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": AGY_ASK_QUESTION["toolCallId"],
                    "title": AGY_ASK_QUESTION["title"],
                    "kind": "ask_question",
                    "status": "running",
                    "rawInput": AGY_ASK_QUESTION["rawInput"]}})
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": AGY_ASK_QUESTION["toolCallId"],
                    "status": "pending"}})
            elif MODE == "chunks":
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "agent_thought_chunk",
                    "content": {"type": "text", "text": "thinking..."}}})
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello "}}})
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "world"}}})
                _ask_client("session/request_permission", {
                    "sessionId": sid, "toolCallId": "perm-1",
                    "options": [{"optionId": "allow", "name": "Allow"}]})
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "usage_update",
                    "tokens": {"input": 10, "output": 20}}})
            else:
                _notify({"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "done work"}}})
            _end_turn(sid)
            time.sleep(0.2)
            _reply(rid, {"stopReason": "end_turn"})
        elif method == "session/load":
            _reply(rid, {"sessionId": sid})
        elif method in ("session/cancel", "shutdown", "exit"):
            _reply(rid, {})
            if method == "exit":
                return


if __name__ == "__main__":
    if MODE == "badline":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
    main()
