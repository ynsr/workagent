"""issue_comments.post_issue_comment: tracker dispatch."""
from workagent import issue_comments as ic


def test_jira_posts_via_add_comment(monkeypatch):
    seen = []
    monkeypatch.setattr(ic, "run_cmd", lambda *a, **k: seen.append(a) or "")
    assert ic.post_issue_comment("https://tribe.jibit.cloud/browse/IPG-1", "did things") is True
    assert seen[0][:4] == ("jira", "issue", "comment", "add")


def test_gh_posts_via_issue_comment(monkeypatch):
    seen = []
    monkeypatch.setattr(ic, "run_cmd", lambda *a, **k: seen.append(a) or "")
    url = "https://github.com/o/r/issues/22"
    assert ic.post_issue_comment(url, "did things") is True
    assert seen[0][:3] == ("gh", "issue", "comment")
    assert "did things" in seen[0]


def test_glab_posts_via_issue_note(monkeypatch):
    seen = []
    monkeypatch.setattr(ic, "run_cmd", lambda *a, **k: seen.append(a) or "")
    url = "https://git.jibit.cloud/g/r/-/issues/430"
    assert ic.post_issue_comment(url, "did things") is True
    assert seen[0][:3] == ("glab", "issue", "note")
    assert "did things" in seen[0]


def test_pr_url_refused_and_failure_warns(monkeypatch, capsys):
    assert ic.post_issue_comment("https://github.com/o/r/pull/33", "x") is False
    def _boom(*a, **k):
        from workagent.errors import HarnessError
        raise HarnessError("nope")
    monkeypatch.setattr(ic, "run_cmd", _boom)
    assert ic.post_issue_comment("https://github.com/o/r/issues/1", "x") is False
    assert "warning" in capsys.readouterr().err
