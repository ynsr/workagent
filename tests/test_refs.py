"""Ref parsing round-trips."""

from __future__ import annotations

import json

from workagent import refs
from workagent.errors import HarnessError

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


def test_shorthand_accepts_tracker_prefixed_repo():
    """Launch sends tracker-qualified refs like github:o/r#22; the repo
    field must not keep the tracker prefix (doubled github:github:o/r)."""
    p = refs.parse_ref("github:ynsr/harness#20")
    assert p["repo"] == "ynsr/harness" and p["number"] == "20"


def test_jira_prefixed_key_round_trip():
    """issue_key() emits jira:KEY; parse_ref must accept it back (issue #16)."""
    p = refs.parse_ref("jira:IPG-984")
    assert p["tool"] == "jira-cli" and p["number"] == "IPG-984"
    assert refs.issue_key(p) == "jira:IPG-984"


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
    """glab branch query must include merged/closed MRs, not just opened (#31)."""
    seen: list[list[str]] = []
    glab_json = json.dumps([
        {"iid": 1701, "state": "opened", "title": "X",
         "created_at": "2026-09-15T03:01:59Z", "author": {"username": "younes"},
         "web_url": "https://git.jibit.cloud/g/r/-/merge_requests/1701",
         "target_branch": "develop"},
        {"iid": 1720, "state": "merged", "title": "Y",
         "created_at": "2026-09-20T03:01:59Z", "author": {"username": "younes"},
         "web_url": "https://git.jibit.cloud/g/r/-/merge_requests/1720",
         "target_branch": "develop"},
    ])
    def fake_run(*a, **k):
        seen.append(list(a))
        return glab_json
    monkeypatch.setattr(refs, "run_cmd", fake_run)
    prs = refs.fetch_pr_list_for_branch("glab", "feat/x")
    assert "--all" in seen[0]
    assert prs == [{"number": 1701, "state": "open", "title": "X",
                    "author": "younes", "created_at": "2026-09-15T03:01:59Z",
                    "url": "https://git.jibit.cloud/g/r/-/merge_requests/1701",
                    "target_branch": "develop"},
                   {"number": 1720, "state": "merged", "title": "Y",
                    "author": "younes", "created_at": "2026-09-20T03:01:59Z",
                    "url": "https://git.jibit.cloud/g/r/-/merge_requests/1720",
                    "target_branch": "develop"}]


def test_latest_pr_picks_newest():
    prs = [{"number": 1, "created_at": "2026-09-01"},
           {"number": 2, "created_at": "2026-09-15"}]
    assert refs.latest_pr(prs)["number"] == 2
    assert refs.latest_pr([]) is None


def test_pick_branch_pr_prefers_latest_open():
    prs = [{"number": 1, "state": "merged", "created_at": "2026-09-01"},
           {"number": 2, "state": "closed", "created_at": "2026-09-20"},
           {"number": 3, "state": "open", "created_at": "2026-09-05"},
           {"number": 4, "state": "open", "created_at": "2026-09-15"}]
    assert refs.pick_branch_pr(prs)["number"] == 4
    # Newest closed PR does NOT win while an older open PR exists.
    assert refs.pick_branch_pr([
        {"number": 1, "state": "open", "created_at": "2026-09-01"},
        {"number": 2, "state": "closed", "created_at": "2026-09-20"},
    ])["number"] == 1
    # No open PR: latest overall (even merged) identifies the branch fate.
    assert refs.pick_branch_pr([
        {"number": 1, "state": "merged", "created_at": "2026-09-01"},
        {"number": 2, "state": "merged", "created_at": "2026-09-15"},
    ])["number"] == 2
    assert refs.pick_branch_pr([]) is None


def test_fetch_pr_merge_state_github(monkeypatch):
    import workagent.refs as r
    calls = []
    def fake(*a, **k):
        calls.append(a)
        return '{"state": "OPEN", "mergeable": "CONFLICTING", "mergeStateStatus": "DIRTY"}'
    monkeypatch.setattr(r, "run_cmd", fake)
    out = refs.fetch_pr_merge_state("https://github.com/o/r/pull/9")
    assert out == {"state": "OPEN", "mergeable": "CONFLICTING",
                   "merge_state": "DIRTY"}
    assert "mergeStateStatus" in str(calls)


def test_fetch_pr_merge_state_404_surfaces(monkeypatch):
    import workagent.refs as r
    from workagent.errors import HarnessError
    import pytest
    def fake(*a, **k):
        raise HarnessError("gh pr view failed: 404 Not Found")
    monkeypatch.setattr(r, "run_cmd", fake)
    with pytest.raises(HarnessError, match="404"):
        refs.fetch_pr_merge_state("https://github.com/o/r/pull/404")


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


def test_fetch_ci_gh_rollup(monkeypatch):
    calls = []

    def fake_run(cmd, *args, **kw):
        calls.append(args)
        return json.dumps({"statusCheckRollup": [
            {"conclusion": "SUCCESS"}, {"conclusion": "SUCCESS"}]})

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") == "success"
    assert calls == [("pr", "view", "https://github.com/o/r/pull/9",
                      "--json", "statusCheckRollup")]
    monkeypatch.setattr(
        refs, "run_cmd",
        lambda *a, **k: json.dumps({"statusCheckRollup": [
            {"conclusion": "FAILURE"}]}))
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") == "failure"


def test_fetch_ci_gh_rollup_precedence(monkeypatch):
    # any failure beats any running beats success
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps(
        {"statusCheckRollup": [
            {"conclusion": "SUCCESS"}, {"conclusion": "IN_PROGRESS"},
            {"conclusion": "ACTION_REQUIRED"}]}))
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") == "failure"
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps(
        {"statusCheckRollup": [
            {"conclusion": "SUCCESS"}, {"conclusion": "QUEUED"}]}))
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") == "running"
    # empty rollup -> not_started
    monkeypatch.setattr(refs, "run_cmd",
                        lambda *a, **k: json.dumps({"statusCheckRollup": []}))
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") == "not_started"


def test_fetch_ci_glab_pipeline(monkeypatch):
    calls = []

    def fake_run(*args, **kw):
        calls.append(args)
        return json.dumps([{"id": 1, "status": "failed"},
                           {"id": 2, "status": "success"}])

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci_status(
        "glab", "https://git.jibit.cloud/g/p/-/merge_requests/7", "/repo"
    ) == "success"
    assert calls == [("glab", "api",
                      "projects/g%2Fp/merge_requests/7/pipelines",
                      "--hostname", "git.jibit.cloud")]


def test_fetch_ci_glab_hostname_retry(monkeypatch):
    calls = []

    def fake_run(cmd, *args, **kw):
        calls.append(args)
        if "--hostname" in args:
            raise HarnessError("unknown flag")
        return json.dumps([{"id": 5, "status": "running"}])

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci_status(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo"
    ) == "running"
    assert len(calls) == 2


def test_fetch_ci_glab_status_map(monkeypatch):
    for raw, want in (("canceled", "failure"), ("skipped", "not_started"),
                      ("pending", "running")):
        def fake_run(cmd, *args, r=raw, **kw):
            return json.dumps([{"id": 1, "status": r}])
        monkeypatch.setattr(refs, "run_cmd", fake_run)
        assert refs.fetch_ci_status(
            "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo"
        ) == want


def test_ci_fetch_failure_is_soft(monkeypatch):
    def boom(*a, **k):
        raise HarnessError("timeout")

    monkeypatch.setattr(refs, "run_cmd", boom)
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") is None
    assert refs.fetch_ci_status(
        "glab", "https://git.jibit.cloud/g/p/-/merge_requests/7", "/repo"
    ) is None
    # unrecognized URL also degrades to None, no raise
    assert refs.fetch_ci_status(
        "glab", "https://example.com/bogus", "/repo") is None


def test_fetch_ci_glab_empty_list(monkeypatch):
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([]))
    assert refs.fetch_ci_status(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo"
    ) == "not_started"

def test_fetch_pr_comment_stats_gh(monkeypatch):
    """Issue #28: gh counts # Code Review comments + thread resolution.

    Issue #32: plain bot comments without a `Status: RESOLVED` second line
    count as unresolved (no native resolution state on GitHub comments).
    """
    def fake_run(*a, **k):
        if "graphql" in a:
            return json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"nodes": [{"isResolved": True},
                                            {"isResolved": False}]}}}}})
        return json.dumps({"comments": [
            {"body": "# Code Review: looks good\nStatus: RESOLVED"},
            {"body": "  # Code Review follow-up"},
            {"body": "just a comment"}]})
    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_pr_comment_stats(
        "gh", "https://github.com/o/r/pull/9", "/repo") == {
            "reviews": 2, "unresolved": 2, "resolved": 2}


def test_gh_bot_comment_resolved_marker():
    """Issue #32: only an exact `Status: RESOLVED` second line resolves."""
    assert refs._gh_bot_comment_resolved("# Code Review: ok\nStatus: RESOLVED")
    assert refs._gh_bot_comment_resolved("# Code Review: ok\n\n  Status: RESOLVED  \nbody text")
    assert not refs._gh_bot_comment_resolved("# Code Review: open finding")
    assert not refs._gh_bot_comment_resolved("# Code Review: ok\nbody\nStatus: RESOLVED")
    assert not refs._gh_bot_comment_resolved("# Code Review: only header, no second line")
    assert not refs._gh_bot_comment_resolved("")


def test_fetch_pr_comment_stats_glab(monkeypatch):
    """Issue #28: glab counts # Code Review notes + discussion resolution.

    System/activity notes ("added N commits", …) are individual
    non-resolvable discussions and must not count as unresolved.
    """
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([
        {"resolved": True, "notes": [
            {"body": "# Code Review: done", "resolved": True,
             "resolvable": True}]},
        {"notes": [{"body": "fix this", "resolved": False,
                    "resolvable": True}]},
        {"individual_note": True, "notes": [
            {"system": True, "body": "added 2 commits", "resolvable": False}]},
    ]))
    assert refs.fetch_pr_comment_stats(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo") == {
            "reviews": 1, "unresolved": 1, "resolved": 1}


def test_pr_comment_stats_failure_is_soft(monkeypatch):
    def boom(*a, **k):
        raise HarnessError("timeout")
    monkeypatch.setattr(refs, "run_cmd", boom)
    assert refs.fetch_pr_comment_stats(
        "gh", "https://github.com/o/r/pull/9", "/repo") is None
    assert refs.fetch_pr_comment_stats(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo") is None


# ── fetch_open_prs / pr_key ───────────────────────────────────────────


def test_fetch_open_prs_gh(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append((a, k))
        return json.dumps([
            {"number": 33, "title": "T1", "headRefName": "feat/x",
             "updatedAt": "2026-09-20T10:00:00Z",
             "url": "https://github.com/o/r/pull/33"}])

    monkeypatch.setattr(refs, "run_cmd", fake)
    prs = refs.fetch_open_prs("gh", "/repo")
    assert prs == [{"number": 33, "title": "T1", "branch": "feat/x",
                    "updated": "2026-09-20T10:00:00Z",
                    "url": "https://github.com/o/r/pull/33", "state": "OPEN"}]
    assert calls[0][0][:5] == ("gh", "pr", "list", "--state", "open")
    assert calls[0][1].get("cwd") == "/repo"


def test_fetch_open_prs_glab_state_flag(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(a)
        return json.dumps([{"iid": 1706, "title": "T2",
                            "source_branch": "feat/y",
                            "updated_at": "2026-09-21T00:06:18.549Z",
                            "state": "opened",
                            "web_url": "https://git.x/g/r/-/merge_requests/1706"}])

    monkeypatch.setattr(refs, "run_cmd", fake)
    prs = refs.fetch_open_prs("glab", "/repo")
    assert prs[0]["number"] == 1706 and prs[0]["branch"] == "feat/y"
    assert prs[0]["state"] == "OPEN" and prs[0]["updated"].endswith("Z")
    assert prs[0]["url"].endswith("/-/merge_requests/1706")
    assert calls[0][:4] == ("glab", "mr", "list", "--state")


def test_fetch_open_prs_glab_falls_back_without_state(monkeypatch):
    """glab ≥1.x `mr list` rejects --state and defaults to open MRs."""
    calls = []

    def fake(*a, **k):
        calls.append(a)
        if "--state" in a:
            raise HarnessError("Unknown flag: --state")
        return json.dumps([])

    monkeypatch.setattr(refs, "run_cmd", fake)
    assert refs.fetch_open_prs("glab", "/repo") == []
    assert len(calls) == 2 and "--state" not in calls[1]


def test_fetch_open_prs_unknown_tool(monkeypatch):
    with pytest.raises(HarnessError):
        refs.fetch_open_prs("bzr", "/repo")


def test_pr_key():
    assert refs.pr_key("https://github.com/o/r/pull/33") == "github:o/r#33"
    assert refs.pr_key(
        "https://git.jibit.cloud/server/projectx/-/merge_requests/1706"
    ) == "gitlab:git.jibit.cloud/server/projectx#1706"
    assert refs.pr_key("https://example.com/whatever") == ""
    assert refs.pr_key("") == ""
