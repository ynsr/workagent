"""Issue #62: AI-suggested Lucide icon — parse/validate, icon column, migration, CLI guard."""

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
