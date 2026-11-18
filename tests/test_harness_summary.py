"""harness_summary.last_assistant_text: transcript parsing."""
import json

from workagent import harness_summary as hs


def _write(path, lines):
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(path)


def test_last_plain_assistant_text_wins(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "first"}]}},
        {"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {}}]}},
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1",
                                        "content": [{"type": "text", "text": "out"}]}},
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "final words"}]}},
    ])
    assert hs.last_assistant_text(p) == "final words"


def test_skips_malformed_lines_and_missing_file(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('not json\n{"type":"session"}\n'
                 '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"kept"}]}}\n')
    assert hs.last_assistant_text(str(p)) == "kept"
    assert hs.last_assistant_text(str(tmp_path / "nope.jsonl")) == ""


def test_truncates_long_summary(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        {"type": "message", "message": {"role": "assistant",
                                        "content": [{"type": "text", "text": "x" * 5000}]}},
    ])
    out = hs.last_assistant_text(p)
    assert len(out) < 5000 and out.endswith("…(truncated)")


def test_agy_planner_response_and_generic_error_rate(tmp_path):
    p = _write(tmp_path / "agy.jsonl", [
        {"type": "agy_meta", "conversation_id": "c-1"},
        {"type": "GENERIC", "status": "DONE", "content": "tool ok"},
        {"type": "GENERIC", "status": "ERROR", "content": "tool failed"},
        {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "final answer"},
    ])
    assert hs.last_assistant_text(p) == "final answer"
    assert hs.tool_error_rate(p) == (1, 2)
    assert hs.tool_error_rate(p, since_line=2) == (1, 1)


def test_stream_json_fallback_in_harness_summary(tmp_path):
    # Only step_update deltas, followed by trailing non-text records, truncated by max_chars
    p = _write(tmp_path / "stream.jsonl", [
        {"event": "init", "conversation_id": "conv-99"},
        {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Part 1 "}},
        {"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Part 2"}},
        {"event": "tool_call", "tool_call": {"name": "bash"}},
    ])
    assert hs.last_assistant_text(p, max_chars=None) == "Part 1 Part 2"
    assert hs.last_assistant_text(p, max_chars=4) == "Part\n…(truncated)"


def test_tool_error_rate_returns_none_when_zero_tool_results(tmp_path):
    p = _write(tmp_path / "no_tools.jsonl", [
        {"type": "agy_meta", "conversation_id": "c-2"},
        {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "done"},
    ])
    assert hs.tool_error_rate(p) is None
    p2 = _write(tmp_path / "since_past.jsonl", [
        {"type": "GENERIC", "status": "DONE", "content": "ok"},
        {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "done"},
    ])
    assert hs.tool_error_rate(p2, since_line=1) is None


def test_tool_error_rate_scores_stream_json_events(tmp_path):
    p = _write(tmp_path / "stream_tools.jsonl", [
        {"event": "init", "conversation_id": "c-stream"},
        {"event": "tool_call", "tool_call": {"name": "bash"}},
        {"event": "tool_result", "tool_result": {"status": "DONE", "output": "ok"}},
        {"event": "tool_result", "tool_result": {"is_error": True, "output": "boom"}},
        {"event": "tool_call", "tool_call": {"name": "bash", "status": "ERROR", "error": "bad"}},
        {"event": "result", "result": {"status": "SUCCESS", "response": "done"}},
    ])
    assert hs.tool_error_rate(p) == (2, 3)
    assert hs.tool_error_rate(p, since_line=4) == (1, 1)

    p_err = _write(tmp_path / "stream_failed.jsonl", [
        {"event": "init", "conversation_id": "c-fail"},
        {"event": "result", "result": {"status": "ERROR", "response": "failed"}},
    ])
    assert hs.tool_error_rate(p_err) == (1, 1)

    p_ok = _write(tmp_path / "stream_ok.jsonl", [
        {"event": "init", "conversation_id": "c-ok"},
        {"event": "tool_call", "tool_call": {"name": "read"}},
        {"event": "result", "result": {"status": "SUCCESS", "response": "done"}},
    ])
    assert hs.tool_error_rate(p_ok) is None


