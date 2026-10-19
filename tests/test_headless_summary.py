"""_publish_headless_summary: stdout/JSON/comment wiring."""
import json

from workagent import cli_harness as hh


def _transcript(tmp_path, text="assistant final words"):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "message", "message": {"role": "assistant",
        "content": [{"type": "text", "text": text}]}}) + "\n")
    return str(p)


def test_human_prints_stdout_and_posts_to_issue(tmp_path, monkeypatch, capsys):
    from workagent import store as _store
    monkeypatch.setattr(_store, "lookup_link",
                        lambda k: {"issue_url": "https://github.com/o/r/issues/7"})
    posted = []
    monkeypatch.setattr("workagent.issue_comments.post_issue_comment",
                        lambda url, body: posted.append((url, body)) or True)
    result = {"command": "start", "branch": "feat/x", "key": "github:o/r#7",
              "session_file": _transcript(tmp_path)}
    hh._publish_headless_summary(result, json_output=False)
    out = capsys.readouterr()
    assert "assistant final words" in out.out
    assert posted == [("https://github.com/o/r/issues/7", "assistant final words")]


def test_json_mode_carries_summary_field(tmp_path, monkeypatch, capsys):
    from workagent import store as _store
    monkeypatch.setattr(_store, "lookup_link", lambda k: {})
    result = {"command": "sync", "branch": "feat/y", "key": "k",
              "session_file": _transcript(tmp_path, "sync done")}
    hh._publish_headless_summary(result, json_output=True)
    out = capsys.readouterr()
    assert out.out == ""
    assert result["summary"] == "sync done"


def test_no_issue_link_prints_fallback_but_skips_post(tmp_path, monkeypatch, capsys):
    from workagent import store as _store
    monkeypatch.setattr(_store, "lookup_link", lambda k: {})
    posted = []
    monkeypatch.setattr("workagent.issue_comments.post_issue_comment",
                        lambda url, body: posted.append((url, body)) or True)
    result = {"command": "review", "branch": "feat/z", "key": "k",
              "session_file": str(tmp_path / "missing.jsonl")}
    hh._publish_headless_summary(result, json_output=False)
    out = capsys.readouterr()
    assert "no transcript summary" in out.out
    assert posted == []
