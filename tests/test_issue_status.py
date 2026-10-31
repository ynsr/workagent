"""issue_status: tracker status transitions + release discovery."""
from workagent import issue_status as st
from workagent.errors import HarnessError


def test_jira_move_uses_no_extra_flags(monkeypatch):
    seen = []
    def _run(*a, **k):
        seen.append(a)
        return ""
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.set_issue_status("https://example.com/browse/IPG-7", "code review") is True
    assert seen[0][:4] == ("jira", "issue", "move", "IPG-7")
    assert len(seen[0]) == 5  # KEY STATE only, no --no-input


def test_jira_move_bad_state_reports_valid(monkeypatch):
    calls = []
    def _run(*a, **k):
        calls.append(a)
        raise HarnessError('invalid transition state "X"\n'
                           "Available states for issue IPG-7: 'To Do', 'Done'")
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.set_issue_status("IPG-7", "in progress") is False
    assert len(calls) == 1  # narrowing: 2nd variant skipped (not in valid set)
    assert calls[0][4] == "In Progress"


def test_gh_label_swap_and_close(monkeypatch):
    seen = []
    monkeypatch.setattr(st, "run_cmd", lambda *a, **k: seen.append(a) or "")
    assert st.set_issue_status("https://github.com/o/r/issues/22", "code review") is True
    add = seen[0]
    assert add[:4] == ("gh", "issue", "edit", "https://github.com/o/r/issues/22")
    assert "status: code review" in " ".join(add)
    assert "status: done" in " ".join(add)
    seen.clear()
    assert st.set_issue_status("https://github.com/o/r/issues/22", "done") is True
    assert seen[0][:3] == ("gh", "issue", "edit")
    assert seen[1] == ("gh", "issue", "close", "https://github.com/o/r/issues/22",
                       "--reason", "completed")




def test_glab_label_swap_and_close(monkeypatch):
    seen = []
    monkeypatch.setattr(st, "run_cmd", lambda *a, **k: seen.append(a) or "")
    url = "https://gitlab.com/g/r/-/issues/3"
    assert st.set_issue_status(url, "released") is True
    assert seen[0][:4] == ("glab", "issue", "update", url)
    assert "status: released" in " ".join(seen[0])
    seen.clear()
    assert st.set_issue_status(url, "done") is True
    assert seen[0][:3] == ("glab", "issue", "update")
    assert seen[1][:3] == ("glab", "issue", "close")


def test_soft_fail_paths(monkeypatch, capsys):
    assert st.set_issue_status("https://github.com/o/r/pull/5", "done") is False
    assert st.set_issue_status("IPG-1", "bogus") is False
    def _boom(*a, **k):
        raise HarnessError("nope")
    monkeypatch.setattr(st, "run_cmd", _boom)
    assert st.set_issue_status("IPG-1", "done") is False
    assert "warning" in capsys.readouterr().err
    assert st.set_issue_status("not-a-ref", "done") is False


def test_dry_run_writes_nothing(monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("must not run commands in dry-run")
    monkeypatch.setattr(st, "run_cmd", _boom)
    assert st.set_issue_status("IPG-1", "done", dry_run=True) is True
    assert "dry-run" in capsys.readouterr().err


def test_discover_released_refs(monkeypatch):
    calls = []
    def _run(*a, **k):
        calls.append(a)
        if a[3:5] == ("rev-list", "old..new"):
            return "sha1\nsha2"
        if a[3:6] == ("branch", "-r", "--contains"):
            return "origin/feat/IPG-9-thing" if a[6] == "sha1" else ""
        if a[3] == "log":
            return "Fix IPG-10 and o/r#44" if a[-1] == "sha2" else "wip"
        raise AssertionError(a)
    monkeypatch.setattr(st, "run_cmd", _run)
    monkeypatch.setattr("workagent.store.load_links",
                        lambda **k: {"k": {"branch": "feat/IPG-9-thing",
                                           "issue": "IPG-9"}})
    out = st.discover_released_refs("/repo", "old", "new")
    assert out == ["IPG-9", "IPG-10", "o/r#44"]

def test_discover_released_refs_suffixed_branch_ref_and_scan(monkeypatch):
    """Branch-mapped `issue@repo` refs must still parse, and mapped commits
    must still be subject-scanned (release run 1d77abac2d4b: only IPG-934
    moved; IPG-1066/IPG-938 were dropped as `KEY@projectx`, and the
    `if not mapped` guard skipped the regex that would have found them)."""
    def _run(*a, **k):
        if a[3:5] == ("rev-list", "old..new"):
            return "sha1\nsha2"
        if a[3:6] == ("branch", "-r", "--contains"):
            return "origin/feat/IPG-934--x" if a[6] == "sha1" else ""
        if a[3] == "log":
            return "feat: work [IPG-934]" if a[-1] == "sha1" else "fix: work [IPG-1066]"
        raise AssertionError(a)
    monkeypatch.setattr(st, "run_cmd", _run)
    monkeypatch.setattr("workagent.store.load_links",
                        lambda **k: {"jira:IPG-934@projectx": {
                            "branch": "feat/IPG-934--x",
                            "issue": "IPG-934@projectx"}})
    out = st.discover_released_refs("/repo", "old", "new")
    assert out == ["IPG-934", "IPG-1066"]


def test_set_issue_status_accepts_suffixed_jira_ref(monkeypatch):
    """Link-key-shaped `IPG-934@projectx` refs must transition, not warn."""
    seen = []
    monkeypatch.setattr(st, "run_cmd", lambda *a, **k: seen.append(a) or "")
    assert st.set_issue_status("IPG-934@projectx", "released") is True
    assert seen[0][:4] == ("jira", "issue", "move", "IPG-934")



def test_settle_post_work_pr_or_done(monkeypatch):
    seen = []
    monkeypatch.setattr(st, "set_issue_status",
                        lambda ref, state, dry_run=False: seen.append((ref, state)) or True)
    assert st.settle_issue_post_work("IPG-1", "https://github.com/o/r/pull/9") == "code review"
    assert st.settle_issue_post_work("IPG-1", "") == "done"
    assert seen == [("IPG-1", "code review"), ("IPG-1", "done")]
    assert st.settle_issue_post_work("", "https://github.com/o/r/pull/9") == ""


def test_settle_post_review_gated(monkeypatch):
    seen = []
    monkeypatch.setattr(st, "set_issue_status",
                        lambda ref, state, dry_run=False: seen.append((ref, state)) or True)
    monkeypatch.setattr(st, "_issue_state", lambda *a: "code review")
    assert st.settle_issue_post_review("IPG-1", "approved") == "ready to release"
    assert st.settle_issue_post_review("IPG-1", "changes") == "in progress"
    assert st.settle_issue_post_review("IPG-1", "open") == ""
    monkeypatch.setattr(st, "_issue_state", lambda *a: "in progress")
    assert st.settle_issue_post_review("IPG-1", "approved") == ""
    assert seen == [("IPG-1", "ready to release"), ("IPG-1", "in progress")]


def test_jira_variants_match_ipg_workflow():
    assert st._jira_state_variants("code review") == ["Review", "Ready to Release", "Ready to release"]
    assert st._jira_state_variants("ready to release") == ["Ready to Release", "Ready to release", "Review"]
    assert st._jira_state_variants("released") == ["Released", "Done"]
    assert st._jira_state_variants("done") == ["Done"]


def test_ready_to_release_falls_back_to_review(monkeypatch):
    calls = []
    def _run(*a, **k):
        calls.append(a)
        raise HarnessError('invalid transition\nAvailable states for issue IPG-7: \'To Do\', \'Review\'')
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.set_issue_status("IPG-7", "ready to release") is False
    tried = [c[4] for c in calls]
    assert tried == ["Ready to Release", "Review"]  # R2R fails; Review fallback tried (valid)


def test_code_review_falls_back_to_ready_to_release(monkeypatch):
    calls = []
    def _run(*a, **k):
        calls.append(a)
        raise HarnessError('invalid transition\nAvailable states for issue IPG-7: \'Ready to Release\'')
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.set_issue_status("IPG-7", "code review") is False
    tried = [c[4] for c in calls]
    assert tried == ["Review", "Ready to Release", "Ready to release"]  # Review fails; R2R fallbacks tried (valid)


def test_released_prefers_released_then_done(monkeypatch):
    calls = []
    def _run(*a, **k):
        calls.append(a)
        raise HarnessError('invalid transition\nAvailable states for issue IPG-7: \'To Do\', \'Done\'')
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.set_issue_status("IPG-7", "released") is False
    tried = [c[4] for c in calls]
    assert tried == ["Released", "Done"]  # Released not valid; Done fallback tried


def test_settle_post_review_accepts_bare_review(monkeypatch):
    seen = []
    monkeypatch.setattr(st, "set_issue_status",
                        lambda ref, state, dry_run=False: seen.append((ref, state)) or True)
    monkeypatch.setattr(st, "_issue_state", lambda *a: "review")
    assert st.settle_issue_post_review("IPG-1", "approved") == "ready to release"
    assert seen == [("IPG-1", "ready to release")]


def test_pr_review_state_gh(monkeypatch):
    def _run(*a, **k):
        if a[:3] == ("gh", "pr", "view"):
            assert "--json" in a
            return '{"state": "OPEN", "reviewDecision": "APPROVED"}'
        raise AssertionError(a)
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.pr_review_state("https://github.com/o/r/pull/5") == "approved"
