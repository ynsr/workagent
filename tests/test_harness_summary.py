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
