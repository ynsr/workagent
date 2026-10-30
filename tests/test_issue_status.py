"""issue_status: tracker status transitions + release discovery."""
from workagent import issue_status as st
from workagent.errors import HarnessError


def _jira_ok(monkeypatch, tid="11"):
    def _run(*a, **k):
        if a[:3] == ("jira-cli", "issue", "IPG-1") and a[3:] == ("--format", "json"):
            return '{"transitions": [{"id": "11", "name": "Start", "to": {"name": "In Progress"}}]}'
        if a[:4] == ("jira-cli", "issue", "IPG-1", "transition"):
            assert a[4] == tid
            return ""
        raise AssertionError(a)
    monkeypatch.setattr(st, "run_cmd", _run)


def test_jira_discovers_transition(monkeypatch):
    seen = []
    def _run(*a, **k):
        seen.append(a)
        if a[3:] == ("--format", "json"):
            return '{"transitions": [{"id": "21", "to": {"name": "Code Review"}}]}'
        return ""
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.set_issue_status("https://example.com/browse/IPG-7", "code review") is True
    assert seen[1][:5] == ("jira-cli", "issue", "IPG-7", "transition", "21")

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


def test_pr_review_state_gh(monkeypatch):
    def _run(*a, **k):
        if a[:3] == ("gh", "pr", "view"):
            assert "--json" in a
            return '{"state": "OPEN", "reviewDecision": "APPROVED"}'
        raise AssertionError(a)
    monkeypatch.setattr(st, "run_cmd", _run)
    assert st.pr_review_state("https://github.com/o/r/pull/5") == "approved"
