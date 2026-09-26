"""CLI nav/misc tests: cd/open/register/repo/link/tracker/migrate (split from test_cli.py)."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import types
from datetime import datetime, timezone
from pathlib import Path
import pytest

from workagent import cli, store

from tests.cli_helpers import runner, _invoke, _link_session, _sync_mocks, _start_mocks, _init_repo, _add_wt

def test_repo_add_list_remove(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = _invoke("repo", "add", "--name", "proj", "--path", str(repo_dir),
                "--tracker", "IPG", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["registered"] == "proj"
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)[0]["name"] == "proj"
    r = _invoke("repo", "remove", "proj", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout) == {"removed": "proj"}


def test_doctor_missing_and_ok(isolated_config, tmp_path, monkeypatch):
    from workagent import doctor as _doctor
    monkeypatch.setenv("HOME", str(tmp_path))
    r = _invoke("doctor")
    assert r.exit_code == 1
    assert "doctor: missing" in r.output
    receipt = tmp_path / ".local/share/workagent/install-receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({"source_hash": _doctor.source_hash()}))
    r = _invoke("doctor")
    assert r.exit_code == 0
    assert "doctor: ok" in r.output


def test_repo_list_csv(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _invoke("repo", "add", "--name", "proj", "--path", str(repo_dir),
            "--tracker", "IPG")
    r = _invoke("repo", "list", "--csv")
    assert r.exit_code == 0
    lines = [l for l in r.output.strip().splitlines() if l]
    assert lines[0] == "name,path,trackers"
    assert len(lines) == 2 and lines[1].startswith("proj,")


def test_link_set_list_remove_roundtrip(isolated_config, tmp_path):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = _invoke("link", "set", "IPG", str(repo_dir), "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["tracker"] == "jira:IPG"
    r = _invoke("link", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout)["trackers"]["jira:IPG"]["repos"] == [str(repo_dir.resolve())]
    r = _invoke("link", "remove", "jira:IPG", "--json")
    assert r.exit_code == 0
    assert json.loads(r.stdout) == {"removed": "jira:IPG"}


def test_version(isolated_config):
    r = _invoke("--version")
    assert r.exit_code == 0
    assert "workagent" in r.output


def test_help_shows_examples_and_exit_codes(isolated_config):
    r = _invoke("--help")
    assert r.exit_code == 0
    assert "Exit codes" in r.output
    assert "workagent start" in r.output


def test_no_runtime_shorthand_N_on_start_and_review(isolated_config, tmp_path, monkeypatch):
    """`-N` is accepted as shorthand for --no-runtime on both subcommands."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_issue", lambda ref: {"title": "t", "body": "b"})
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/33"})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["start", "o/r#33", "--no-tty", "-N", "--json"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/33",
                                 "--no-tty", "-N", "--json"])
    assert r2.exit_code == 0, r2.output
    assert launched == []
    assert json.loads(r.stdout)["runtime_command"].startswith("cd ")
    assert json.loads(r2.stdout)["runtime_command"].startswith("cd ")


def test_base_completion_lists_cwd_git_branches(isolated_config, tmp_path, monkeypatch):
    """--base completion: cwd local branches filtered by prefix; [] outside a repo."""
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=repo, check=True)
    for name in ("feat/login", "chore/cicd"):
        subprocess.run(["git", "branch", name], cwd=repo, check=True)
    callback = cli._complete_branches
    monkeypatch.chdir(repo)
    assert callback(None, "") == ["chore/cicd", "feat/login", "main"]
    assert callback(None, "feat/") == ["feat/login"]
    assert callback(None, "zzz") == []
    monkeypatch.chdir(tmp_path)  # not a git repo
    assert callback(None, "") == []


def test_cd_prints_worktree(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "wt"
    wt_dir.mkdir(); subprocess.run(["git", "init", "-q", str(wt_dir)], check=True)
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("cd", "IPG-929", "--json")
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data == {"key": "jira:IPG-929", "worktree": str(wt_dir)}
    r = _invoke("cd", "IPG-929")
    assert r.stdout.strip() == str(wt_dir)


def test_cd_missing_worktree_errors(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"; wt_dir = tmp_path / "gone"
    _link_session(repo_dir, wt_dir, monkeypatch)
    r = _invoke("cd", "IPG-929")
    assert r.exit_code == 1


def test_cd_unknown_ref(isolated_config):
    r = _invoke("cd", "NOPE-1")
    assert r.exit_code == 2


def test_cd_wrapper_snippets():
    from workagent import completions as c
    w = c.cd_wrapper("workagent", "bash")
    assert w.startswith("workagent-cd()") and 'cd "$(command workagent cd' in w
    assert "function workagent-cd" in c.cd_wrapper("workagent", "fish")
    snip = c.install_snippet("workagent", "bash")
    assert "workagent-cd()" in snip


def test_open_resolves_and_opens(isolated_config, tmp_path, monkeypatch):
    # Popen is faked, which also breaks subprocess.run inside
    # is_valid_worktree — stub the validity check instead.
    wt = tmp_path / "wt"
    store.record_link("jira:IPG-929", {"issue": "IPG-929", "worktree": str(wt),
                                       "branch": "feat/IPG-929--x", "repo": str(tmp_path)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)
    opened = []

    class FakePopen:
        def __init__(self, argv, **kw):
            opened.append(argv)
            kwargs.update(kw)

    kwargs = {}
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    r = _invoke("open", "IPG-929")
    assert r.exit_code == 0, r.output
    assert r.stdout.strip() == str(wt)
    assert opened, "no opener spawned"
    assert opened[0][0] in ("xdg-open", "open", "explorer")
    assert opened[0][1] == str(wt)
    assert kwargs["stdin"] == kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_open_refuses_invalid_worktree(isolated_config, tmp_path):
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": str(tmp_path / "gone"),
                                     "branch": "feat/1", "repo": str(tmp_path)})
    r = _invoke("open", "IPG-1")
    assert r.exit_code == 1
    assert "jira:IPG-1" in r.output


def test_open_refuses_missing_link(isolated_config):
    r = _invoke("open", "NOPE-1")
    assert r.exit_code == 2
    assert "no linked state" in r.output


def test_open_no_opener_available(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    store.record_link("jira:IPG-1", {"issue": "IPG-1", "worktree": str(wt),
                                     "branch": "feat/1", "repo": str(tmp_path)})
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda path: True)

    def boom(argv, **kw):
        raise FileNotFoundError("xdg-open")

    monkeypatch.setattr(cli.subprocess, "Popen", boom)
    r = _invoke("open", "IPG-1")
    assert r.exit_code == 1
    assert "no opener available" in r.output


def test_register_registers_by_path(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "feat/IPG-777--jira-linking")
    r = _invoke("register", str(wt), "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["key"] == "jira:IPG-777"
    assert data["worktree"] == str(wt)
    assert data["branch"] == "feat/IPG-777--jira-linking"
    assert data["repo"] == str(repo)
    entry = store.lookup_link("jira:IPG-777")
    assert entry is not None and entry["worktree"] == str(wt)


def test_register_defaults_to_branch_key(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "plain-named-branch")
    r = _invoke("register", str(wt), "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["key"] == "branch:plain-named-branch"


def test_register_issue_ref_sets_key_and_url(isolated_config, tmp_path, monkeypatch):
    monkeypatch.setattr(cli.refs, "jira_site", lambda: "https://jira.example.com")
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "some-branch")
    r = _invoke("register", str(wt), "--issue", "IPG-555", "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["key"] == "jira:IPG-555"
    assert data["issue_url"] == "https://jira.example.com/browse/IPG-555"
    assert store.lookup_link("jira:IPG-555")["branch"] == "some-branch"


def test_register_persists_tracker_repo_relation(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "b")
    r = _invoke("register", str(wt), "--issue", "owner/repo#12",
                "--yes", "--json")
    assert r.exit_code == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["key"] == "github:owner/repo#12"
    rels = store.load_config().get("trackers", {})
    assert str(repo) in rels.get("github:owner/repo", {}).get("repos", [])


def test_register_idempotent_same_worktree(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "feat/IPG-777--jira-linking")
    _invoke("register", str(wt), "--json")
    r = _invoke("register", str(wt), "--json")
    assert r.exit_code == 0
    assert "already registered" in r.stderr


def test_register_conflicting_key_needs_force(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt1 = _add_wt(repo, "wt1", "feat/IPG-777--jira-linking")
    wt2 = _add_wt(repo, "wt2", "feat/IPG-778--other")
    assert _invoke("register", str(wt1), "--json").exit_code == 0
    r = _invoke("register", str(wt2), "--key", "jira:IPG-777", "--json")
    assert r.exit_code == 1
    assert "--force" in r.stderr
    r = _invoke("register", str(wt2), "--key", "jira:IPG-777", "--force",
                "--json")
    assert r.exit_code == 0
    assert store.lookup_link("jira:IPG-777")["worktree"] == str(wt2)


def test_register_rejects_main_checkout(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    r = _invoke("register", str(repo), "--json")
    assert r.exit_code == 2
    assert "main checkout" in r.stderr


def test_register_rejects_non_git_dir(isolated_config, tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    r = _invoke("register", str(d), "--json")
    assert r.exit_code == 2


def test_register_missing_dir(isolated_config, tmp_path):
    r = _invoke("register", str(tmp_path / "nope"), "--json")
    assert r.exit_code == 2


def test_register_bad_issue_ref(isolated_config, tmp_path):
    repo = _init_repo(tmp_path / "proj")
    wt = _add_wt(repo, "wt", "b")
    r = _invoke("register", str(wt), "--issue", "not a ref", "--json")
    assert r.exit_code == 2
    assert "cannot parse" in r.stderr


def test_migrate_command(isolated_config, tmp_path):
    import json
    d = Path(str(isolated_config))
    (d / "links.json").write_text(json.dumps({
        "jira:IPG-1": {"worktree": "/wt", "branch": "feat/1",
                       "repo": "/r"}}))
    r = _invoke("migrate", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["migrated"]["worktrees"] == 1
    assert not (d / "links.json").exists()


def test_migrate_roundtrip_uses_sqlite(isolated_config):
    import json
    d = Path(str(isolated_config))
    store.record_link("jira:IPG-7", {"worktree": "/wt7", "branch": "feat/7",
                                     "repo": "/r", "reviewed": True})
    store.cache_pr_status("feat/7", {"number": 7}, tool="gh")
    r = _invoke("migrate", "--json")
    assert r.exit_code == 0, r.output
    # Legacy files gone; reads now served from SQLite with extras preserved.
    assert not (d / "links.json").exists()
    assert not (d / "pr_cache.json").exists()
    assert store.load_links()["jira:IPG-7"]["reviewed"] is True
    assert store.load_pr_cache()["feat/7"]["tool"] == "gh"
    # Writes after cutover persist in SQLite, not JSON.
    store.record_link("jira:IPG-8", {"worktree": "/wt8", "branch": "feat/8",
                                     "repo": "/r"})
    assert "jira:IPG-8" in store.load_links()
    assert not (d / "links.json").exists()


def test_repo_add_derives_tracker_from_github_remote(isolated_config, tmp_path, monkeypatch):
    """--tracker omitted: GitHub origin → github:O/R mapping (issue #19)."""
    import subprocess
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    subprocess.run(["git", "-C", str(repo_dir), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin",
                    "https://github.com/owner/repo.git"], check=True)
    monkeypatch.setattr(cli.repos, "_detect_host_cli", lambda path: "gh")
    r = _invoke("repo", "add", "--name", "p", "--path", str(repo_dir), "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["tracker"] == "github:owner/repo"
    assert store.load_config()["trackers"]["github:owner/repo"]["repos"] == [str(repo_dir.resolve())]


def test_repo_add_no_remote_still_needs_tracker(isolated_config, tmp_path):
    r = _invoke("repo", "add", "--name", "p", "--path", str(tmp_path))
    assert r.exit_code == 2
    assert "--tracker" in r.output
    # Failure path leaves no half-registered repo (review catch).
    assert store.load_config().get("repos", {}) == {}


def test_repo_list_shows_tracker(isolated_config, tmp_path):
    _invoke("repo", "add", "--name", "p", "--path", str(tmp_path),
            "--tracker", "IPG")
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0, r.output
    items = json.loads(r.output)
    assert items[0]["trackers"] == "jira:IPG"


def test_repo_list_prefers_host_scoped_tracker(isolated_config, tmp_path):
    """A repo under both jira:PREFIX and its remote-derived host id shows
    the host-scoped one (the harness symptom: jira:IPG won over
    github:ynsr/harness by first-write order)."""
    from workagent import store
    cfg = store.load_config()
    cfg["repos"] = {"h": {"path": str(tmp_path)}}
    cfg["trackers"] = {"jira:IPG": {"repos": [str(tmp_path)]},
                       "github:o/r": {"repos": [str(tmp_path)]}}
    store.save_config(cfg)
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["trackers"] == "github:o/r"


def test_tracker_add_list_remove_roundtrip(isolated_config):
    """tracker add persists vendor/remote_url; list shows the count; remove cascades."""
    from workagent import store_sqlite as _sq
    _sq.init_db(_sq.db_path())
    r = _invoke("tracker", "add", "IPG", "--remote-url", "https://jira.example/browse/IPG", "--json")
    assert r.exit_code == 0, r.output
    row = json.loads(r.stdout)
    assert row["key"] == "jira:IPG" and row["vendor"] == "jira"
    r = _invoke("tracker", "list", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)[0]["repos"] == 0
    r = _invoke("tracker", "remove", "IPG", "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout) == {"removed": "jira:IPG"}


def test_tracker_add_sets_explicit_vendor(isolated_config):
    """tracker add --vendor (enum) beats key derivation and survives ensures."""
    from workagent import store_sqlite as sq
    sq.init_db(sq.db_path())
    r = _invoke("tracker", "add", "IPG", "--vendor", "jira",
                "--remote-url", "https://x", "--json")
    assert r.exit_code == 0, r.output
    db = sq.db_path()
    sq.add_tracker_repo(db, "jira:IPG", "/r")
    rows = sq.load_tracker_rows(db)
    assert (rows[0]["vendor"], rows[0]["remote_url"]) == ("jira", "https://x")


def test_tracker_add_rejects_non_enum_vendor(isolated_config):
    """--vendor outside the jira/github enum is a usage error (exit 2)."""
    r = _invoke("tracker", "add", "IPG", "--vendor", "Custom", "--json")
    assert r.exit_code == 2
    assert "must be one of jira, github" in r.output


def test_repo_remove_guard_and_force(isolated_config, tmp_path):
    """repo remove refuses while linked worktrees exist; --force cascades."""
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.register_repo_row(db, "proj", str(tmp_path / "proj"), "jira:IPG")
    sq.register_repo_row(db, "lean", str(tmp_path / "lean"), "jira:IPG")
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                     " VALUES ('k', '/wt', 'b', 'proj', '2026-01-01T00:00:00+00:00')")
    r = _invoke("repo", "remove", "proj")
    assert r.exit_code == 2
    assert "linked worktree" in r.output
    with sq.connect(db) as conn:  # untouched
        assert conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] == 2
    r = _invoke("repo", "remove", "lean")
    assert r.exit_code == 0
    r = _invoke("repo", "remove", "proj", "--force")
    assert r.exit_code == 0
    with sq.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 0
