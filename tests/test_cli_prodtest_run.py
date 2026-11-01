"""CLI prodtest run tests (Task 5)."""
from __future__ import annotations

import json

from workagent import cli

from tests.cli_helpers import runner, _invoke


def test_run_refuses_without_release(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--dry-run", "--json"])
    assert r.exit_code != 0, r.output
    assert "release" in (r.stdout + r.stderr).lower()


def test_run_dry_run_with_release_ok(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", plan_path="p.yaml", plan_revision=1,
                       release_sha="abc1234", status="pending_prodtest")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert out["release_sha"] == "abc1234"


def test_run_refuses_bad_fix_mode(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", release_sha="abc1234")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--fix-mode", "auto"])
    assert r.exit_code != 0, r.output
    assert "fix-mode" in (r.stdout + r.stderr).lower()


def test_run_refuses_held_lock(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    sq.upsert_prodtest(db, "IPG-9", release_sha="abc1234")
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    from workagent import prodtest_lock as _lock

    base = tmp_path / "locks"
    base.mkdir()
    monkeypatch.setattr(cli.store, "config_dir", lambda: base)
    with _lock.held_issue_lock(str(base), "IPG-9"):
        r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--json"])
    assert r.exit_code != 0, r.output
    assert "already in progress" in (r.stdout + r.stderr).lower()
