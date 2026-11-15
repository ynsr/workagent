"""Prodtest decoupling tests (issue #50): running transition, run_results,
issue-key-only plan/run, list/status, cleanup non-blocking + create row."""
from __future__ import annotations

import json


def _seed(monkeypatch, tmp_path, links=None, rows=None):
    from workagent import cli
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    for key, fields in (rows or {}).items():
        sq.upsert_prodtest(db, key, **fields)
    monkeypatch.setattr(cli.store, "load_links", lambda **k: dict(links or {}))
    monkeypatch.setattr(cli.store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    saved = sq.upsert_prodtest
    monkeypatch.setattr(cli.store, "upsert_prodtest", lambda key, **kw: saved(db, key, **kw))
    monkeypatch.setattr(cli.store, "list_prodtests", lambda: sq.list_prodtests(db))
    return db


def test_preview_run_never_marks_running(isolated_config, tmp_path, monkeypatch):
    """Preview/interactive runs skip the running upsert (no stuck GC-blocking row)."""
    from workagent import cli_prodtest_checks as cpc
    from workagent import cli

    repo = tmp_path / "repo"
    repo.mkdir()
    db = _seed(monkeypatch, tmp_path, links={}, rows={"IPG-9": {"release_sha": "abc1234"}})
    seen: list[tuple] = []
    orig_upsert = cli.store.upsert_prodtest

    def _spy(key, **kw):
        seen.append((key, dict(kw)))
        return orig_upsert(key, **kw)

    monkeypatch.setattr(cli.store, "upsert_prodtest", _spy)
    monkeypatch.setattr("workagent.release_worktree.prepare", lambda repo_, dest, sha: tmp_path)
    monkeypatch.setattr(cpc, "_enforce_plan_release_binding", lambda *a, **k: None)
    monkeypatch.setattr(cpc, "_run_validated", lambda *a, **k: None)

    entry = {"repo": str(repo), "worktree": "", "branch": "feat/x", "issue": "IPG-9"}
    cpc._run_locked("IPG-9", entry, None, "abc1234", "", "report-only",
                    False, tmp_path / "out", 60, False, False, True, False,
                    None, None, None, None)
    running_marks = [kw for _, kw in seen if kw.get("status") == "running"]
    assert not running_marks, seen
    from workagent import store_sqlite as sq
    row = sq.get_prodtest(db, "IPG-9")
    assert row and row.get("status") != "running", row

def test_run_marks_running_then_passed_with_run_results(isolated_config, tmp_path, monkeypatch):
    """_run_locked upserts running; _finish_run persists passed + run_results."""
    from workagent import cli_prodtest_checks as cpc
    from workagent import cli

    repo = tmp_path / "repo"
    repo.mkdir()
    db = _seed(monkeypatch, tmp_path, links={}, rows={"IPG-9": {"release_sha": "abc1234"}})
    seen: list[tuple] = []
    orig_upsert = cli.store.upsert_prodtest

    def _spy(key, **kw):
        seen.append((key, dict(kw)))
        return orig_upsert(key, **kw)

    monkeypatch.setattr(cli.store, "upsert_prodtest", _spy)
    monkeypatch.setattr("workagent.release_worktree.prepare", lambda repo_, dest, sha: tmp_path)
    monkeypatch.setattr(cpc, "_enforce_plan_release_binding", lambda *a, **k: None)
    monkeypatch.setattr(cpc, "_run_validated",
                        lambda *a, **k: cpc._finish_run(
                            a[0], a[1], a[2], a[3], a[4], a[5], tmp_path / "out", 60,
                            {"session_file": ""}))

    entry = {"repo": str(repo), "worktree": "", "branch": "feat/x", "issue": "IPG-9"}
    cpc._run_locked("IPG-9", entry, None, "abc1234", "", "report-only",
                    False, tmp_path / "out", 60, True, False, True, False,
                    None, None, None, None)
    from workagent import store_sqlite as sq
    row = sq.get_prodtest(db, "IPG-9")
    assert row and row.get("status") in ("passed", "failed", "blocked"), row
    assert row.get("last_run_id"), row
    payload = json.loads(row.get("run_results") or "{}")
    assert payload.get("release_sha") == "abc1234", payload
    assert set(payload) <= {"overall", "run_id", "report_file", "release_sha"}, payload
    assert seen and seen[0] == ("IPG-9", {"status": "running", "worktree_ref": ""}), seen


def test_plan_and_run_without_linked_worktree(isolated_config, tmp_path, monkeypatch):
    """Issue-key-only refs resolve via the repo checkout (no linked worktree)."""
    from workagent import cli
    from tests.cli_helpers import runner

    repo = tmp_path / "repo"
    (repo / ".workagent" / "prodtest" / "plans").mkdir(parents=True)
    plan = repo / ".workagent" / "prodtest" / "plans" / "IPG-9.yaml"
    plan.write_text("plan_version: 1\n")
    _seed(monkeypatch, tmp_path, links={}, rows={"IPG-9": {"release_sha": "abc1234"}})
    monkeypatch.setattr("workagent.cli_prodtest.repos.repo_root", lambda *a, **k: repo)
    import workagent.worktrees as _wt
    monkeypatch.setattr("workagent.worktrees.resolve_any",
                        lambda ref, links: (_ for _ in ()).throw(_wt.NoLinkedState("x")))
    issue_parsed = {"kind": "issue", "tool": "jira", "repo": "",
                    "number": "IPG-9", "url": ""}
    monkeypatch.setattr("workagent.cli_prodtest.refs.parse_ref", lambda ref: dict(issue_parsed))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo_: "main")
    r = runner.invoke(cli.app, ["prodtest", "run", "IPG-9", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["release_sha"] == "abc1234"
    assert str(plan) in str(out["plan_file"])
    r = runner.invoke(cli.app, ["prodtest", "plan", "IPG-9", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["plan_file"] == str(plan)

def test_list_and_status(isolated_config, tmp_path, monkeypatch):
    from workagent import cli
    from tests.cli_helpers import runner

    _seed(monkeypatch, tmp_path, links={},
          rows={"IPG-1": {"status": "passed", "plan_path": "p.yaml",
                          "plan_revision": 2, "plan_risk": "low",
                          "release_sha": "abc", "last_run_id": "r1"}})
    r = runner.invoke(cli.app, ["prodtest", "list", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.stdout)
    assert rows and rows[0]["issue"] == "IPG-1", rows
    assert rows[0]["status"] == "passed" and rows[0]["release"] == "abc"
    r = runner.invoke(cli.app, ["prodtest", "status", "IPG-1", "--json"])
    assert r.exit_code == 0, r.output
    row = json.loads(r.stdout)
    assert row["issue"] == "IPG-1" and row["revision"] == 2, row


def test_cleanup_no_longer_blocks_on_stale_plan(isolated_config, tmp_path, monkeypatch):
    """Stale/invalid plans never block cleanup --merge/--merge-only/single."""
    from workagent import cli
    from workagent import cli_cleanup as cc
    from tests.cli_helpers import _invoke

    wt = tmp_path / "wt"
    wt.mkdir()
    links = {"jira:IPG-1": {"issue": "IPG-1", "worktree": str(wt),
                            "branch": "feat/1", "repo": str(tmp_path)}}
    from workagent import store
    _seed(monkeypatch, tmp_path, links=links,
          rows={"IPG-1": {"plan_path": "p.yaml", "plan_diff_hash": "old",
                          "status": "pending"}})
    monkeypatch.setattr(store, "load_links", lambda **k: dict(links))
    monkeypatch.setattr("workagent.cli_cleanup.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr("workagent.cli.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda key, entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    monkeypatch.setattr(cc, "current_prodtest_diff", lambda repo, default: "new")
    cleaned = []
    monkeypatch.setattr(cli, "_cleanup_one",
                        lambda key, entry, *a, **k: cleaned.append(key) or {"key": key, "status": "cleaned"})
    monkeypatch.setattr(cc, "_cleanup_one",
                        lambda key, entry, *a, **k: cleaned.append(key) or {"key": key, "status": "cleaned"})
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    assert "jira:IPG-1" in cleaned


def test_cleanup_create_prodtest_row(isolated_config, tmp_path, monkeypatch):
    """--create-prodtest upserts the row, then teardown proceeds."""
    from workagent import cli_cleanup as cc
    from workagent import store_sqlite as sq

    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    monkeypatch.setattr(cc.store, "upsert_prodtest", lambda key, **kw: sq.upsert_prodtest(db, key, **kw))
    cc._ensure_prodtest_row("jira:IPG-7", {"issue": "IPG-7", "branch": "b"})
    row = sq.get_prodtest(db, "IPG-7")
    assert row, row
    assert row.get("worktree_ref") == "jira:IPG-7", row
