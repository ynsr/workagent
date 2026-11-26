from datetime import datetime, timedelta, timezone

from workagent import cli, cli_status, store, store_sqlite as sq, swr


def test_open_pr_becomes_stale_after_10m_while_merged_stays_fresh(isolated_config):
    sq.init_db(sq.db_path())
    old_20m = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    open_entry = {"pr": {"state": "open", "number": 1}, "branch_tip": "a", "base_tip": "b", "checked_at": old_20m}
    merged_entry = {"pr": {"state": "merged", "number": 2}, "branch_tip": "a", "base_tip": "b", "checked_at": old_20m}
    assert cli_status._pr_freshness(open_entry, "a", "b") == "stale"
    assert cli_status._pr_freshness(merged_entry, "a", "b") == "fresh"


def test_query_pr_error_fallback_does_not_bump_checked_at(isolated_config, monkeypatch, tmp_path):
    sq.init_db(sq.db_path())
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    repo = str(tmp_path)
    store.cache_pr_status("feat/x", {"state": "open", "number": 9}, branch_tip="t1", base_tip="b1", repo=repo)
    with sq.connect(sq.db_path()) as conn:
        conn.execute("UPDATE pr_cache SET checked_at = ?", (old_ts,))
    monkeypatch.setattr(cli_status, "_query_pr", lambda *a, **kw: (_ for _ in ()).throw(cli_status.HarnessError("offline")))
    monkeypatch.setattr(cli_status, "_repo_default_branch", lambda _r: "main")
    monkeypatch.setattr(cli, "_git_tip", lambda _wt, ref: "t1" if ref in ("feat/x", "HEAD") else "b1")
    entry = {"repo": repo, "branch": "feat/x", "worktree": repo}
    cli_status._pr_cells(entry, refresh_pr=True)
    hit = store.lookup_pr_cache("feat/x", repo)
    assert hit is not None
    assert hit["checked_at"] == old_ts


def test_gone_worktree_ci_cache_hits_when_branch_tip_is_none(isolated_config):
    sq.init_db(sq.db_path())
    store.cache_ci_status("gone-br", "success", sha="", repo="/r")
    hit = store.lookup_pr_cache("gone-br", "/r")
    assert cli_status._ci_freshness(hit, None) == "fresh"


def test_stale_open_pr_triggers_swr_background_in_serve(isolated_config, monkeypatch, tmp_path):
    sq.init_db(sq.db_path())
    old_20m = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    repo = str(tmp_path)
    store.cache_pr_status(
        "feat/swr",
        {"state": "open", "number": 5},
        tool="gh",
        base_branch="main",
        branch_tip="t1",
        base_tip="b1",
        behind=0,
        ahead=1,
        repo=repo,
    )
    with sq.connect(sq.db_path()) as conn:
        conn.execute("UPDATE pr_cache SET checked_at = ?", (old_20m,))
    kicked: list[str] = []
    monkeypatch.setattr(
        swr,
        "serve_stale_or_refresh",
        lambda stale, is_fresh, refresh, key, **kw: (kicked.append(key), (stale, True))[1],
    )
    monkeypatch.setattr(cli, "_git_tip", lambda _wt, ref: "t1" if ref in ("feat/swr", "HEAD") else "b1")
    entry = {"repo": repo, "branch": "feat/swr", "worktree": repo}
    cells = cli_status._pr_cells(entry, refresh_pr=False)
    assert cells["pr_data"]["number"] == 5
    assert len(kicked) == 1
    assert kicked[0].startswith("pr:pr:")
