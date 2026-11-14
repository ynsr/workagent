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


def test_validate_template_prodtest_plan_rules(tmp_path):
    from workagent import store_flows as sf
    # prodtest_plan must come before cleanup.
    _rejects([{"kind": "start"}, {"kind": "cleanup"},
              {"kind": "prodtest_plan"}], "prodtest_plan")
    ok = sf.validate_template(
        [{"kind": "start"}, {"kind": "prodtest_plan"}, {"kind": "cleanup"}])
    assert [s["kind"] for s in ok] == ["start", "prodtest_plan", "cleanup"]


def test_validate_template_cleanup_merge_only(tmp_path):
    from workagent import store_flows as sf
    # merge_only selects merge-PR-only mode — not a banned override.
    ok = sf.validate_template(
        [{"kind": "start"},
         {"kind": "cleanup", "config": {"merge_only": True}}])
    assert ok[1]["config"] == {"merge_only": True}
    # Other hard-delete/no-squash overrides stay banned.
    _rejects([{"kind": "start"},
              {"kind": "cleanup", "config": {"hard_delete": True}}],
             "hard_delete")


def test_validate_template_action_without_cleanup(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template(
        [{"kind": "start"}, {"kind": "action", "action_label": "lint"}],
        get_action=lambda label: {"label": label})
    assert [s["kind"] for s in ok] == ["start", "action"]


def test_validate_template_action_without_start(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template(
        [{"kind": "action", "action_label": "lint"}],
        get_action=lambda label: {"label": label})
    assert [s["kind"] for s in ok] == ["action"]
    assert ok[0]["action_label"] == "lint"

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

def test_template_row_parses_parent_labels_default(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    row = sf.create_template(db, name="T", label="t", steps='[{"kind": "sync"}]')
    assert row["parent_labels"] == []

def test_compose_chains_collapses_second_start(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    a = [{"kind": "start"}, {"kind": "sync"}]
    b = [{"kind": "start"}, {"kind": "cleanup"}]
    merged, parents, dropped = sf.compose_chains([("a", a), ("b", b)])
    assert [s["kind"] for s in merged] == ["start", "sync", "cleanup"]
    assert parents == ["a", "b"] and dropped is True


def test_compose_chains_index_mapped_error(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    from workagent.errors import HarnessError
    import pytest
    with pytest.raises(HarnessError) as ei:
        sf.compose_chains([("a", [{"kind": "sync"}]),
                           ("b", [{"kind": "release"}])])
    assert "b:1" in str(ei.value)


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


def test_template_display_columns(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    row = sf.create_template(db, name="Full", steps=[{"kind": "cleanup"}],
                             protected=True, sort_order=3,
                             dedicated_top_bar=True, dedicated_overlay=True)
    assert row["protected"] is True
    assert row["sort_order"] == 3
    assert row["dedicated_top_bar"] is True
    assert row["dedicated_overlay"] is True
    got = sf.get_template(db, row["label"])
    assert got is not None and got["run_immediately"] is False


def test_template_run_immediately_round_trip(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    row = sf.create_template(db, name="Quick Flow", steps=[{"kind": "cleanup"}], run_immediately=True)
    assert row["run_immediately"] is True
    assert sf.get_template(db, row["label"])["run_immediately"] is True
    updated = sf.update_template(db, row["label"], run_immediately=False)
    assert updated["run_immediately"] is False

def test_templates_ordered_by_sort_order(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    sf.create_template(db, name="Second", steps=[{"kind": "cleanup"}], sort_order=2)
    sf.create_template(db, name="First", steps=[{"kind": "cleanup"}], sort_order=1)
    labels = [r["label"] for r in sf.list_templates(db)]
    assert labels == ["first", "second"]


def test_release_step_rejects_bad_checks():
    import pytest
    from workagent import store_flows as sf
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError):
        sf.validate_template([{"kind": "cleanup"}, {"kind": "release", "config": {"checks": "all"}}])
    with pytest.raises(HarnessError):
        sf.validate_template([{"kind": "cleanup"}, {"kind": "release", "config": {"diff": "local2"}}])
    ok = sf.validate_template([{"kind": "cleanup"}, {"kind": "release", "config": {"checks": "both", "diff": "local"}}])
    assert ok[-1]["config"] == {"checks": "both", "diff": "local"}


def test_template_category_round_trip(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    row = sf.create_template(db, name="Msg flow", steps=[{"kind": "cleanup"}], category="message")
    assert row["category"] == "message"
    assert sf.get_template(db, row["label"])["category"] == "message"


def test_template_category_rejects_bad_value(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    from workagent.errors import HarnessError
    db = tmp_path / "state.db"
    sq.init_db(db)
    try:
        sf.create_template(db, name="X", steps=[{"kind": "cleanup"}], category="sms")
    except HarnessError as e:
        assert e.exit_code == 2
    else:
        raise AssertionError("expected HarnessError")

def test_execution_mode_defaults_sequential(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template([{"kind": "start"}, {"kind": "cleanup"}])
    assert [s["execution_mode"] for s in ok] == ["sequential", "sequential"]


def test_execution_mode_parallel_allowed(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template(
        [{"kind": "start"}, {"kind": "review", "execution_mode": "parallel"},
         {"kind": "fix_comments"}, {"kind": "cleanup"}])
    assert ok[1]["execution_mode"] == "parallel"
    assert ok[3]["execution_mode"] == "sequential"


def test_execution_mode_rejects_unknown_and_forced_sequential():
    _rejects([{"kind": "review", "execution_mode": "sometimes"},
              {"kind": "fix_comments"}, {"kind": "cleanup"}], "execution_mode")
    _rejects([{"kind": "start", "execution_mode": "parallel"},
              {"kind": "cleanup"}], "sequentially")
    _rejects([{"kind": "review", "execution_mode": "parallel"},
              {"kind": "fix_comments"},
              {"kind": "cleanup", "execution_mode": "parallel"}],
             "sequentially")
    _rejects([{"kind": "review"}, {"kind": "fix_comments"},
              {"kind": "cleanup"},
              {"kind": "release", "execution_mode": "parallel"}],
             "sequentially")

def test_execution_mode_case_insensitive(tmp_path):
    from workagent import store_flows as sf
    ok = sf.validate_template(
        [{"kind": "start"}, {"kind": "review", "execution_mode": "Parallel"},
         {"kind": "fix_comments"}, {"kind": "cleanup"}])
    assert ok[1]["execution_mode"] == "parallel"


def test_execution_mode_survives_template_round_trip(tmp_path, monkeypatch):
    from workagent import store_flows as sf
    db = tmp_path / "state.db"
    monkeypatch.setattr("workagent.store_sqlite.db_path", lambda: db)
    sf.create_template(db, name="Modes",
                       steps=[{"kind": "start"},
                              {"kind": "review", "execution_mode": "Parallel"},
                              {"kind": "fix_comments"}, {"kind": "cleanup"}])
    row = sf.get_template(db, "modes")
    assert [s["execution_mode"] for s in row["steps"]] == [
        "sequential", "parallel", "sequential", "sequential"]
