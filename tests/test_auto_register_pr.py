"""Tests for issue #189: auto-register worktree for PR/MR ref before flow/action execution."""
from __future__ import annotations

from pathlib import Path
from typer.testing import CliRunner

from workagent import cli as _cli  # noqa: F401
from workagent import cli_review, refs, store, trackers, worktrees
from workagent.cli_core import app
from workagent.errors import HarnessError


MR_URL = "https://git.jibit.cloud/server/projectx/-/merge_requests/1824"
PR_URL = "https://github.com/ynsr/workagent/pull/42"


def test_derive_issue_url_variants(monkeypatch):
    monkeypatch.setattr(refs, "jira_site", lambda: "https://tribe.jibit.cloud")
    assert (
        refs.derive_issue_url(
            "feat/other",
            {"title": "Fix", "body": "See https://tribe.jibit.cloud/browse/IPG-1076."},
        )
        == "https://tribe.jibit.cloud/browse/IPG-1076"
    )
    assert (
        refs.derive_issue_url(
            "feat/other",
            {"title": "Fix", "body": "Closes #42"},
            {"tool": "gh", "repo": "ynsr/workagent"},
        )
        == "https://github.com/ynsr/workagent/issues/42"
    )
    assert (
        refs.derive_issue_url("feat/IPG-1076--terminal-priority", {"title": "x", "body": ""})
        == "https://tribe.jibit.cloud/browse/IPG-1076"
    )
    assert refs.derive_issue_url("feat/no-issue", {"title": "plain", "body": ""}) == ""


def test_resolve_worktree_matches_pr_url():
    links = {
        "ao/terminal-priority/ipg-1076": {
            "worktree": "/tmp/wt-1076",
            "branch": "ao/terminal-priority/ipg-1076",
            "pr_url": MR_URL,
            "issue_url": "https://tribe.jibit.cloud/browse/IPG-1076",
        }
    }
    assert worktrees.resolve_worktree(MR_URL, links) == "ao/terminal-priority/ipg-1076"
    assert worktrees.resolve_worktree(MR_URL + "/", links) == "ao/terminal-priority/ipg-1076"


def _stub_pr_env(monkeypatch, tmp_path: Path, *, head_ref: str = "ao/terminal-priority/ipg-1076",
                 title: str = "[IPG-1076] Terminal priority", body: str = ""):
    repo_dir = tmp_path / "projectx"
    repo_dir.mkdir(exist_ok=True)
    wt_dir = tmp_path / "wt" / head_ref
    wt_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(refs, "jira_site", lambda: "https://tribe.jibit.cloud")
    monkeypatch.setattr(
        trackers,
        "resolve_repo_for_ref",
        lambda parsed, repo, cwd, depth=7, yes=False, persist=True, pinned="": (
            repo_dir, "main", "gitlab:git.jibit.cloud/server/projectx", "existing"
        ),
    )
    monkeypatch.setattr(cli_review.repos, "default_branch", lambda p: "main")
    monkeypatch.setattr(cli_review.repos, "register_repo", lambda *a, **k: None)
    monkeypatch.setattr(
        refs,
        "fetch_pr_info",
        lambda parsed, cwd=None: {"title": title, "head_ref": head_ref, "body": body},
    )
    start_calls = []

    def _fake_start(rdir, branch, base):
        start_calls.append((rdir, branch, base))
        return {"worktree_path": str(wt_dir), "branch": branch}

    monkeypatch.setattr(cli_review.gitwt, "start_worktree", _fake_start)
    monkeypatch.setattr(worktrees, "is_valid_worktree", lambda p: Path(p).is_dir())
    return repo_dir, wt_dir, start_calls


def test_auto_register_creates_and_adopts_without_duplicate(isolated_config, tmp_path, monkeypatch):
    _repo_dir, wt_dir, start_calls = _stub_pr_env(monkeypatch, tmp_path)
    parsed = refs.parse_ref(MR_URL)

    key1, entry1 = cli_review.auto_register_pr_worktree(MR_URL, parsed, yes=True)
    assert key1 == "ao/terminal-priority/ipg-1076"
    assert entry1["worktree"] == str(wt_dir)
    assert entry1["pr_url"] == MR_URL
    assert entry1["issue_url"] == "https://tribe.jibit.cloud/browse/IPG-1076"
    assert len(start_calls) == 1

    # Re-running adopts existing worktree without calling start_worktree again.
    links = store.load_links()
    k_res, _, _ = worktrees.resolve_any(MR_URL, links)
    assert k_res == key1
    key2, _entry2 = cli_review.auto_register_pr_worktree(MR_URL, parsed, yes=True)
    assert key2 == key1
    assert len(start_calls) == 1


def test_auto_register_propagates_head_ref_error(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "projectx"
    repo_dir.mkdir()
    monkeypatch.setattr(
        trackers,
        "resolve_repo_for_ref",
        lambda *a, **k: (repo_dir, "main", "gitlab:git.jibit.cloud/server/projectx", "existing"),
    )
    monkeypatch.setattr(cli_review.repos, "default_branch", lambda p: "main")
    monkeypatch.setattr(
        refs,
        "fetch_pr_info",
        lambda parsed, cwd=None: (_ for _ in ()).throw(HarnessError("branch gone", exit_code=2)),
    )
    try:
        cli_review.auto_register_pr_worktree(MR_URL, refs.parse_ref(MR_URL), yes=True)
    except HarnessError as e:
        assert "branch gone" in str(e)
    else:
        raise AssertionError("expected HarnessError")


def test_flow_run_auto_registers_mr_url_and_no_auto_register_fails(isolated_config, tmp_path, monkeypatch):
    from workagent import cli_flow as _cf

    _repo_dir, wt_dir, start_calls = _stub_pr_env(monkeypatch, tmp_path)
    store.create_template(
        name="Review Fix",
        label="review_fix",
        steps=[{"kind": "review", "config": {"run_mode": "preview"}}],
    )
    seen_keys = []
    monkeypatch.setattr(
        _cf,
        "_run_review_step",
        lambda key, config, fix_comments, json_output: (
            seen_keys.append(key),
            {"status": "done", "key": key},
        )[1],
    )

    runner = CliRunner()
    # --no-auto-register fails fast with no linked state (never ambiguous ref '')
    res_no = runner.invoke(
        app, ["flow", "run", "review_fix", MR_URL, "--yes", "--no-auto-register"]
    )
    assert res_no.exit_code == 2
    assert "no linked state" in res_no.output
    assert len(start_calls) == 0

    # Default auto-registers, links pr_url + issue_url, and runs flow with non-empty key
    res_ok = runner.invoke(app, ["flow", "run", "review_fix", MR_URL, "--yes", "--json"])
    assert res_ok.exit_code == 0, res_ok.output
    assert seen_keys == ["ao/terminal-priority/ipg-1076"]
    assert len(start_calls) == 1
    links = store.load_links()
    assert links["ao/terminal-priority/ipg-1076"]["pr_url"] == MR_URL
    assert links["ao/terminal-priority/ipg-1076"]["issue_url"] == "https://tribe.jibit.cloud/browse/IPG-1076"

    # Re-running adopts the existing worktree (no second start_worktree)
    res_again = runner.invoke(app, ["flow", "run", "review_fix", MR_URL, "--yes", "--fresh", "--json"])
    assert res_again.exit_code == 0, res_again.output
    assert len(start_calls) == 1
    assert seen_keys == ["ao/terminal-priority/ipg-1076", "ao/terminal-priority/ipg-1076"]


def test_action_run_and_adhoc_auto_register_and_opt_out(isolated_config, tmp_path, monkeypatch):
    from workagent import cli_action as _ca
    from workagent import cli_adhoc as _cad

    _repo_dir, wt_dir, start_calls = _stub_pr_env(monkeypatch, tmp_path)
    store.create_action(name="Check", label="chk", prompt="Check it.")
    action_keys = []
    adhoc_keys = []
    monkeypatch.setattr(
        _ca,
        "_run_harness",
        lambda *a, **k: (action_keys.append(k.get("run_key")), {"status": "ok"})[1],
    )
    monkeypatch.setattr(
        _cad,
        "_run_harness",
        lambda *a, **k: (adhoc_keys.append(k.get("run_key")), {"status": "ok"})[1],
    )
    monkeypatch.setattr(_cad, "_guard_harness", lambda *a, **k: None)

    runner = CliRunner()
    # --no-auto-register fails fast on action run
    res_act_no = runner.invoke(app, ["action", "run", "chk", MR_URL, "--yes", "--no-auto-register"])
    assert res_act_no.exit_code == 2
    assert "no linked state" in res_act_no.output

    # action run auto-registers
    res_act = runner.invoke(app, ["action", "run", "chk", MR_URL, "--yes"])
    assert res_act.exit_code == 0, res_act.output
    assert action_keys == ["ao/terminal-priority/ipg-1076"]
    assert len(start_calls) == 1

    # adhoc adopts the registered worktree (and --no-auto-register skips unlinked)
    res_adhoc = runner.invoke(app, ["adhoc", MR_URL, "--prompt", "hi", "--yes"])
    assert res_adhoc.exit_code == 0, res_adhoc.output
    assert adhoc_keys == ["ao/terminal-priority/ipg-1076"]
    assert len(start_calls) == 1

    res_adhoc_no = runner.invoke(
        app, ["adhoc", PR_URL, "--prompt", "hi", "--yes", "--no-auto-register"]
    )
    assert res_adhoc_no.exit_code == 1


def test_sync_no_auto_register_fails_fast(isolated_config, tmp_path, monkeypatch):
    _stub_pr_env(monkeypatch, tmp_path)
    runner = CliRunner()
    res = runner.invoke(app, ["sync", MR_URL, "--yes", "--no-auto-register"])
    assert res.exit_code == 2
    assert "no linked state" in res.output
