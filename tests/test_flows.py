"""Flow store tests (Task 1 TDD)."""
from __future__ import annotations

import json

import pytest

from workagent.errors import HarnessError


def _rejects(steps, needle, **kwargs):
    from workagent import store_flows as sf
    try:
        sf.validate_template(steps, **kwargs)
    except HarnessError as e:
        assert e.exit_code == 2, f"expected exit 2, got {e.exit_code}"
        assert needle in str(e), f"{needle!r} not in {e}"
    else:
        raise AssertionError(f"expected validate_template to reject {steps!r}")


def test_validate_template_rejects_review_cleanup_without_fix(tmp_path):
    from workagent import store_flows as sf
    steps = [{"kind": "review"}, {"kind": "cleanup"}]
    try:
        sf.validate_template(steps)
    except Exception as e:
        assert "fix_comments" in str(e)
    else:
        raise AssertionError("expected validate_template to reject")


def test_validate_template_ordering_matrix(tmp_path):
    _rejects([{"kind": "review"}, {"kind": "start"}], "start")
    _rejects([{"kind": "start"}, {"kind": "start"}], "start")
    _rejects([{"kind": "start"}, {"kind": "release"},
              {"kind": "cleanup"}], "release")
    _rejects([{"kind": "start"}, {"kind": "release"}], "cleanup")
    _rejects([{"kind": "frobnicate"}, {"kind": "cleanup"}], "unknown step kind")
    _rejects([{"kind": "start"}, {"kind": "release"},
              {"kind": "cleanup"}, {"kind": "release"}], "at most one")


def test_validate_template_action_rules(tmp_path):
    _rejects([{"kind": "start"}, {"kind": "cleanup"},
              {"kind": "action", "action_label": "x"}], "action")
    _rejects([{"kind": "start"}, {"kind": "action"},
              {"kind": "cleanup"}], "action_label")
    _rejects([{"kind": "start"}, {"kind": "action", "action_label": "Nope!"},
              {"kind": "cleanup"}], "action_label")
    _rejects([{"kind": "start"},
              {"kind": "action", "action_label": "nope"},
              {"kind": "cleanup"}], "nope", get_action=lambda label: None)


def test_validate_template_rejects_banned_flags(tmp_path):
    _rejects([{"kind": "review", "config": {"then-fix-comments": True}},
              {"kind": "fix_comments"}, {"kind": "cleanup"}],
             "then-fix-comments")
    _rejects([{"kind": "start"},
              {"kind": "cleanup", "config": {"hard_delete": True}}],
             "hard_delete")
    _rejects([{"kind": "start"},
              {"kind": "cleanup", "config": {"flags": "--no-squash"}}],
             "no-squash")


def test_validate_template_accepts_valid_chains(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template([{"kind": "start"}, {"kind": "cleanup"}])
    assert [s["kind"] for s in ok] == ["start", "cleanup"]
    assert ok[0]["config"] == {}
    ok = sf.validate_template(json.dumps([
        {"kind": "start"}, {"kind": "review"}, {"kind": "fix_comments"},
        {"kind": "action", "action_label": "lint"},
        {"kind": "sync"}, {"kind": "cleanup"}, {"kind": "release"}]),
        get_action=lambda label: {"label": label})
    assert [s["kind"] for s in ok] == [
        "start", "review", "fix_comments", "action",
        "sync", "cleanup", "release"]
    assert ok[3]["action_label"] == "lint"


def test_validate_template_action_without_cleanup(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template(
        [{"kind": "start"}, {"kind": "action", "action_label": "lint"}],
        get_action=lambda label: {"label": label})
    assert [s["kind"] for s in ok] == ["start", "action"]


def test_template_crud_round_trip(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    row = sf.create_template(db, name="End to End",
                             steps=json.dumps([{"kind": "start"}, {"kind": "cleanup"}]))
    assert row["label"] == "end_to_end"
    assert sf.get_template(db, "end_to_end")["name"] == "End to End"
    assert sf.list_templates(db)[0]["label"] == "end_to_end"
    upd = sf.update_template(db, "end_to_end", name="E2E")
    assert upd["name"] == "E2E"
    sf.delete_template(db, "end_to_end")
    assert sf.get_template(db, "end_to_end") is None


def test_flow_crud_round_trip(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    sf.create_template(db, name="End to End",
                       steps=json.dumps([{"kind": "start"}, {"kind": "cleanup"}]))
    flow = sf.create_flow(db, template_label="end_to_end")
    assert flow["status"] == "pending"
    assert flow["current_step"] == 0
    assert flow["step_results"] == []
    assert sf.get_flow(db, flow["id"])["template_label"] == "end_to_end"
    assert len(sf.list_flows(db)) == 1
    upd = sf.update_flow(db, flow["id"], status="running", current_step=1)
    assert upd["status"] == "running"
    assert upd["current_step"] == 1
    with pytest.raises(HarnessError) as ei:
        sf.update_flow(db, flow["id"], status="bogus")
    assert ei.value.exit_code == 2
    with pytest.raises(HarnessError) as ei:
        sf.update_flow(db, 99999, status="done")
    assert ei.value.exit_code == 2


def test_template_rename_ok_and_retargets_flows(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    sf.create_template(db, name="End to End",
                       steps=json.dumps([{"kind": "start"}, {"kind": "cleanup"}]))
    sf.create_flow(db, template_label="end_to_end")
    renamed = sf.update_template(db, "end_to_end", new_label="e2e_v2")
    assert renamed["label"] == "e2e_v2"
    assert sf.get_template(db, "end_to_end") is None
    assert sf.get_template(db, "e2e_v2")["name"] == "End to End"
    assert all(f["template_label"] == "e2e_v2" for f in sf.list_flows(db))


def test_template_rename_collision_rejected(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    sf.create_template(db, name="One",
                       steps=json.dumps([{"kind": "start"}, {"kind": "cleanup"}]))
    sf.create_template(db, name="Two",
                       steps=json.dumps([{"kind": "start"}, {"kind": "cleanup"}]))
    with pytest.raises(HarnessError) as ei:
        sf.update_template(db, "one", new_label="two")
    assert ei.value.exit_code == 2
    assert "two" in str(ei.value)


def test_action_rename_rewrites_template_steps(tmp_path, monkeypatch):
    from workagent import store, store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    sq.init_db(db)
    store.create_action(name="Lint", label="lint", prompt="lint it")
    store.create_template(name="Pipe",
                          steps=json.dumps([{"kind": "start"},
                                            {"kind": "action", "action_label": "lint"},
                                            {"kind": "cleanup"}]))
    row = store.update_action("lint", new_label="lint_v2")
    assert row["label"] == "lint_v2"
    tpl = store.get_template("pipe")
    assert tpl["steps"][1]["action_label"] == "lint_v2"
