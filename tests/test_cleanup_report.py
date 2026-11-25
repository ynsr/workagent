"""Compact cleanup report tests across all cleanup commands, subcommands, and flags."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from workagent import cli, store, store_sqlite as sq
from tests.cli_helpers import _invoke


def _seed_worktree(tmp_path, isolated_config, key="jira:IPG-211",
                   branch="feat/211", pr_url="", with_run=True):
    db = sq.db_path()
    sq.init_db(db)
    repo = tmp_path / "repo"
    wt = tmp_path / f"wt-{branch.replace('/', '-')}"
    repo.mkdir(exist_ok=True)
    wt.mkdir(exist_ok=True)
    entry = {"issue": key.removeprefix("jira:"), "branch": branch,
             "repo": str(repo), "worktree": str(wt)}
    if pr_url:
        entry["pr_url"] = pr_url
    store.record_link(key, entry)
    transcript = None
    if with_run:
        rdir = isolated_config / "runs" / "omp" / branch.replace("/", "-") / "start"
        rdir.mkdir(parents=True, exist_ok=True)
        transcript = rdir / "t.jsonl"
        transcript.write_text("{}\n")
        sq.insert_run(db, "start", [key], 0, worktree_ref=key,
                      harness_name="omp", initiator_command="start",
                      file_path=str(transcript), session_type="start")
    return repo, wt, transcript


def test_cleanup_soft_delete_report_json_and_human(isolated_config, tmp_path, monkeypatch):
    _seed_worktree(tmp_path, isolated_config,
                   pr_url="https://github.com/o/r/pull/10", with_run=False)
    monkeypatch.setattr(cli, "_status_cells",
                        lambda *a, **k: {"pr_data": {"state": "OPEN"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, *a, **k: (url, {"state": "open"}, False))
    monkeypatch.setattr(cli, "_merge_pr", lambda *a, **k: True)

    r = _invoke("cleanup", "jira:IPG-211", "--yes")
    assert r.exit_code == 0, r.output
    assert "mode: soft-delete" in r.stdout
    assert "status: cleaned" in r.stdout
    assert "merged PR https://github.com/o/r/pull/10" in r.stdout
    assert "remote branch feat/211" in r.stdout
    assert "worktree row jira:IPG-211 (soft-deleted)" in r.stdout

    # Re-seed and check --json output shape
    _seed_worktree(tmp_path, isolated_config, key="jira:IPG-212",
                   branch="feat/212", pr_url="https://github.com/o/r/pull/11",
                   with_run=False)
    r_json = _invoke("cleanup", "jira:IPG-212", "--yes", "--json")
    assert r_json.exit_code == 0, r_json.output
    out = json.loads(r_json.stdout)
    assert out["mode"] == "soft-delete"
    assert out["status"] == "cleaned"
    assert out["remote_deleted"] is True
    assert "merged PR https://github.com/o/r/pull/11" in out["removed"]
    assert "remote branch feat/212" in out["removed"]
    assert "worktree row jira:IPG-212 (soft-deleted)" in out["removed"]


def test_cleanup_hard_delete_report_includes_cascades_and_transcripts(
    isolated_config, tmp_path, monkeypatch
):
    _repo, wt, transcript = _seed_worktree(tmp_path, isolated_config, with_run=True)
    monkeypatch.setattr(cli, "_status_cells", lambda *a, **k: {"pr_data": {}})
    monkeypatch.setattr(cli, "_resolve_branch_pr", lambda *a, **k: ("", None, False))
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_close_pr", lambda *a, **k: None)
    def _fake_cleanup(*a, **k):
        if wt.exists():
            wt.rmdir()
        return {"actions": ["removed worktree"]}

    monkeypatch.setattr(cli.gitwt, "cleanup_worktree", _fake_cleanup)
    monkeypatch.setattr(cli, "run_cmd", lambda *a, **k: "")

    r = _invoke("cleanup", "jira:IPG-211", "--hard-delete", "--yes", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["mode"] == "hard-delete"
    assert out["status"] == "cleaned"
    assert out["cascaded"]["runs"] == 1
    assert out["transcripts_removed"] == 1
    assert not transcript.exists()
    assert f"worktree dir {wt}" in out["removed"]
    assert "local branch feat/211" in out["removed"]
    assert "worktree row jira:IPG-211" in out["removed"]
    assert "1 run" in out["removed"]
    assert "1 transcript" in out["removed"]

    # If the worktree dir was already missing on disk, do not report it as removed
    _repo2, wt2, _ = _seed_worktree(tmp_path, isolated_config, key="jira:IPG-215",
                                    branch="feat/215", with_run=False)
    wt2.rmdir()
    r2 = _invoke("cleanup", "jira:IPG-215", "--hard-delete", "--yes", "--json")
    assert r2.exit_code == 0, r2.output
    out2 = json.loads(r2.stdout)
    assert not any(item.startswith("worktree dir ") for item in out2["removed"])


def test_cleanup_merge_only_and_dry_run_reports(isolated_config, tmp_path, monkeypatch):
    _seed_worktree(tmp_path, isolated_config,
                   pr_url="https://github.com/o/r/pull/20", with_run=False)
    r_dry = _invoke("cleanup", "jira:IPG-211", "--hard-delete", "--dry-run", "--json")
    assert r_dry.exit_code == 0, r_dry.output
    dry = json.loads(r_dry.stdout)
    assert dry["dry_run"] is True
    assert dry["mode"] == "hard-delete"
    assert any("worktree dir" in x for x in dry["would_remove"])
    assert "local branch feat/211" in dry["would_remove"]
    assert "worktree row jira:IPG-211" in dry["would_remove"]

    r_mo_dry = _invoke("cleanup", "jira:IPG-211", "--merge-only", "--dry-run", "--json")
    assert r_mo_dry.exit_code == 0, r_mo_dry.output
    mo_dry = json.loads(r_mo_dry.stdout)
    assert mo_dry["mode"] == "merge-only"
    assert mo_dry["would_remove"] == ["merged PR https://github.com/o/r/pull/20"]
    assert mo_dry["kept"] == ["worktree", "local branch", "worktree row"]

    monkeypatch.setattr(cli, "_status_cells", lambda *a, **k: {"pr_data": {}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda *a, **k: ("https://github.com/o/r/pull/20",
                                         {"state": "open"}, False))
    monkeypatch.setattr(cli, "_merge_pr", lambda *a, **k: False)
    r_mo = _invoke("cleanup", "jira:IPG-211", "--merge-only")
    assert r_mo.exit_code == 0, r_mo.output
    assert "mode: merge-only" in r_mo.stdout
    assert "status: merged" in r_mo.stdout
    assert "kept: worktree, local branch, worktree row" in r_mo.stdout


def test_cleanup_merged_report_columns_and_json(isolated_config, tmp_path, monkeypatch):
    _seed_worktree(tmp_path, isolated_config, key="jira:IPG-1",
                   branch="feat/1", pr_url="https://github.com/o/r/pull/1",
                   with_run=False)
    _seed_worktree(tmp_path, isolated_config, key="jira:IPG-2",
                   branch="feat/2", pr_url="https://github.com/o/r/pull/2",
                   with_run=False)
    monkeypatch.setattr(
        cli, "_status_cells",
        lambda k, e, refresh_pr=False: {"pr_data": {"state": "MERGED" if k == "jira:IPG-1" else "OPEN"}})
    monkeypatch.setattr(cli, "_resolve_branch_pr",
                        lambda branch, url, *a, **k: (url, {"state": "merged"}, False))
    monkeypatch.setattr(cli, "_close_issue", lambda *a, **k: None)
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda *a, **k: {"head_ref": "feat/1"})
    monkeypatch.setattr(cli, "run_cmd", lambda *a, **k: "")
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda *a, **k: True)

    r_json = _invoke("cleanup", "--merged", "--yes", "--json")
    assert r_json.exit_code == 0, r_json.output
    rows = {row["key"]: row for row in json.loads(r_json.stdout)["results"]}
    assert "remote branch feat/1" in rows["jira:IPG-1"]["removed"]
    assert "worktree row jira:IPG-1 (soft-deleted)" in rows["jira:IPG-1"]["removed"]
    assert rows["jira:IPG-2"]["removed"] == []


def test_link_remove_gc_and_destroy_deleted_reports(isolated_config, tmp_path, monkeypatch):
    _repo, _wt, transcript = _seed_worktree(tmp_path, isolated_config, with_run=True)
    # link remove --hard-delete
    r_rem = _invoke("link", "remove", "jira:IPG-211", "--hard-delete", "--json")
    assert r_rem.exit_code == 0, r_rem.output
    rem = json.loads(r_rem.stdout)
    assert rem["mode"] == "hard-delete"
    assert rem["cascaded"]["runs"] == 1
    assert rem["transcripts_removed"] == 1
    assert not transcript.exists()

    # link destroy-deleted --json & human table
    _seed_worktree(tmp_path, isolated_config, key="jira:IPG-213",
                   branch="feat/213", with_run=True)
    store.soft_delete_worktree("jira:IPG-213")
    monkeypatch.setattr("workagent.cli_cleanup_gc._teardown_local",
                        lambda *a, **k: {"worktree_removed": True,
                                         "local_branch_deleted": True,
                                         "worktree_path": "/tmp/wt-213",
                                         "branch": "feat/213", "skipped": ""})
    r_dest = _invoke("link", "destroy-deleted", "jira:IPG-213", "--json")
    assert r_dest.exit_code == 0, r_dest.output
    dest = json.loads(r_dest.stdout)["results"][0]
    assert dest["status"] == "destroyed"
    assert "worktree dir /tmp/wt-213" in dest["removed"]
    assert "local branch feat/213" in dest["removed"]
    assert "1 run" in dest["removed"]
    assert "1 transcript" in dest["removed"]

    # link gc --json (expired > 7 days)
    _seed_worktree(tmp_path, isolated_config, key="jira:IPG-214",
                   branch="feat/214", with_run=True)
    store.soft_delete_worktree("jira:IPG-214")
    db = sq.db_path()
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with sq.connect(db) as conn:
        conn.execute("UPDATE worktrees SET deleted_at = ? WHERE ref_key = ?",
                     (old, "jira:IPG-214"))
    r_gc = _invoke("link", "gc", "--json")
    assert r_gc.exit_code == 0, r_gc.output
    gc_row = json.loads(r_gc.stdout)["results"][0]
    assert gc_row["status"] == "purged"
    assert "worktree row jira:IPG-214" in gc_row["removed"]
    assert "1 run" in gc_row["removed"]
    assert "1 transcript" in gc_row["removed"]

