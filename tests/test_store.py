"""Store round-trips under an isolated config dir."""

from harness import store


def test_config_defaults(isolated_config):
    cfg = store.load_config()
    assert cfg["default_harness"] == "omp"
    assert cfg["repos"] == {} and cfg["trackers"] == {}


def test_record_and_lookup_link(isolated_config):
    store.record_link("github:o/r#22", {"branch": "feat/22-x", "worktree": "/tmp/wt"})
    entry = store.lookup_link("github:o/r#22")
    assert entry["branch"] == "feat/22-x"
    # Merge, don't clobber.
    store.record_link("github:o/r#22", {"pr_url": "https://example.com/pr/1"})
    entry = store.lookup_link("github:o/r#22")
    assert entry["worktree"] == "/tmp/wt" and "pr" in entry["pr_url"]


def test_missing_link_is_none(isolated_config):
    assert store.lookup_link("github:nope#1") is None


def test_pr_cache_roundtrip(isolated_config):
    pr = {"number": 123, "state": "open", "title": "T", "author": "a",
          "created_at": "2026-09-15", "url": "https://x/pr/123"}
    store.cache_pr_status("feat/x", pr)
    cached = store.get_cached_pr_status("feat/x")
    assert cached == pr
    raw = store.load_pr_cache()
    assert raw["feat/x"]["pr"] == pr and "checked_at" in raw["feat/x"]


def test_pr_cache_none_roundtrip(isolated_config):
    store.cache_pr_status("feat/y", None)
    assert store.get_cached_pr_status("feat/y") is None


def test_pr_cache_missing_branch(isolated_config):
    assert store.get_cached_pr_status("feat/nope") is None


def test_load_links_backfills_ref_key(isolated_config):
    store.save_links({"jira:IPG-1": {"branch": "feat/x", "worktree": "/tmp/wt"}})
    links = store.load_links()
    assert links["jira:IPG-1"]["ref_key"] == "jira:IPG-1"
