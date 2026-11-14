"""Issues #62/#84: AI-suggested Lucide icon — parse/validate, icon column, migration, CLI guard, full-list prompt + used-icons avoidance."""

import pytest


def test_parse_suggested_icon_bare_token():
    from workagent import suggest_icon as si
    assert si.parse_suggested_icon("Rocket") == "Rocket"


def test_parse_suggested_icon_case_insensitive():
    from workagent import suggest_icon as si
    assert si.parse_suggested_icon("  rocket  ") == "Rocket"


def test_parse_suggested_icon_json_last_line():
    from workagent import suggest_icon as si
    text = 'thinking...\n{"icon": "Rocket"}'
    assert si.parse_suggested_icon(text) == "Rocket"


def test_parse_suggested_icon_garbage_returns_none():
    from workagent import suggest_icon as si
    assert si.parse_suggested_icon("not-an-icon-xyz-123") is None
    assert si.parse_suggested_icon("") is None
    assert si.parse_suggested_icon("   \n  ") is None


def test_validate_suggested_icon_accepts_and_rejects():
    from workagent import suggest_icon as si
    from workagent.errors import HarnessError
    assert si.validate_suggested_icon("Rocket") == "Rocket"
    with pytest.raises(HarnessError):
        si.validate_suggested_icon(None)


def test_template_icon_round_trip_defaults_zap(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    row = sf.create_template(db, name="Icon Flow", steps=[{"kind": "cleanup"}])
    assert row["icon"] == "Zap"
    updated = sf.update_template(db, row["label"], icon="Rocket")
    assert updated["icon"] == "Rocket"
    assert sf.get_template(db, row["label"])["icon"] == "Rocket"


def test_template_icon_rejects_bad_identifier(tmp_path):
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    from workagent.errors import HarnessError
    db = tmp_path / "state.db"
    sq.init_db(db)
    with pytest.raises(HarnessError):
        sf.create_template(db, name="Bad Icon", steps=[{"kind": "cleanup"}],
                           icon="not an icon!")


def test_flow_template_icon_migration_backfills_zap(tmp_path):
    import sqlite3
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(flow_templates)")}
        assert "icon" in cols
        conn.execute(
            "INSERT INTO flow_templates (label, name, steps, pre_execution, post_execution,"
            " parent_labels, protected, sort_order, dedicated_top_bar, dedicated_overlay,"
            " category, run_immediately, created_at, modified_at)"
            " VALUES ('legacy', 'Legacy', '[]', '', '', '[]', 0, 0, 0, 0,"
            " 'worktree', 0, 't', 't')")
        conn.commit()
    finally:
        conn.close()
    sq.init_db(db)
    conn = sqlite3.connect(db)
    try:
        val = conn.execute("SELECT icon FROM flow_templates WHERE label='legacy'").fetchone()[0]
    finally:
        conn.close()
    assert val == "Zap"


def test_template_add_requires_steps_cli():
    from typer.testing import CliRunner
    import workagent.cli  # noqa: F401  (register commands)
    import workagent.cli_flow as cli_mod
    runner = CliRunner()
    result = runner.invoke(cli_mod.app, ["flow", "template", "add", "--name", "NoSteps"])
    assert result.exit_code == 2, result.output
    assert "at least one step" in result.output

def test_parse_suggested_icon_legacy_alias():
    from workagent import suggest_icon as si
    assert si.parse_suggested_icon("Wand2") == "WandSparkles"
    assert si.parse_suggested_icon("wand2") == "WandSparkles"


def test_suggest_prompt_lists_full_lucide_set():
    from workagent import suggest_icon as si
    assert len(si.SUGGEST_ICON_CHOICES) > 1000
    prompt = si.suggest_prompt(kind="custom action", name="N", label="l",
                               prompt="p", steps="", pre="", post="")
    for name in ("Rocket", "AlarmClock", "Zap"):
        assert name in prompt
    assert "do NOT suggest" not in prompt


def test_suggest_prompt_warns_about_used_icons():
    from workagent import suggest_icon as si
    prompt = si.suggest_prompt(kind="flow template", name="N", label="l",
                               prompt="", steps="[]", pre="", post="",
                               used=["Rocket", "Zap"])
    assert "do NOT suggest" in prompt
    assert "Rocket" in prompt and "Zap" in prompt


def test_used_icons_collects_both_stores_and_excludes_current(tmp_path):
    from workagent import suggest_icon as si
    from workagent import store_custom_actions as ca
    from workagent import store_flows as sf
    from workagent import store_sqlite as sq
    import workagent.store as store
    db = tmp_path / "state.db"
    sq.init_db(db)
    monkey_db = sq.db_path
    sq.db_path = lambda: db
    try:
        ca.create_action(db, name="Lint", icon="Rocket", prompt="lint it")
        sf.create_template(db, name="Flow", steps=[{"kind": "cleanup"}], icon="Zap")
        assert si.used_icons() == ["Rocket", "Zap"]
        assert si.used_icons(exclude_label="lint", exclude_kind="action") == ["Zap"]
        assert si.used_icons(exclude_label="flow", exclude_kind="flow") == ["Rocket"]
    finally:
        sq.db_path = monkey_db


def test_used_icons_never_raises():
    from workagent import suggest_icon as si
    import workagent.store as store
    orig = store.list_actions
    store.list_actions = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert si.used_icons() == []
    finally:
        store.list_actions = orig
