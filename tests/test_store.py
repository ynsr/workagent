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
