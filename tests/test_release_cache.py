from workagent import refs, refs_ci, repos, repos_release, store, store_sqlite as sq, swr


def test_fresh_release_cache_does_not_trigger_background_git_fetch(isolated_config, monkeypatch, tmp_path):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_release_diff_cache(
        db,
        "demo",
        3,
        default_branch="main",
        release_branch="release",
        ci_json='{"ci":"success","ci_url":"https://ci/1"}',
        ci_fetched_at="2099-01-01T00:00:00+00:00",
    )
    kicked: list[str] = []
    monkeypatch.setattr(
        swr,
        "serve_stale_or_refresh",
        lambda *a, **kw: (kicked.append(kw.get("key") or a[3]), (a[0], False))[1],
    )
    monkeypatch.setattr(
        store,
        "load_repos",
        lambda: {"demo": {"path": str(tmp_path), "default_branch": "main", "release_branch": "release"}},
    )
    monkeypatch.setattr(repos_release, "_behind_count", lambda *a, **kw: 0)
    monkeypatch.setattr(refs_ci, "fetch_branch_ci", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("live CI fetch")))
    swr.set_background_enabled(True)
    res = repos_release.release_status("demo", background=True)
    assert res["diff_count"] == 3
    assert res["ci"] == "success"
    assert res["ci_url"] == "https://ci/1"
    assert kicked == []


def test_branch_change_invalidates_release_diff_cache(isolated_config, monkeypatch, tmp_path):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_release_diff_cache(
        db, "demo", 9, default_branch="main", release_branch="old-release"
    )
    monkeypatch.setattr(
        store,
        "load_repos",
        lambda: {"demo": {"path": str(tmp_path), "default_branch": "main", "release_branch": "new-release"}},
    )
    monkeypatch.setattr(repos_release, "release_diff_count", lambda *a, **kw: 2)
    monkeypatch.setattr(repos_release, "_behind_count", lambda *a, **kw: 0)
    monkeypatch.setattr(refs_ci, "fetch_branch_ci", lambda *a, **kw: (None, ""))
    res = repos_release.release_status("demo", background=False)
    assert res["diff_count"] == 2


def test_force_offline_falls_back_to_cached_count_and_preserves_fetched_at(isolated_config, monkeypatch, tmp_path):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_release_diff_cache(db, "demo", 4, default_branch="main", release_branch="release")
    _, orig_ts = sq.get_release_diff_cache(db, "demo")
    monkeypatch.setattr(
        store,
        "load_repos",
        lambda: {"demo": {"path": str(tmp_path), "default_branch": "main", "release_branch": "release"}},
    )
    monkeypatch.setattr(
        repos_release,
        "release_diff_count",
        lambda *a, **kw: (_ for _ in ()).throw(repos_release.HarnessError("offline")),
    )
    monkeypatch.setattr(refs_ci, "fetch_branch_ci", lambda *a, **kw: (None, ""))
    res = repos_release.release_status("demo", force=True)
    assert res["diff_count"] == 4
    assert res["stale"] is True
    assert res["fetched_at"] == orig_ts


def test_force_ci_soft_fail_preserves_cached_branch_ci(isolated_config, monkeypatch, tmp_path):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_release_diff_cache(
        db,
        "demo",
        2,
        default_branch="main",
        release_branch="release",
        ci_json='{"ci":"success","ci_url":"https://ci/42"}',
        ci_fetched_at="2026-01-01T00:00:00+00:00",
    )
    monkeypatch.setattr(
        store,
        "load_repos",
        lambda: {"demo": {"path": str(tmp_path), "default_branch": "main", "release_branch": "release"}},
    )
    monkeypatch.setattr(repos_release, "release_diff_count", lambda *a, **kw: 2)
    monkeypatch.setattr(repos_release, "_behind_count", lambda *a, **kw: 0)
    monkeypatch.setattr(refs_ci, "fetch_branch_ci", lambda *a, **kw: (None, ""))
    res = repos_release.release_status("demo", force=True)
    assert res["ci"] == "success"
    assert res["ci_url"] == "https://ci/42"
    entry = sq.get_release_diff_cache_entry(db, "demo")
    assert entry is not None and "success" in entry["ci_json"]


def test_release_branch_change_clears_stale_ci_json(isolated_config):
    db = sq.db_path()
    sq.init_db(db)
    sq.set_release_diff_cache(
        db,
        "demo",
        2,
        default_branch="main",
        release_branch="release-v1",
        ci_json='{"ci":"failure","ci_url":"https://ci/old"}',
        ci_fetched_at="2099-01-01T00:00:00+00:00",
    )
    sq.set_release_diff_cache(db, "demo", 1, default_branch="main", release_branch="release-v2")
    entry = sq.get_release_diff_cache_entry(db, "demo")
    assert entry is not None
    assert entry["release_branch"] == "release-v2"
    assert entry["ci_json"] == ""
    assert entry["ci_fetched_at"] == ""
