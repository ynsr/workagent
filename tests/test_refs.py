"""Ref parsing round-trips."""

from __future__ import annotations

import json

from harness import refs
from harness.errors import HarnessError

import pytest


def test_github_issue_url():
    p = refs.parse_ref("https://github.com/OWNER/REPO/issues/22")
    assert p == {"kind": "issue", "tool": "gh", "repo": "OWNER/REPO",
                 "number": "22", "url": "https://github.com/OWNER/REPO/issues/22"}


def test_github_pr_url():
    p = refs.parse_ref("https://github.com/o/r/pull/33")
    assert p["kind"] == "pr" and p["tool"] == "gh" and p["number"] == "33"


def test_gitlab_issue_and_mr():
    assert refs.parse_ref("https://git.example.com/g/r/-/issues/430")["tool"] == "glab"
    m = refs.parse_ref("https://git.example.com/g/r/-/merge_requests/55")
    assert m["kind"] == "mr" and m["number"] == "55"


def test_shorthand_and_bare_number():
    assert refs.parse_ref("o/r#22")["repo"] == "o/r"
    assert refs.parse_ref("22")["kind"] == "issue_or_pr"


def test_unknown_rejected():
    with pytest.raises(HarnessError):
        refs.parse_ref("not a ref at all !!!")


def test_issue_key_stable():
    p = refs.parse_ref("https://github.com/o/r/issues/22")
    assert refs.issue_key(p) == "github:o/r#22"


def test_fetch_pr_list_gh(monkeypatch):
    gh_json = json.dumps([
        {"number": 12, "state": "OPEN", "title": "B", "createdAt": "2026-09-02",
         "author": {"login": "bob"}, "url": "https://github.com/o/r/pull/12",
         "baseRefName": "main"},
        {"number": 11, "state": "MERGED", "title": "A", "createdAt": "2026-09-01",
         "author": {"login": "bob"}, "url": "https://github.com/o/r/pull/11",
         "baseRefName": "main"},
    ])
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: gh_json)
    prs = refs.fetch_pr_list_for_branch("gh", "feat/x")
    assert prs[0] == {"number": 12, "state": "open", "title": "B",
                      "author": "bob", "created_at": "2026-09-02",
                      "url": "https://github.com/o/r/pull/12",
                      "target_branch": "main"}
    assert prs[1]["state"] == "merged"


def test_fetch_pr_list_glab(monkeypatch):
    glab_json = json.dumps([
        {"iid": 1701, "state": "opened", "title": "X",
         "created_at": "2026-09-15T03:01:59Z", "author": {"username": "younes"},
         "web_url": "https://git.jibit.cloud/g/r/-/merge_requests/1701",
         "target_branch": "develop"},
    ])
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: glab_json)
    prs = refs.fetch_pr_list_for_branch("glab", "feat/x")
    assert prs == [{"number": 1701, "state": "open", "title": "X",
                    "author": "younes", "created_at": "2026-09-15T03:01:59Z",
                    "url": "https://git.jibit.cloud/g/r/-/merge_requests/1701",
                    "target_branch": "develop"}]


def test_latest_pr_picks_newest():
    prs = [{"number": 1, "created_at": "2026-09-01"},
           {"number": 2, "created_at": "2026-09-15"}]
    assert refs.latest_pr(prs)["number"] == 2
    assert refs.latest_pr([]) is None


def test_issue_url_stored_http_wins():
    assert refs.issue_url("jira:IPG-1", "https://x/browse/IPG-1") == "https://x/browse/IPG-1"


def test_issue_url_jira_from_site_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"url": "https://jira.example.com/"}))
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (cfg, tmp_path / "missing.json"))
    assert refs.issue_url("jira:IPG-959") == "https://jira.example.com/browse/IPG-959"


def test_issue_url_jira_site_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (tmp_path / "missing.json",))
    assert refs.issue_url("jira:IPG-959") is None


def test_issue_url_jira_invalid_config_skipped(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"url": "https://jira.example.com"}))
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (bad, good))
    assert refs.issue_url("jira:IPG-959") == "https://jira.example.com/browse/IPG-959"


def test_issue_url_github_and_gitlab():
    assert refs.issue_url("github:owner/repo#22") == "https://github.com/owner/repo/issues/22"
    assert refs.issue_url("github:local#22") is None
    assert refs.issue_url("gitlab:server/projectx#430") is None
