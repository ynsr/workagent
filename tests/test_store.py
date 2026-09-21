"""Store round-trips under an isolated config dir."""

import os
import subprocess
from unittest import mock

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


def test_harness_run_roundtrip(isolated_config):
    store.record_harness_run("jira:IPG-1", "omp", "/tmp/wt-a")
    rec = store.active_harness("jira:IPG-1")
    assert rec is not None and rec["harness"] == "omp" and rec["pid"] == os.getpid()
    store.clear_harness_run("jira:IPG-1")
    assert store.active_harness("jira:IPG-1") is None


def test_harness_run_dead_pid_swept(isolated_config):
    store.save_harnesses_raw({"jira:OLD": {"harness": "omp", "pid": _dead_pid(), "started_at": 0.0}})
    assert store.active_harness("jira:OLD") is None
    assert store.load_harnesses() == {}


def test_harness_run_live_foreign_pid(isolated_config):
    # A pid we cannot signal counts as alive (PermissionError branch).
    with mock.patch.object(store.os, "kill", side_effect=PermissionError):
        store.record_harness_run("jira:P", "omp", "")
        assert store.active_harness("jira:P") is not None


def _dead_pid() -> int:
    p = subprocess.Popen(["sleep", "0"])
    p.wait()
    return p.pid
