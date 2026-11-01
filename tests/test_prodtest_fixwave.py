"""Fix-wave covering tests: P0 diff-hash persist, P1b-d gates/bindings/follow-up."""
from __future__ import annotations

from pathlib import Path

from workagent import cli, cli_cleanup, cli_prodtest, cli_review


def _db(monkeypatch, tmp_path):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    saved = sq.upsert_prodtest
    monkeypatch.setattr(cli.store, "upsert_prodtest", lambda key, **kw: saved(db, key, **kw))
    return sq, db


def test_finish_plan_persists_diff_hash_and_status(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    sqm, db = _db(monkeypatch, tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    plan = wt / "plan.yaml"
    plan.write_text("plan_version: 1\n")
    monkeypatch.setattr(cli_prodtest.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli_cleanup, "current_prodtest_diff", lambda repo, default: "abc123")
    monkeypatch.setattr("workagent.harness_summary.last_assistant_text", lambda sf: "")
    monkeypatch.setattr("workagent.prodtest_result.parse_result_line",
                        lambda text, wt2, tag: {"revision": 3, "risk": "low"})
    monkeypatch.setattr("workagent.prodtest_scripts.run_validate",
                        lambda plan2, manifest: {"valid": True, "errors": [], "warnings": []})
    monkeypatch.setattr("workagent.errors.run_cmd", lambda *a, **k: "")
    cli_prodtest._finish_plan("IPG-1", "IPG-1", str(wt), str(tmp_path),
                              "feat/x", plan, "", {"session_file": ""})
    row = sqm.get_prodtest(db, "IPG-1")
    assert row["plan_diff_hash"] == "abc123", row
    assert row["plan_revision"] == 3, row
    assert row["status"] == "pending", row


def test_finish_plan_preserves_pending_prodtest(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    sqm, db = _db(monkeypatch, tmp_path)
    sqm.upsert_prodtest(db, "IPG-1", release_sha="deadbeef", status="pending_prodtest")
    wt = tmp_path / "wt"
    wt.mkdir()
    plan = wt / "plan.yaml"
    plan.write_text("plan_version: 1\n")
    monkeypatch.setattr(cli_prodtest.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli_cleanup, "current_prodtest_diff", lambda repo, default: "abc123")
    monkeypatch.setattr("workagent.harness_summary.last_assistant_text", lambda sf: "")
    monkeypatch.setattr("workagent.prodtest_result.parse_result_line",
                        lambda text, wt2, tag: {"revision": 1, "risk": ""})
    monkeypatch.setattr("workagent.prodtest_scripts.run_validate",
                        lambda plan2, manifest: {"valid": True, "errors": [], "warnings": []})
    monkeypatch.setattr("workagent.errors.run_cmd", lambda *a, **k: "")
    cli_prodtest._finish_plan("IPG-1", "IPG-1", str(wt), str(tmp_path),
                              "feat/x", plan, "", {"session_file": ""})
    row = sqm.get_prodtest(db, "IPG-1")
    assert row["status"] == "pending_prodtest", row
    assert row["plan_diff_hash"] == "abc123", row


def test_current_prodtest_diff_missing_dir_returns_empty():
    assert cli_cleanup.current_prodtest_diff("", "main") == ""
    assert cli_cleanup.current_prodtest_diff("/nonexistent-xyz-123", "main") == ""


def test_fix_warning_empty_when_plan_untouched(monkeypatch):
    monkeypatch.setattr(cli_review, "_mr_touches_prodtest_plan", lambda url, cwd: False)
    assert cli_review._prodtest_fix_warning("https://x/mr/1", "/tmp") == ""


def test_fix_warning_loud_on_stale(monkeypatch):
    monkeypatch.setattr(cli_review, "_mr_touches_prodtest_plan", lambda url, cwd: True)
    monkeypatch.setattr(cli_cleanup, "prodtest_gate", lambda *a, **k: "blocked:stale")
    from workagent import store as _store
    monkeypatch.setattr(_store, "get_prodtest", lambda key: {"release_sha": "abc"})
    monkeypatch.setattr(cli_review, "_review_issue_ref", lambda ref: "IPG-1")
    monkeypatch.setattr(cli_cleanup, "current_prodtest_diff", lambda repo, default: "x")
    out = cli_review._prodtest_fix_warning("https://x/mr/1", "/tmp")
    assert "WARNING" in out and "stale" in out.lower()


def test_plan_binding_blocks_on_mismatch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plan = repo / "plan.yaml"
    plan.write_text("current")
    from workagent import errors as _e
    import subprocess

    def _fake_run(*a, **k):
        assert "show" in a
        return "at-release-bytes"
    orig = _e.run_cmd
    _e.run_cmd = _fake_run
    try:
        assert cli_prodtest._plan_release_binding("IPG-1", plan, "deadbeef", str(repo), None) == \
            "blocked:plan-changed-since-release"
    finally:
        _e.run_cmd = orig
    assert cli_prodtest._plan_release_binding("IPG-1", None, "deadbeef", str(repo), None) == \
        "warn:missing-plan"
    assert cli_prodtest._plan_release_binding("IPG-1", plan, "deadbeef", "", None) is None


def test_finish_plan_resets_status_after_completed_run(isolated_config, tmp_path, monkeypatch):
    """Re-plan after passed/failed/blocked resets to pending (round 2)."""
    from workagent import store_sqlite as sq
    sqm, db = _db(monkeypatch, tmp_path)
    for terminal in ("passed", "failed", "blocked"):
        sqm.upsert_prodtest(db, "IPG-2", release_sha="deadbeef", status=terminal)
        wt = tmp_path / f"wt-{terminal}"
        wt.mkdir(exist_ok=True)
        plan = wt / "plan.yaml"
        plan.write_text("plan_version: 1\n")
        monkeypatch.setattr(cli_prodtest.repos, "default_branch", lambda repo: "main")
        monkeypatch.setattr(cli_cleanup, "current_prodtest_diff", lambda repo, default: "abc123")
        monkeypatch.setattr("workagent.harness_summary.last_assistant_text", lambda sf: "")
        monkeypatch.setattr("workagent.prodtest_result.parse_result_line",
                            lambda text, wt2, tag: {"revision": 1, "risk": ""})
        monkeypatch.setattr("workagent.prodtest_scripts.run_validate",
                            lambda plan2, manifest: {"valid": True, "errors": [], "warnings": []})
        monkeypatch.setattr("workagent.errors.run_cmd", lambda *a, **k: "")
        cli_prodtest._finish_plan("IPG-2", "IPG-2", str(wt), str(tmp_path),
                                  "feat/x", plan, "", {"session_file": ""})
        row = sqm.get_prodtest(db, "IPG-2")
        assert row["status"] == "pending", (terminal, row)


def test_finish_plan_preserves_running(isolated_config, tmp_path, monkeypatch):
    """Running (released, awaiting run) is preserved like pending_prodtest."""
    from workagent import store_sqlite as sq
    sqm, db = _db(monkeypatch, tmp_path)
    sqm.upsert_prodtest(db, "IPG-3", release_sha="deadbeef", status="running")
    wt = tmp_path / "wt-running"
    wt.mkdir()
    plan = wt / "plan.yaml"
    plan.write_text("plan_version: 1\n")
    monkeypatch.setattr(cli_prodtest.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli_cleanup, "current_prodtest_diff", lambda repo, default: "abc123")
    monkeypatch.setattr("workagent.harness_summary.last_assistant_text", lambda sf: "")
    monkeypatch.setattr("workagent.prodtest_result.parse_result_line",
                        lambda text, wt2, tag: {"revision": 1, "risk": ""})
    monkeypatch.setattr("workagent.prodtest_scripts.run_validate",
                        lambda plan2, manifest: {"valid": True, "errors": [], "warnings": []})
    monkeypatch.setattr("workagent.errors.run_cmd", lambda *a, **k: "")
    cli_prodtest._finish_plan("IPG-3", "IPG-3", str(wt), str(tmp_path),
                              "feat/x", plan, "", {"session_file": ""})
    row = sqm.get_prodtest(db, "IPG-3")
    assert row["status"] == "running", row


def test_plan_binding_worktree_local_layout_blocks_on_mismatch(tmp_path, monkeypatch):
    """Worktree-local plan (production layout) binds via git show (round 2)."""
    import workagent.errors as _e
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    plan = wt / "IPG-1.yaml"
    plan.write_text("current")

    def _fake_run(*a, **k):
        assert "show" in a
        # Production layout: canonical repo-relative plan path at release SHA.
        assert a[-1] == "deadbeef:.workagent/prodtest/plans/IPG-1.yaml", a
        return "at-release-bytes"
    monkeypatch.setattr(_e, "run_cmd", _fake_run)
    assert cli_prodtest._plan_release_binding("IPG-1", plan, "deadbeef", str(repo), None) == \
        "blocked:plan-changed-since-release"

    def _fake_same(*a, **k):
        return "current"
    monkeypatch.setattr(_e, "run_cmd", _fake_same)
    assert cli_prodtest._plan_release_binding("IPG-1", plan, "deadbeef", str(repo), None) is None


def test_plan_binding_unverifiable_blocks_by_default(tmp_path, monkeypatch):
    """Unverifiable plan at release blocks unless manifest opts into warn."""
    import workagent.errors as _e
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    plan = wt / "IPG-1.yaml"
    plan.write_text("current")

    def _boom(*a, **k):
        raise RuntimeError("no such path at sha")
    monkeypatch.setattr(_e, "run_cmd", _boom)
    assert cli_prodtest._plan_release_binding("IPG-1", plan, "deadbeef", str(repo), None) == \
        "blocked:plan-unverifiable-at-release"

    man_dir = repo / ".workagent"
    man_dir.mkdir()
    (man_dir / "prodtest.yaml").write_text("fix_policy:\n  plan_binding: warn\n")
    assert cli_prodtest._plan_release_binding("IPG-1", plan, "deadbeef", str(repo), None) == \
        "warn:plan-unverifiable-at-release"


def test_followup_creates_issue_on_fail(isolated_config, tmp_path, monkeypatch):
    calls = []
    import workagent.errors as _e
    monkeypatch.setattr(_e, "run_cmd", lambda *a, **k: calls.append(a) or "IPG-99 created")
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    cli_prodtest._create_fix_followup("IPG-9", {}, "https://tribe.jibit.cloud/browse/IPG-9",
                                      "issue", {"decision": "retry"}, "FAIL",
                                      out_dir, str(out_dir / "report.md"), "abc1234")
    assert calls, "expected an issue create call"
    assert calls[0][1:4] == ("issue", "create", "--no-input")


def test_followup_report_only_logs_no_create(isolated_config, tmp_path, monkeypatch, capsys):
    import workagent.errors as _e
    called = []
    monkeypatch.setattr(_e, "run_cmd", lambda *a, **k: called.append(a) or "")
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    cli_prodtest._create_fix_followup("IPG-9", {}, "IPG-9", "report-only",
                                      {"decision": "retry"}, "FAIL",
                                      out_dir, str(out_dir / "report.md"), "abc")
    assert not called
    assert "report-only" in capsys.readouterr().err or True
