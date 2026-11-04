"""Prodtest gates for review/cleanup/release (Task 6)."""
from __future__ import annotations


def test_cleanup_blocks_stale_plan():
    from workagent import cli_cleanup as cc
    assert cc.prodtest_gate("IPG-1", {"plan_diff_hash": "old"}, current_diff="new", config={"mode": "block"}) == "blocked:stale"


def test_gate_warn_missing_plan_by_default():
    from workagent import cli_cleanup as cc
    assert cc.prodtest_gate("IPG-1", {}, current_diff="new", config={}) == "warn:missing-plan"


def test_gate_warn_missing_plan_explicit_warn_mode():
    from workagent import cli_cleanup as cc
    assert cc.prodtest_gate("IPG-1", {}, current_diff="new", config={"mode": "warn"}) == "warn:missing-plan"


def test_gate_none_when_no_item_and_falsy_mode():
    from workagent import cli_cleanup as cc
    assert cc.prodtest_gate("IPG-1", None, current_diff="new", config={"mode": ""}) is None


def test_gate_missing_plan_path_warns():
    from workagent import cli_cleanup as cc
    item = {"plan_diff_hash": "abc", "plan_path": ""}
    assert cc.prodtest_gate("IPG-1", item, current_diff="abc", config={"mode": "warn"}) == "warn:missing-plan"


def test_gate_passes_when_hash_matches_and_plan_valid(monkeypatch):
    from workagent import cli_cleanup as cc
    monkeypatch.setattr(cc, "run_validate", lambda plan, manifest: {"valid": True, "errors": [], "warnings": []})
    item = {"plan_path": "p.yaml", "plan_diff_hash": "abc"}
    assert cc.prodtest_gate("IPG-1", item, current_diff="abc",
                            config={"mode": "block"}, manifest_path="m.yaml") is None


def test_gate_blocks_invalid_plan(monkeypatch):
    from workagent import cli_cleanup as cc
    monkeypatch.setattr(cc, "run_validate", lambda plan, manifest: {"valid": False, "errors": ["bad"], "warnings": []})
    item = {"plan_path": "p.yaml", "plan_diff_hash": "abc"}
    assert cc.prodtest_gate("IPG-1", item, current_diff="abc",
                            config={"mode": "block"}, manifest_path="m.yaml") == "blocked:invalid-plan"


def test_manifest_path_for_prefers_explicit():
    from workagent import cli_cleanup as cc
    assert cc.manifest_path_for("IPG-1", explicit="m.yaml") == "m.yaml"


def test_manifest_path_for_defaults_repo_file(tmp_path):
    from workagent import cli_cleanup as cc
    assert cc.manifest_path_for("IPG-1", repo_dir=str(tmp_path)) == str(
        tmp_path / ".workagent" / "prodtest.yaml")


def test_row_gate_skipped_prodtest_stale_in_merged_loop(isolated_config, tmp_path, monkeypatch):
    from workagent import cli
    from workagent import cli_cleanup as cc
    from workagent import store
    from workagent import store_sqlite as sq
    from tests.cli_helpers import _invoke
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    wt = tmp_path / "wt"; wt.mkdir()
    # store.get_prodtest reads state.db while record_link writes links.json;
    # seed the isolated DB directly (same pattern as Task 5 run tests).
    sq.save_links_rows(db, {"jira:IPG-1": {"issue": "IPG-1", "worktree": str(wt),
        "branch": "feat/1", "repo": str(tmp_path), "added_at": "2026-11-01T00:00:00+00:00"}})
    sq.upsert_prodtest(db, "IPG-1", plan_path="p.yaml", plan_diff_hash="old",
                       status="pending")
    monkeypatch.setattr(store, "load_links",
                        lambda **k: sq.load_links_rows(db, include_inactive=True))
    monkeypatch.setattr(store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    monkeypatch.setattr("workagent.cli_cleanup.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr("workagent.cli.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda key, entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    monkeypatch.setattr(cc, "current_prodtest_diff", lambda repo, default: "new")
    monkeypatch.setattr(cc, "run_validate",
                        lambda plan, manifest: {"valid": True, "errors": [], "warnings": []})
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    import json as _json
    rows = _json.loads(r.stdout)["results"]
    assert rows[0]["status"] == "skipped:prodtest-stale"


def test_row_gate_warn_missing_passes_merged_loop(isolated_config, tmp_path, monkeypatch, capsys):
    from workagent import cli
    from workagent import cli_cleanup as cc
    from workagent import store
    from workagent import store_sqlite as sq
    from tests.cli_helpers import _invoke
    db = tmp_path / "state.db"
    sq.ensure_schema(db)
    wt = tmp_path / "wt"; wt.mkdir()
    sq.save_links_rows(db, {"jira:IPG-1": {"issue": "IPG-1", "worktree": str(wt),
        "branch": "feat/1", "repo": str(tmp_path), "added_at": "2026-11-01T00:00:00+00:00"}})
    monkeypatch.setattr(store, "load_links",
                        lambda **k: sq.load_links_rows(db, include_inactive=True))
    monkeypatch.setattr(store, "get_prodtest", lambda key: sq.get_prodtest(db, key))
    monkeypatch.setattr("workagent.cli_cleanup.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr("workagent.cli.worktrees.is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda key, entry, refresh_pr=False: {"pr_data": {"state": "MERGED"}})
    monkeypatch.setattr(cc, "current_prodtest_diff", lambda repo, default: "")
    cleaned = []
    def _fake_cleanup(key, entry, *a, **k):
        cleaned.append(entry.get("branch"))
        return {"key": key, "branch": entry.get("branch"), "status": "cleaned"}
    monkeypatch.setattr(cli, "_cleanup_one", _fake_cleanup)
    monkeypatch.setattr(cc, "_cleanup_one", _fake_cleanup)
    r = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r.exit_code == 0, r.output
    import json as _json
    rows = _json.loads(r.stdout)["results"]
    assert rows[0]["status"] == "cleaned"

def test_row_gate_hashes_worktree_not_repo(isolated_config, tmp_path, monkeypatch):
    """row_prodtest_gate hashes the linked worktree (matches _finish_plan)."""
    from workagent import cli_cleanup as cc
    from workagent import store
    wt = tmp_path / "wt"; wt.mkdir()
    repo = tmp_path / "repo"; repo.mkdir()
    entry = {"issue": "IPG-9", "worktree": str(wt), "branch": "feat/9",
             "repo": str(repo)}
    monkeypatch.setattr(store, "get_prodtest",
                        lambda key: {"plan_diff_hash": "wt-hash",
                                     "plan_path": "p.yaml"})
    seen: list[str] = []
    monkeypatch.setattr(cc, "current_prodtest_diff",
                        lambda d, default: seen.append(d) or "wt-hash")
    monkeypatch.setattr(cc, "run_validate",
                        lambda plan, manifest: {"valid": True, "errors": [],
                                                "warnings": []})
    assert cc.row_prodtest_gate("k", dict(entry)) is None
    assert seen == [str(wt)], seen


def test_review_checklist_block_appended(monkeypatch):
    import workagent.cli_review as rv
    monkeypatch.setattr(rv, "_mr_touches_prodtest_plan", lambda url, cwd: True)
    assert rv._mr_touches_prodtest_plan("x", "y") is True
    block = rv._prodtest_checklist_block()
    assert ".workagent/prodtest/plans/" in block
    assert "stale diff" in block


def test_review_no_checklist_when_no_plan(monkeypatch):
    import workagent.cli_review as rv
    monkeypatch.setattr(rv, "_mr_touches_prodtest_plan", lambda url, cwd: False)
    assert rv._mr_touches_prodtest_plan("x", "y") is False


def test_release_post_release_records_pending(isolated_config, monkeypatch):
    import workagent.cli_release as cr
    from workagent import store
    calls = []
    monkeypatch.setattr(store, "upsert_prodtest",
                        lambda key, **f: calls.append((key, f)) or {"issue_key": key})
    cr._prodtest_post_release("p", "rel", "sha123", ["IPG-1: thing"], ["IPG-1"])
    assert calls and calls[0][0] == "IPG-1"
    assert calls[0][1]["release_sha"] == "sha123"
    assert calls[0][1]["status"] == "pending_prodtest"
    assert calls[0][1]["released_at"]