"""CLI prodtest delete/cleanup/status tests (issue #102 review follow-up)."""
from __future__ import annotations

import json

from workagent import cli

from tests.cli_helpers import runner


def _seed(monkeypatch, tmp_path, rows: dict[str, dict]):
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    for key, kw in rows.items():
        sq.upsert_prodtest(db, key, **kw)
    monkeypatch.setattr(cli.store, "list_prodtests", lambda: sq.list_prodtests(db))
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    monkeypatch.setattr(cli.store, "delete_prodtest", lambda key: sq.delete_prodtest(db, key))
    return db


def test_status_prints_row_without_json(isolated_config, tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path, {"IPG-9": {"status": "passed"}})
    r = runner.invoke(cli.app, ["prodtest", "status", "IPG-9"])
    assert r.exit_code == 0, r.output
    assert "IPG-9" in r.stdout
    assert "passed" in r.stdout


def test_delete_unknown_key_exits_2(isolated_config, tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path, {})
    r = runner.invoke(cli.app, ["prodtest", "delete", "IPG-404"])
    assert r.exit_code == 2, r.output


def test_delete_removes_row(isolated_config, tmp_path, monkeypatch):
    db = _seed(monkeypatch, tmp_path, {"IPG-9": {"status": "failed"}})
    r = runner.invoke(cli.app, ["prodtest", "delete", "IPG-9", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == {"deleted": "IPG-9"}
    from workagent import store_sqlite as sq

    assert sq.get_prodtest(db, "IPG-9") is None


def test_cleanup_only_drops_passed(isolated_config, tmp_path, monkeypatch):
    db = _seed(monkeypatch, tmp_path, {
        "IPG-1": {"status": "passed"},
        "IPG-2": {"status": "failed"},
    })
    r = runner.invoke(cli.app, ["prodtest", "cleanup", "--yes", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out == {"results": [{"issue": "IPG-1", "status": "deleted"}]}
    from workagent import store_sqlite as sq

    assert sq.get_prodtest(db, "IPG-1") is None
    assert sq.get_prodtest(db, "IPG-2") is not None


def test_cleanup_dry_run_shape(isolated_config, tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path, {"IPG-1": {"status": "passed"}})
    r = runner.invoke(cli.app, ["prodtest", "cleanup", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == {"results": [{"issue": "IPG-1", "status": "cleanup-dry-run"}]}
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    assert sq.get_prodtest(db, "IPG-1") is not None
