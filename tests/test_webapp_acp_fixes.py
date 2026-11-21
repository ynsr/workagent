"""Failing-first tests for reviewer findings (issue #112 fixes)."""

from __future__ import annotations

import json

from tests.webapp_helpers import client  # noqa: F401 (pytest fixture)
from tests.test_webapp_acp import _seed_running


def test_run_input_persists_qid(client, monkeypatch, tmp_path):
    """POST /input keeps the qid so _read_answer can correlate."""
    run_id, gate = _seed_running(client, monkeypatch)
    run = client.app.state.registry.get(run_id)
    run_dir = tmp_path / "rundir"
    run_dir.mkdir()
    run.run_dir = str(run_dir)
    r = client.post(f"/api/runs/{run_id}/input",
                    json={"text": "Yes", "qid": "call-ask-1"})
    assert r.status_code == 200, r.text
    payload = json.loads((run_dir / "input.jsonl").read_text().strip())
    assert payload == {"text": "Yes", "qid": "call-ask-1"}
    gate.set()


def test_pending_question_normalizes_dict_options(client, monkeypatch):
    """Agy dict options surface as label strings (QuestionCard-safe)."""
    run_id, gate = _seed_running(client, monkeypatch)
    reg = client.app.state.registry
    run = reg.get(run_id)
    run.append('ACP: {"t": "question", "qid": "q1", "text": "proceed?",'
               ' "options": [{"label": "Yes"}, {"label": "No"}], "multi": false}')
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["pending_question"]["options"] == ["Yes", "No"]
    gate.set()


def test_coemitted_turn_end_keeps_pending_question(client, monkeypatch):
    """A non-waiting turn_end from the same unanswered turn keeps the question."""
    run_id, gate = _seed_running(client, monkeypatch)
    reg = client.app.state.registry
    run = reg.get(run_id)
    run.append('ACP: {"t": "question", "qid": "q1", "text": "proceed?",'
               ' "options": ["yes"], "multi": false}')
    run.append('ACP: {"t": "turn_end", "id": "s1"}')
    body = client.get(f"/api/runs/{run_id}").json()
    assert body["pending_question"] is not None
    assert body["pending_question"]["qid"] == "q1"
    # A later answer event clears it.
    run.append('ACP: {"t": "answer", "qid": "q1", "text": "yes"}')
    body2 = client.get(f"/api/runs/{run_id}").json()
    assert body2["pending_question"] is None
    gate.set()
