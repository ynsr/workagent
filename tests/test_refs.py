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
    assert p["tool"] == "jira" and p["number"] == "IPG-984"


def test_unknown_rejected():
    with pytest.raises(HarnessError):
        refs.parse_ref("not a ref at all !!!")


def test_issue_key_stable():
    p = refs.parse_ref("https://github.com/o/r/issues/22")
    assert refs.issue_key(p) == "github:o/r#22"


def test_issue_key_jira_repo_suffix():
    p = refs.parse_ref("IPG-1011")
    assert refs.issue_key(p) == "jira:IPG-1011"
    assert refs.issue_key(p, "saman-ipg") == "jira:IPG-1011@saman-ipg"
    assert refs.issue_key(p, "/home/x/My Repo!") == "jira:IPG-1011@my-repo"
    # GitHub keys already embed the repo — suffix ignored.
    g = refs.parse_ref("https://github.com/o/r/issues/22")
    assert refs.issue_key(g, "other") == "github:o/r#22"


def test_split_issue_key_round_trip():
    assert refs.split_issue_key("jira:IPG-1011@saman-ipg") == ("jira:IPG-1011", "saman-ipg")
    assert refs.split_issue_key("jira:IPG-1011") == ("jira:IPG-1011", None)
    assert refs.split_issue_key("github:o/r#22") == ("github:o/r#22", None)


def test_matching_issue_keys_dual_resolve():
    links = {"jira:IPG-1011@saman-ipg": {}, "jira:IPG-999": {}}
    assert refs.matching_issue_keys("jira:IPG-1011@saman-ipg", links) == ["jira:IPG-1011@saman-ipg"]
    assert refs.matching_issue_keys("jira:IPG-1011", links) == ["jira:IPG-1011@saman-ipg"]
    assert refs.matching_issue_keys("jira:IPG-999@other", links) == ["jira:IPG-999"]
    assert refs.matching_issue_keys("jira:IPG-404", links) == []


def test_issue_url_strips_repo_suffix(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text("server: https://jira.example.com\n")
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (cfg,))
    assert refs.issue_url("jira:IPG-1011@saman-ipg") == "https://jira.example.com/browse/IPG-1011"


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
                   "merge_state": "DIRTY", "squash": False}
    assert "mergeStateStatus" in str(calls)
    assert "autoMergeRequest" in str(calls)


def test_fetch_pr_merge_state_github_squash_auto_merge(monkeypatch):
    import workagent.refs as r
    monkeypatch.setattr(
        r, "run_cmd",
        lambda *a, **k: '{"state": "OPEN", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "autoMergeRequest": {"mergeMethod": "SQUASH"}}',
    )
    out = refs.fetch_pr_merge_state("https://github.com/o/r/pull/10")
    assert out["squash"] is True


def test_fetch_pr_merge_state_404_surfaces(monkeypatch):
    import workagent.refs as r
    from workagent.errors import HarnessError
    import pytest
    def fake(*a, **k):
        raise HarnessError("gh pr view failed: 404 Not Found")
    monkeypatch.setattr(r, "run_cmd", fake)
    with pytest.raises(HarnessError, match="404"):
        refs.fetch_pr_merge_state("https://github.com/o/r/pull/404")

def test_pr_url_for_number_not_a_pr_hint(monkeypatch):
    """gh 'Could not resolve to a PullRequest' → 'not a pull request' + start hint."""
    import workagent.refs as r
    def fake(*a, **k):
        raise HarnessError("gh pr view 22 failed: GraphQL: Could not resolve to a PullRequest")
    monkeypatch.setattr(r, "run_cmd", fake)
    with pytest.raises(HarnessError, match="is not a pull request"):
        refs.pr_url_for_number("o/r", "22")


def test_pr_url_for_number_transient_error_raw(monkeypatch):
    """Auth/rate-limit failures surface raw, not as 'not a pull request'."""
    import workagent.refs as r
    def fake(*a, **k):
        raise HarnessError("gh pr view 22 failed: HTTP 403 rate limit exceeded")
    monkeypatch.setattr(r, "run_cmd", fake)
    with pytest.raises(HarnessError, match="cannot resolve"):
        refs.pr_url_for_number("o/r", "22")


def test_fetch_pr_merge_state_gitlab_passes_json_flag(monkeypatch):
    """glab mr view needs -F json: without it the parse fails and every
    caller degrades to fresh=None (blind merge, no blocked-state gate)."""
    import workagent.refs as r
    calls = []
    def fake(*a, **k):
        calls.append(a)
        return ('{"state": "opened", "detailed_merge_status": '
                '"discussions_not_resolved", "has_conflicts": false}')
    monkeypatch.setattr(r, "run_cmd", fake)
    out = refs.fetch_pr_merge_state(
        "https://git.jibit.cloud/server/projectx/-/merge_requests/1")
    assert out == {"state": "opened",
                   "mergeable": "discussions_not_resolved",
                   "merge_state": "",
                   "squash": False}
    assert "-F" in calls[0] and "json" in calls[0]


def test_issue_url_stored_http_wins():
    assert refs.issue_url("jira:IPG-1", "https://x/browse/IPG-1") == "https://x/browse/IPG-1"


def test_issue_url_jira_from_site_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yml"
    cfg.write_text("server: https://jira.example.com/\n")
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (cfg, tmp_path / "missing.yml"))
    assert refs.issue_url("jira:IPG-959") == "https://jira.example.com/browse/IPG-959"


def test_issue_url_jira_legacy_config_fallback(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"url": "https://jira.example.com/"}))
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (tmp_path / "missing.yml", cfg))
    assert refs.issue_url("jira:IPG-959") == "https://jira.example.com/browse/IPG-959"


def test_issue_url_jira_site_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(refs, "_JIRA_CONFIGS", (tmp_path / "missing.yml",))
    assert refs.issue_url("jira:IPG-959") is None


def test_issue_url_jira_invalid_config_skipped(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yml"
    bad.write_text("server: \n")
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
            {"conclusion": "SUCCESS", "detailsUrl": "https://github.com/o/r/actions/runs/1"},
            {"conclusion": "SUCCESS", "detailsUrl": "https://github.com/o/r/actions/runs/2"}]})

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci(
        "gh", "https://github.com/o/r/pull/9", "/repo") == (
            "success", "https://github.com/o/r/actions/runs/1")
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") == "success"
    assert calls == [("pr", "view", "https://github.com/o/r/pull/9",
                      "--json", "statusCheckRollup")] * 2
    monkeypatch.setattr(
        refs, "run_cmd",
        lambda *a, **k: json.dumps({"statusCheckRollup": [
            {"conclusion": "FAILURE", "detailsUrl": "https://github.com/o/r/actions/runs/9"}]}))
    assert refs.fetch_ci(
        "gh", "https://github.com/o/r/pull/9", "/repo") == (
            "failure", "https://github.com/o/r/actions/runs/9")


def test_fetch_ci_gh_rollup_precedence(monkeypatch):
    # any failure beats any running beats success
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps(
        {"statusCheckRollup": [
            {"conclusion": "SUCCESS", "detailsUrl": "https://github.com/o/r/actions/runs/1"},
            {"conclusion": "IN_PROGRESS", "detailsUrl": "https://github.com/o/r/actions/runs/2"},
            {"conclusion": "ACTION_REQUIRED", "detailsUrl": "https://github.com/o/r/actions/runs/3"}]}))
    assert refs.fetch_ci(
        "gh", "https://github.com/o/r/pull/9", "/repo") == (
            "failure", "https://github.com/o/r/actions/runs/3")
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps(
        {"statusCheckRollup": [
            {"conclusion": "SUCCESS", "detailsUrl": "https://github.com/o/r/actions/runs/1"},
            {"conclusion": "QUEUED", "detailsUrl": "https://github.com/o/r/actions/runs/2"}]}))
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
        if len(args) > 2 and "/jobs" in args[2]:
            return json.dumps([{"id": 10, "web_url": "https://git.jibit.cloud/g/p/-/jobs/10"},
                               {"id": 11, "web_url": "https://git.jibit.cloud/g/p/-/jobs/11"}])
        return json.dumps([{"id": 1, "status": "failed"},
                           {"id": 2, "status": "success"}])

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci(
        "glab", "https://git.jibit.cloud/g/p/-/merge_requests/7", "/repo"
    ) == ("success", "https://git.jibit.cloud/g/p/-/jobs/11")
    assert calls == [("glab", "api",
                      "projects/g%2Fp/merge_requests/7/pipelines",
                      "--hostname", "git.jibit.cloud"),
                     ("glab", "api",
                      "projects/g%2Fp/pipelines/2/jobs",
                      "--hostname", "git.jibit.cloud")]


def test_fetch_ci_glab_hostname_retry(monkeypatch):
    calls = []

    def fake_run(cmd, *args, **kw):
        calls.append(args)
        if "--hostname" in args:
            raise HarnessError("unknown flag")
        if len(args) > 1 and "/jobs" in args[1]:
            return json.dumps([{"id": 50, "web_url": "https://git.example.com/g/p/-/jobs/50"}])
        return json.dumps([{"id": 5, "status": "running"}])

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    got = refs.fetch_ci(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo")
    assert got == ("running", "https://git.example.com/g/p/-/jobs/50"), got
    assert len(calls) == 4, calls


def test_fetch_ci_glab_status_map(monkeypatch):
    for raw, want in (("canceled", "failure"), ("skipped", "not_started"),
                      ("pending", "running")):
        def fake_run(cmd, *args, r=raw, **kw):
            if len(args) > 1 and "/jobs" in args[1]:
                return json.dumps([{"id": 60, "web_url": "https://git.example.com/g/p/-/jobs/60"}])
            return json.dumps([{"id": 1, "status": r}])
        monkeypatch.setattr(refs, "run_cmd", fake_run)
        got = refs.fetch_ci(
            "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo")
        assert got[0] == want
        # only a real (non-not_started) pipeline resolves a job URL
        assert got[1] == ("" if want == "not_started"
                          else "https://git.example.com/g/p/-/jobs/60")


def test_ci_fetch_failure_is_soft(monkeypatch):
    def boom(*a, **k):
        raise HarnessError("timeout")

    monkeypatch.setattr(refs, "run_cmd", boom)
    assert refs.fetch_ci_status(
        "gh", "https://github.com/o/r/pull/9", "/repo") is None
    assert refs.fetch_ci(
        "glab", "https://git.jibit.cloud/g/p/-/merge_requests/7", "/repo"
    ) == (None, "")
    # unrecognized URL also degrades to None, no raise
    assert refs.fetch_ci_status(
        "glab", "https://example.com/bogus", "/repo") is None


def test_fetch_ci_glab_empty_jobs_falls_back_to_pipeline_url(monkeypatch):
    """A pipeline with no jobs (merge_request_event that never started) still
    yields the pipeline page so the badge never renders as a dead link."""
    def fake_run(cmd, *args, **kw):
        if len(args) > 1 and "/jobs" in args[1]:
            return json.dumps([])
        return json.dumps([{"id": 78899, "status": "failed",
                            "web_url": "https://git.example.com/g/p/-/pipelines/78899"}])

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo"
    ) == ("failure", "https://git.example.com/g/p/-/pipelines/78899")


def test_fetch_ci_glab_job_without_web_url_falls_back_to_pipeline(monkeypatch):
    """Job rows lacking web_url degrade to the pipeline page, not ''."""
    def fake_run(cmd, *args, **kw):
        if len(args) > 1 and "/jobs" in args[1]:
            return json.dumps([{"id": 60, "web_url": ""}])
        return json.dumps([{"id": 5, "status": "success",
                            "web_url": "https://git.example.com/g/p/-/pipelines/5"}])

    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo"
    ) == ("success", "https://git.example.com/g/p/-/pipelines/5")


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
    """Issue #32: `Status:\\s+RESOLVED` second line (case-insensitive) resolves."""
    assert refs._gh_bot_comment_resolved("# Code Review: ok\nStatus: RESOLVED")
    assert refs._gh_bot_comment_resolved("# Code Review: ok\n\n  Status: RESOLVED  \nbody text")
    assert refs._gh_bot_comment_resolved("# Code Review: ok\nstatus: resolved")
    assert refs._gh_bot_comment_resolved("# Code Review: ok\nSTATUS:   RESOLVED")
    assert refs._gh_bot_comment_resolved("# Code Review: ok\nStatus:\tRESOLVED")
    assert not refs._gh_bot_comment_resolved("# Code Review: open finding")
    assert not refs._gh_bot_comment_resolved("# Code Review: ok\nbody\nStatus: RESOLVED")
    assert not refs._gh_bot_comment_resolved("# Code Review: only header, no second line")
    assert not refs._gh_bot_comment_resolved("# Code Review: ok\nStatus:RESOLVED")
    assert not refs._gh_bot_comment_resolved("# Code Review: ok\nStatus: RESOLVED extra")
    assert not refs._gh_bot_comment_resolved("")

def test_fetch_pr_comment_stats_glab(monkeypatch):
    """Issue #28: glab counts # Code Review notes + discussion resolution.
    A bot `# Code Review` note counts as resolved when natively resolved
    OR its second non-empty line matches `Status:\\s+RESOLVED`
    (case-insensitive); a bot discussion is resolved only when every bot
    note in it is resolved. Non-bot threads keep native resolution.
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


def test_fetch_pr_comment_stats_glab_bot_marker(monkeypatch):
    """Bot note resolved when natively resolved OR marker present (either suffices)."""
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([
        # Marker present but native flags say unresolved → resolved anyway.
        {"resolved": False, "notes": [
            {"body": "# Code Review: ok\nStatus: RESOLVED", "resolved": False,
             "resolvable": True}]},
        # No marker but natively resolved → resolved (MR 1766 shape).
        {"resolved": True, "notes": [
            {"body": "# Code Review: open finding", "resolved": True,
             "resolvable": True}]},
        # Mixed bot notes in one discussion, none natively resolved and one
        # missing the marker → unresolved (all() required on both paths).
        {"notes": [
            {"body": "# Code Review: one\nStatus: RESOLVED",
             "resolved": False, "resolvable": True},
            {"body": "# Code Review: two", "resolved": False,
             "resolvable": True}]},
        # Non-bot thread keeps native resolution.
        {"resolved": True, "notes": [
            {"body": "looks good", "resolved": True, "resolvable": True}]},
    ]))
    assert refs.fetch_pr_comment_stats(
        "glab", "https://git.example.com/g/p/-/merge_requests/7", "/repo") == {
            "reviews": 4, "unresolved": 1, "resolved": 3}

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
             "baseRefName": "main",
             "updatedAt": "2026-09-20T10:00:00Z",
             "url": "https://github.com/o/r/pull/33"}])

    monkeypatch.setattr(refs, "run_cmd", fake)
    prs = refs.fetch_open_prs("gh", "/repo")
    assert prs == [{"number": 33, "title": "T1", "branch": "feat/x",
                    "target_branch": "main",
                    "updated": "2026-09-20T10:00:00Z",
                    "url": "https://github.com/o/r/pull/33",
                    "author": "", "state": "OPEN"}]
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
    assert prs[0]["author"] == ""
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


def test_fetch_ci_details_gh(monkeypatch):
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps({
        "statusCheckRollup": [
            {
                "name": "pytest",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "detailsUrl": "https://github.com/o/r/runs/1",
            },
            {
                "name": "lint",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/o/r/runs/2",
            },
        ]
    }))
    state, url, checks = refs.fetch_ci_details("gh", "https://github.com/o/r/pull/151", "/repo")
    assert state == "failure"
    assert url == "https://github.com/o/r/runs/1"
    assert len(checks) == 2
    assert checks[0]["name"] == "pytest"
    assert checks[0]["conclusion"] == "failure"
    assert checks[1]["name"] == "lint"
    assert checks[1]["conclusion"] == "success"


def test_fetch_ci_details_glab_fallback_does_not_leak_older_checks(monkeypatch):
    """When newest pipeline has no jobs and falls back to an older pipeline for
    the badge URL, checks must remain [] instead of pairing stale jobs with newest."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 200, "status": "failed", "sha": "newsha", "web_url": "https://git.x/p/200"},
                {"id": 100, "status": "success", "sha": "oldsha", "web_url": "https://git.x/p/100"},
            ])
        if "/pipelines/200/jobs" in api:
            return json.dumps([])
        if "/pipelines/100/jobs" in api:
            return json.dumps([
                {"id": 10, "name": "old-job", "status": "success", "web_url": "https://git.x/j/10"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url, checks = refs.fetch_ci_details(
        "glab", "https://git.x/g/r/-/merge_requests/9", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.x/j/10"
    assert checks == []

def test_fetch_ci_details_glab_fallback_stops_after_first_hit(monkeypatch):
    """Once an older pipeline banks a fallback URL, remaining pipelines are
    not fetched (saves one glab API call per older pipeline)."""
    calls = []
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 300, "status": "failed", "sha": "newsha", "web_url": "https://git.x/p/300"},
                {"id": 200, "status": "success", "sha": "midsha", "web_url": "https://git.x/p/200"},
                {"id": 100, "status": "success", "sha": "oldsha", "web_url": "https://git.x/p/100"},
            ])
        if "/jobs" in api:
            calls.append(api)
            if "/pipelines/300/jobs" in api:
                return json.dumps([])
            if "/pipelines/200/jobs" in api:
                return json.dumps([
                    {"id": 10, "name": "mid-job", "status": "success", "web_url": "https://git.x/j/10"},
                ])
            return json.dumps([
                {"id": 9, "name": "old-job", "status": "success", "web_url": "https://git.x/j/9"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url, _ = refs.fetch_ci_details(
        "glab", "https://git.x/g/r/-/merge_requests/9", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.x/j/10"
    assert not any("/pipelines/100/jobs" in c for c in calls)


def test_fetch_ci_details_glab_preserves_newest_checks_without_web_url(monkeypatch):
    """When newest pipeline has jobs without web_url, its checks are preserved."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 200, "status": "failed", "sha": "newsha", "web_url": "https://git.x/p/200"},
            ])
        if "/pipelines/200/jobs" in api:
            return json.dumps([
                {"id": 20, "name": "unit", "status": "failed", "web_url": ""},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url, checks = refs.fetch_ci_details(
        "glab", "https://git.x/g/r/-/merge_requests/9", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.x/p/200"
    assert len(checks) == 1
    assert checks[0]["name"] == "unit"
    assert checks[0]["conclusion"] == "failure"

def test_fetch_ci_glab_prefers_failed_job_over_newer_success(monkeypatch):
    """Issue #116: pipeline listing jobs [..., failed 203729, success 203730]
    must link the failed job, not max(id)."""
    def fake(*a, **k):
        api = a[2]
        if "/pipelines/81409/jobs" in api:
            return json.dumps([
                {"id": 203729, "name": "test", "status": "failed",
                 "web_url": "https://git.jibit.cloud/server/ipg-commons/-/jobs/203729"},
                {"id": 203730, "name": "test", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/ipg-commons/-/jobs/203730"},
            ])
        return json.dumps([{"id": 81409, "status": "failed",
                            "sha": "abc",
                            "web_url": "https://git.jibit.cloud/server/ipg-commons/-/pipelines/81409"}])

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url, checks = refs.fetch_ci_details(
        "glab", "https://git.jibit.cloud/server/ipg-commons/-/merge_requests/1", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.jibit.cloud/server/ipg-commons/-/jobs/203729"
    assert {c["conclusion"] for c in checks} == {"failure", "success"}


def test_pick_job_url_prefers_running_over_success():
    from workagent.refs_ci import _pick_job_url
    jobs = [
        {"id": 1, "status": "success", "web_url": "https://x/j/1"},
        {"id": 2, "status": "running", "web_url": "https://x/j/2"},
    ]
    assert _pick_job_url(jobs) == "https://x/j/2"
    assert _pick_job_url([]) == ""
    # Jobs without web_url never compete.
    assert _pick_job_url([{"id": 3, "status": "failed", "web_url": ""}]) == ""
    # Cancelled jobs (either spelling) rank in the failure tier, above
    # skipped/manual leftovers.
    assert _pick_job_url([
        {"id": 1, "status": "skipped", "web_url": "https://x/j/1"},
        {"id": 2, "status": "canceled", "web_url": "https://x/j/2"},
    ]) == "https://x/j/2"
    assert _pick_job_url([
        {"id": 1, "status": "skipped", "web_url": "https://x/j/1"},
        {"id": 2, "status": "cancelled", "web_url": "https://x/j/2"},
    ]) == "https://x/j/2"

def test_fetch_pr_review_details_gh(monkeypatch):
    def fake(*a, **k):
        if a[:3] == ("gh", "pr", "view"):
            return json.dumps({
                "comments": [
                    {
                        "id": "IC_1",
                        "body": "# Code Review\nNeeds fix",
                        "author": {"login": "bot"},
                    }
                ]
            })
        return json.dumps({
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": [
                            {
                                "id": "PRRT_1",
                                "isResolved": False,
                                "comments": {"nodes": [
                                    {
                                        "body": "Handle None",
                                        "path": "src/a.py",
                                        "line": 12,
                                        "author": {"login": "alice"},
                                    }
                                ]},
                            }
                        ]},
                    }
                }
            }
        })

    monkeypatch.setattr(refs, "run_cmd", fake)
    stats, threads = refs.fetch_pr_review_details(
        "gh", "https://github.com/o/r/pull/151", "/repo"
    )
    assert stats == {"reviews": 1, "unresolved": 2, "resolved": 0}
    assert len(threads) == 2
    assert {t["thread_id"] for t in threads} == {"IC_1", "PRRT_1"}
    inline = next(t for t in threads if t["thread_id"] == "PRRT_1")
    assert inline["file"] == "src/a.py" and inline["line"] == 12 and not inline["resolved"]


def test_fetch_pr_review_details_glab(monkeypatch):
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([
        {
            "id": "disc-1",
            "resolved": False,
            "notes": [
                {
                    "id": 101,
                    "body": "Check edge case",
                    "resolved": False,
                    "resolvable": True,
                    "author": {"username": "bob"},
                    "position": {"new_path": "src/b.py", "new_line": 33},
                    "updated_at": "2026-11-22T00:02:00Z",
                }
            ],
        },
        {
            "id": "disc-2",
            "resolved": True,
            "notes": [
                {
                    "id": 102,
                    "body": "# Code Review\nStatus: RESOLVED",
                    "resolved": True,
                    "resolvable": True,
                    "author": {"username": "review-bot"},
                    "updated_at": "2026-11-22T00:03:00Z",
                }
            ],
        },
    ]))
    stats, threads = refs.fetch_pr_review_details(
        "glab", "https://git.x/g/r/-/merge_requests/7", "/repo"
    )
    assert stats == {"reviews": 1, "unresolved": 1, "resolved": 1}
    assert len(threads) == 2
    t1 = next(t for t in threads if t["thread_id"] == "disc-1")
    assert t1["file"] == "src/b.py" and t1["line"] == 33 and not t1["resolved"]
    t2 = next(t for t in threads if t["thread_id"] == "disc-2")
    assert t2["is_bot"] and t2["resolved"]


def test_fetch_ci_glab_ignores_side_ci_security_when_main_fails(monkeypatch):
    """Issue #184: main CI (#81906) fails while later side-CI (#81907, security) passes."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 81907, "status": "success", "sha": "a9892b0e",
                 "source": "merge_request_event",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81907"},
                {"id": 81906, "status": "failed", "sha": "a9892b0e",
                 "source": "push",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81906"},
            ])
        if "/pipelines/81907/jobs" in api:
            return json.dumps([
                {"id": 9001, "name": "trivy:source", "stage": "security", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/9001"},
            ])
        if "/pipelines/81906/jobs" in api:
            return json.dumps([
                {"id": 8001, "name": "test", "stage": "check", "status": "failed",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/8001"},
                {"id": 8002, "name": "trivy:source", "stage": "security", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/8002"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url, checks = refs.fetch_ci_details(
        "glab", "https://git.jibit.cloud/server/projectx/-/merge_requests/1823", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.jibit.cloud/server/projectx/-/jobs/8001"
    assert [c["name"] for c in checks] == ["test", "trivy:source"]


def test_fetch_ci_glab_ignores_side_ci_when_main_passes(monkeypatch):
    """Issue #184: main CI passes while later side-CI fails -> still report success + main link."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 81907, "status": "failed", "sha": "a9892b0e",
                 "source": "merge_request_event",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81907"},
                {"id": 81906, "status": "success", "sha": "a9892b0e",
                 "source": "push",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81906"},
            ])
        if "/pipelines/81907/jobs" in api:
            return json.dumps([
                {"id": 9001, "name": "trivy:source", "stage": "security", "status": "failed",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/9001"},
            ])
        if "/pipelines/81906/jobs" in api:
            return json.dumps([
                {"id": 8001, "name": "test", "stage": "check", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/8001"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url = refs.fetch_ci(
        "glab", "https://git.jibit.cloud/server/projectx/-/merge_requests/1823", "/repo"
    )
    assert state == "success"
    assert url == "https://git.jibit.cloud/server/projectx/-/jobs/8001"


def test_fetch_ci_glab_falls_back_when_all_pipelines_match_side_ci(monkeypatch):
    """If only the side-CI pipeline exists for the SHA, fall back to it instead of not_started."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 81907, "status": "success", "sha": "a9892b0e",
                 "source": "merge_request_event",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81907"},
            ])
        if "/pipelines/81907/jobs" in api:
            return json.dumps([
                {"id": 9001, "name": "trivy:source", "stage": "security", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/9001"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url = refs.fetch_ci(
        "glab", "https://git.jibit.cloud/server/projectx/-/merge_requests/1823", "/repo"
    )
    assert state == "success"
    assert url == "https://git.jibit.cloud/server/projectx/-/jobs/9001"


def test_fetch_ci_branch_glab_ignores_side_ci(monkeypatch):
    """Branch CI lookup also ignores later side-CI pipelines on the same SHA."""
    def fake(*a, **k):
        api = a[2]
        if "/pipelines?" in api:
            return json.dumps([
                {"id": 81907, "status": "success", "sha": "a9892b0e",
                 "source": "merge_request_event",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81907"},
                {"id": 81906, "status": "failed", "sha": "a9892b0e",
                 "source": "push",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81906"},
            ])
        if "/pipelines/81907/jobs" in api:
            return json.dumps([
                {"id": 9001, "name": "trivy:source", "stage": "security", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/9001"},
            ])
        if "/pipelines/81906/jobs" in api:
            return json.dumps([
                {"id": 8001, "name": "test", "stage": "check", "status": "failed",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/8001"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    from workagent import refs_ci
    state, url = refs_ci.fetch_branch_ci(
        "glab", "https://git.jibit.cloud/server/projectx.git", "feat/x", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.jibit.cloud/server/projectx/-/jobs/8001"


def test_fetch_ci_gh_ignores_configured_side_ci(monkeypatch):
    from workagent import refs_ci
    monkeypatch.setattr(refs_ci, "_repo_ci_ignore", lambda cwd: ["trivy-*"])
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps({
        "statusCheckRollup": [
            {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS",
             "detailsUrl": "https://github.com/o/r/runs/1"},
            {"name": "trivy-scan", "status": "COMPLETED", "conclusion": "FAILURE",
             "detailsUrl": "https://github.com/o/r/runs/2"},
        ]
    }))
    state, url, checks = refs.fetch_ci_details("gh", "https://github.com/o/r/pull/1", "/repo")
    assert state == "success"
    assert url == "https://github.com/o/r/runs/1"
    assert [c["name"] for c in checks] == ["test"]


def test_fetch_ci_glab_main_without_jobs_never_links_side_ci(monkeypatch):
    """Issue #184: main pipeline with no job URL must not fall back to a side-CI link."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 81907, "status": "success", "sha": "a9892b0e",
                 "source": "merge_request_event",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81907"},
                {"id": 81906, "status": "running", "sha": "a9892b0e",
                 "source": "push",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81906"},
            ])
        if "/pipelines/81907/jobs" in api:
            return json.dumps([
                {"id": 9001, "name": "trivy:source", "stage": "security", "status": "success",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/9001"},
            ])
        if "/pipelines/81906/jobs" in api:
            return "[]"  # main pipeline: nothing started yet
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url = refs.fetch_ci(
        "glab", "https://git.jibit.cloud/server/projectx/-/merge_requests/1823", "/repo"
    )
    assert state == "running"
    assert url == "https://git.jibit.cloud/server/projectx/-/pipelines/81906"


def test_fetch_ci_glab_survives_jobs_api_failure(monkeypatch):
    """A transient jobs error on one candidate must not blank the CI cell."""
    from workagent.errors import HarnessError

    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 81907, "status": "success", "sha": "a9892b0e",
                 "source": "merge_request_event",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81907"},
                {"id": 81906, "status": "failed", "sha": "a9892b0e",
                 "source": "push",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/pipelines/81906"},
            ])
        if "/pipelines/81907/jobs" in api:
            return "{broken"
        if "/pipelines/81906/jobs" in api:
            return json.dumps([
                {"id": 8001, "name": "test", "stage": "check", "status": "failed",
                 "web_url": "https://git.jibit.cloud/server/projectx/-/jobs/8001"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url = refs.fetch_ci(
        "glab", "https://git.jibit.cloud/server/projectx/-/merge_requests/1823", "/repo"
    )
    assert (state, url) == ("failure", "https://git.jibit.cloud/server/projectx/-/jobs/8001")


def test_repo_ci_ignore_prefers_longest_repo_path(monkeypatch):
    """Nested registered repos: the innermost match wins, not config order."""
    from workagent import refs_ci, store as _store
    monkeypatch.setattr(_store, "load_repos", lambda: {
        "outer": {"path": "/work", "ci_ignore": ["outer-ci"]},
        "inner": {"path": "/work/proj", "ci_ignore": ["trivy:*"]},
    })
    assert refs_ci._repo_ci_ignore("/work/proj/src") == ["trivy:*"]
    assert refs_ci._repo_ci_ignore("/work/other") == ["outer-ci"]


def test_repo_ci_ignore_resolves_linked_worktree(monkeypatch):
    """A linked worktree inherits its main repo's ci_ignore patterns."""
    from workagent import refs_ci, store as _store
    monkeypatch.setattr(_store, "load_repos", lambda: {
        "proj": {"path": "/work/proj", "ci_ignore": ["trivy:*"]},
    })
    monkeypatch.setattr(_store, "load_links", lambda: {
        "jira:IPG-1": {"worktree": "/wt/IPG-1", "repo": "/work/proj"},
    })
    assert refs_ci._repo_ci_ignore("/wt/IPG-1") == ["trivy:*"]
    assert refs_ci._repo_ci_ignore("/wt/IPG-1/src/sub") == ["trivy:*"]
    assert refs_ci._repo_ci_ignore("/wt/IPG-10") == []


def test_fetch_ci_details_glab_fallback_survives_jobs_error(monkeypatch):
    """A transient jobs error on an older fallback pipeline must not fail the lookup."""
    def fake(*a, **k):
        api = a[2]
        if api.endswith("/pipelines"):
            return json.dumps([
                {"id": 300, "status": "failed", "sha": "newsha",
                 "source": "push", "web_url": "https://git.x/p/300"},
                {"id": 200, "status": "success", "sha": "midsha",
                 "source": "push", "web_url": "https://git.x/p/200"},
                {"id": 100, "status": "success", "sha": "oldsha",
                 "source": "push", "web_url": "https://git.x/p/100"},
            ])
        if "/pipelines/300/jobs" in api:
            return "[]"
        if "/pipelines/200/jobs" in api:
            return "{broken"
        if "/pipelines/100/jobs" in api:
            return json.dumps([
                {"id": 10, "name": "test", "stage": "check", "status": "success",
                 "web_url": "https://git.x/j/10"},
            ])
        return "[]"

    monkeypatch.setattr(refs, "run_cmd", fake)
    state, url, checks = refs.fetch_ci_details(
        "glab", "https://git.x/g/r/-/merge_requests/9", "/repo"
    )
    assert state == "failure"
    assert url == "https://git.x/j/10"
    assert checks == []


def test_fetch_ci_branch_gh_ignores_configured_side_ci(monkeypatch):
    """Branch CI lookup on GitHub skips runs matching ci_ignore and falls back if all match."""
    from workagent import refs_ci
    monkeypatch.setattr(refs_ci, "_repo_ci_ignore", lambda cwd: ["trivy*", "security*"])

    # data[0] matches ci_ignore via workflowName -> pick data[1] (main run).
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([
        {"name": "scan", "workflowName": "Security Scan", "status": "COMPLETED",
         "conclusion": "SUCCESS", "url": "https://github.com/o/r/actions/runs/2"},
        {"name": "CI", "workflowName": "Build & Test", "status": "COMPLETED",
         "conclusion": "FAILURE", "url": "https://github.com/o/r/actions/runs/1"},
    ]))
    state, url = refs_ci.fetch_branch_ci("gh", "https://github.com/o/r.git", "feat/x", "/repo")
    assert (state, url) == ("failure", "https://github.com/o/r/actions/runs/1")

    # data[0] matches ci_ignore via name -> pick data[1].
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([
        {"name": "trivy-scan", "workflowName": "Misc", "status": "COMPLETED",
         "conclusion": "FAILURE", "url": "https://github.com/o/r/actions/runs/4"},
        {"name": "pytest", "workflowName": "CI", "status": "COMPLETED",
         "conclusion": "SUCCESS", "url": "https://github.com/o/r/actions/runs/3"},
    ]))
    state, url = refs_ci.fetch_branch_ci("gh", "https://github.com/o/r.git", "feat/x", "/repo")
    assert (state, url) == ("success", "https://github.com/o/r/actions/runs/3")

    # All runs match ci_ignore -> fall back to data[0] rather than not_started.
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([
        {"name": "trivy-scan", "workflowName": "Security", "status": "COMPLETED",
         "conclusion": "SUCCESS", "url": "https://github.com/o/r/actions/runs/6"},
        {"name": "security-audit", "workflowName": "Security", "status": "COMPLETED",
         "conclusion": "FAILURE", "url": "https://github.com/o/r/actions/runs/5"},
    ]))
    state, url = refs_ci.fetch_branch_ci("gh", "https://github.com/o/r.git", "feat/x", "/repo")
    assert (state, url) == ("success", "https://github.com/o/r/actions/runs/6")


def test_fetch_ci_job_log_gitlab_tails_50_lines_and_strips_ansi(monkeypatch):
    from workagent import refs_ci

    calls = []
    raw_lines = [f"\x1b[31mline {i}\x1b[0m" for i in range(1, 71)]
    raw_lines.append("progress 10%\rprogress 100% FAILED")

    def fake(*args, **kwargs):
        calls.append(args)
        return "\n".join(raw_lines)

    monkeypatch.setattr(refs, "run_cmd", fake)
    out = refs_ci.fetch_ci_job_log(
        "glab",
        "https://git.jibit.cloud/server/projectx/-/jobs/203729",
        cwd="/repo",
    )
    assert calls == [(
        "glab", "api", "projects/server%2Fprojectx/jobs/203729/trace",
        "--hostname", "git.jibit.cloud",
    )]
    lines = out.splitlines()
    assert len(lines) == 50
    assert lines[0] == "line 22"
    assert lines[-2] == "line 70"
    assert lines[-1] == "progress 100% FAILED"
    assert "\x1b[" not in out


def test_fetch_ci_job_log_gitlab_pipeline_url_picks_failed_job(monkeypatch):
    from workagent import refs_ci

    def fake(*args, **kwargs):
        api = args[2]
        if "/pipelines/81409/jobs" in api:
            return json.dumps([
                {"id": 100, "name": "lint", "status": "success"},
                {"id": 101, "name": "test", "status": "failed"},
            ])
        if "/jobs/101/trace" in api:
            return "FAILED test_foo\n"
        return ""

    monkeypatch.setattr(refs, "run_cmd", fake)
    out = refs_ci.fetch_ci_job_log(
        "glab",
        "https://git.jibit.cloud/server/projectx/-/pipelines/81409",
        cwd="/repo",
    )
    assert out == "FAILED test_foo"


def test_fetch_ci_job_log_github_variants(monkeypatch):
    from workagent import refs_ci

    calls = []

    def fake_run_job(*args, **kwargs):
        calls.append(args)
        return "\n".join(f"gh-line-{i}" for i in range(1, 65))

    monkeypatch.setattr(refs, "run_cmd", fake_run_job)
    out = refs_ci.fetch_ci_job_log(
        "gh",
        "https://github.com/ynsr/workagent/actions/runs/555/job/777",
        cwd="/repo",
    )
    assert calls == [(
        "gh", "run", "view", "555", "--job", "777", "--log-failed", "--repo", "ynsr/workagent"
    )]
    assert len(out.splitlines()) == 50
    assert out.splitlines()[0] == "gh-line-15"
    assert out.splitlines()[-1] == "gh-line-64"

    # Empty --log-failed falls back to gh api job logs
    calls.clear()

    def fake_fallback(*args, **kwargs):
        calls.append(args)
        if "run" in args:
            return ""
        return "job cancelled by user"

    monkeypatch.setattr(refs, "run_cmd", fake_fallback)
    out_fb = refs_ci.fetch_ci_job_log(
        "gh",
        "https://github.com/ynsr/workagent/actions/runs/555/job/777",
        cwd="/repo",
    )
    assert out_fb == "job cancelled by user"
    assert calls[1] == ("gh", "api", "repos/ynsr/workagent/actions/jobs/777/logs")

    # /runs/<id> and ?check_run_id=<id>
    calls.clear()
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: (calls.append(a) or "api check log"))
    assert refs_ci.fetch_ci_job_log("gh", "https://github.com/ynsr/workagent/runs/999") == "api check log"
    assert refs_ci.fetch_ci_job_log(
        "gh", "https://github.com/ynsr/workagent/pull/12/checks?check_run_id=888"
    ) == "api check log"
    assert calls == [
        ("gh", "api", "repos/ynsr/workagent/actions/jobs/999/logs"),
        ("gh", "api", "repos/ynsr/workagent/actions/jobs/888/logs"),
    ]


def test_fetch_ci_job_log_soft_fails_and_populate_failing_check_logs(monkeypatch):
    from workagent import refs_ci
    from workagent.errors import HarnessError

    def boom(*_a, **_k):
        raise HarnessError("network down")

    monkeypatch.setattr(refs, "run_cmd", boom)
    assert refs_ci.fetch_ci_job_log("gh", "https://github.com/o/r/actions/runs/1/job/2") == ""
    assert refs_ci.fetch_ci_job_log("gh", "https://external-ci.example/build/1") == ""

    fetched_urls = []

    def fake_ok(*args, **kwargs):
        fetched_urls.append(args)
        return "AssertionError: boom"

    monkeypatch.setattr(refs, "run_cmd", fake_ok)
    checks = [
        {"name": "lint", "status": "completed", "conclusion": "success",
         "url": "https://github.com/o/r/actions/runs/1/job/10", "log_tail": ""},
        {"name": "test", "status": "completed", "conclusion": "failure",
         "url": "https://github.com/o/r/actions/runs/1/job/20", "log_tail": ""},
        {"name": "cached", "status": "completed", "conclusion": "failure",
         "url": "https://github.com/o/r/actions/runs/1/job/30", "log_tail": "already here"},
    ]
    refs_ci.populate_failing_check_logs("gh", checks, cwd="/repo")
    assert checks[0]["log_tail"] == ""
    assert checks[1]["log_tail"] == "AssertionError: boom"
    assert checks[2]["log_tail"] == "already here"
    assert len(fetched_urls) == 1
