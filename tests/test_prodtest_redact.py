"""Tests for prodtest redaction + result-line parsing utilities (Task 1)."""
from __future__ import annotations


def test_redact_bearer_jwt_card(tmp_path):
    from workagent import prodtest_redact as pr
    raw = "auth Bearer abcdefghijklmnopqrst header eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c card 4111111111111111"
    out = pr.redact(raw)
    assert "abcdefghijklmnopqrst" not in out
    assert "4111111111111111" not in out
    assert "4111" not in out or out.count("*") > 0


def test_parse_result_prefers_file(tmp_path):
    import json
    from workagent import prodtest_result as pr
    (tmp_path / "result.json").write_text(json.dumps({"plan_file": "p.yaml"}))
    got = pr.parse_result_line("noise\nPRODTEST_PLAN_RESULT: {\"plan_file\": \"x\"}\n", str(tmp_path), "PRODTEST_PLAN")
    assert got["plan_file"] == "p.yaml"
