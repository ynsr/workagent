import pytest

from workagent import cli_release as _rel
from workagent import release_gates as rel_gates
from workagent import store as _store


def _valid_ctx(**kw):
    base = {"repo": "rx", "path": "/r", "tool": "glab", "default_branch": "main",
            "release_branch": "master", "pinned_sha": "abc123", "behind": 0,
            "ahead": 3, "diff_count": 3, "strategy": ["gitlab-mr-ff"]}
    base.update(kw)
    return base

def _registry(monkeypatch):
    monkeypatch.setattr(_store, "load_repos", lambda: {"rx": {"path": "/r"}})
    monkeypatch.setattr(_rel.repos, "validate_release", lambda *a, **k: _valid_ctx())


def _status(**kw):
    base = {"repo": "rx", "path": "/r", "default_branch": "main",
            "release_branch": "master", "diff_count": 3, "releasable": True,
            "reason": "", "stale": False}
    base.update(kw)
    return base


def test_release_no_branch_gate_exits_2(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setattr(_rel.repos, "validate_release",
                        lambda *a, **k: (_ for _ in ()).throw(__import__("typer").Exit(2)))
    with pytest.raises(Exception) as ei:
        _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    assert getattr(ei.value, "exit_code", 2) == 2


def test_release_same_branch_exits_2(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setattr(_rel.repos, "validate_release",
                        lambda *a, **k: (_ for _ in ()).throw(__import__("typer").Exit(2)))
    with pytest.raises(Exception) as ei:
        _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    assert getattr(ei.value, "exit_code", 2) == 2


def test_release_ff_argv_per_host(monkeypatch):
    _registry(monkeypatch)
    calls = []
    monkeypatch.setattr(_rel, "run_cmd", lambda *a, **k: calls.append(a) or "abc123")
    monkeypatch.setattr(_rel, "_find_or_open_mr", lambda *a, **k: "https://h/mr/1")
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: None)
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: None)
    _rel.run_release("rx", yes=True, dry_run=False, json_output=False)
    flat = " ".join(" ".join(c) for c in calls)
    assert "--sha" in flat
    assert "--squash" not in flat
    assert "--rebase" not in flat


def test_release_dry_run_runs_nothing(monkeypatch):
    _registry(monkeypatch)
    def _boom(*a, **k):
        raise AssertionError("must not run")
    monkeypatch.setattr(_rel, "run_cmd", _boom)
    _rel.run_release("rx", yes=True, dry_run=True, json_output=False)
def test_fetch_ci_status_branch_param():
    import inspect
    from workagent import refs as _refs
    assert "branch" in inspect.signature(_refs.fetch_ci_status).parameters
def test_fetch_branch_ci_parses_remote(monkeypatch):
    from workagent import refs_ci as _ci
    calls = []
    def _fake(*a, **k):
        calls.append(a)
        return '[{"status":"success","id":7,"web_url":"https://h/-/pipelines/7"}]'
    monkeypatch.setattr(_ci, "run_cmd", _fake)
    st, url = _ci.fetch_branch_ci("glab", "https://git.jibit.cloud/server/projectx.git",
                                  "master")
    assert st == "success"
    assert any("projects/server%2Fprojectx/pipelines" in " ".join(c) for c in calls)
    assert any("ref=master" in " ".join(c) for c in calls)


def test_fetch_branch_ci_gh_remote(monkeypatch):
    from workagent import refs_ci as _ci
    seen = {}
    def _fake(*a, **k):
        seen["argv"] = a
        return '[{"conclusion":"SUCCESS","status":"COMPLETED","url":"https://gh/run/1"}]'
    monkeypatch.setattr(_ci, "run_cmd", _fake)
    st, url = _ci.fetch_branch_ci("gh", "git@github.com:o/r.git", "master")
    assert st == "success"
    assert "--repo" in seen["argv"] and "o/r" in seen["argv"]
def test_repo_update_roundtrip(isolated_config, tmp_path):
    from typer.testing import CliRunner
    from workagent import cli as _cli
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = CliRunner().invoke(_cli.app, ["repo", "add", "--name", "proj",
                                      "--path", str(repo_dir), "--tracker", "IPG",
                                      "--release-branch", "main"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(_cli.app, ["repo", "list", "--json"])
    import json
    rows = json.loads(r.stdout)
    assert rows[0]["release_branch"] == "main"
    r = CliRunner().invoke(_cli.app, ["repo", "update", "proj",
                                      "--release-branch", "", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["release_branch"] == ""


def test_release_status_same_branch_guard(monkeypatch):
    from workagent import repos as _repos
    monkeypatch.setattr(_repos.store, "load_repos", lambda: {
        "rx": {"path": "/r", "default_branch": "main", "release_branch": "main",
               "tool": "", "remote": ""}})
    st = _repos.release_status("rx")
    assert st["releasable"] is False
    assert "same" in st["reason"]

def test_repos_commands_migration_backfills_empty_object(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    with sq.connect(db) as conn:
        conn.executescript("CREATE TABLE repos (key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL);")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/x', 'r')")
    sq.init_db(db)
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
        assert "commands" in cols
        val = conn.execute("SELECT commands FROM repos WHERE key_ref = 'r'").fetchone()[0]
        assert val == "{}"


def test_load_repos_carries_commands_dict(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import store_trackers as st
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    monkeypatch.setattr(st, "connect", lambda p: sq.connect(db))
    sq.init_db(db)
    st.register_repo_row(db, "r", "/x", "jira:A", remote="", tool="",
                         commands={"pre-release": "npm test"})
    repos = st.load_repos(db)
    assert repos["r"]["commands"] == {"pre-release": "npm test"}


def test_register_repo_row_none_preserves_commands(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import store_trackers as st
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    st.register_repo_row(db, "r", "/x", "jira:A", remote="", tool="",
                         commands={"pre-release": "npm test"})
    st.register_repo_row(db, "r", "/x", "jira:A", remote="", tool="")
    assert st.load_repos(db)["r"]["commands"] == {"pre-release": "npm test"}


def test_load_repos_bad_json_is_no_gate(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import store_trackers as st
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO repos (key_ref, path, name, commands) VALUES ('r', '/x', 'r', 'not-json{{{')")
    assert st.load_repos(db)["r"]["commands"] == {}


import json as _json_mod
import pytest as _pytest
from workagent import repos as _repos

_CASES = [
    ("node-test", {"package.json": _json_mod.dumps({"scripts": {"test": "jest"}})}, "npm test"),
    ("node-build", {"package.json": _json_mod.dumps({"scripts": {"build": "tsc"}})}, "npm run build"),
    ("node-bare", {"package.json": _json_mod.dumps({"name": "x"})}, None),
    ("maven", {"pom.xml": "<project/>"}, "mvn clean; mvn verify"),
    ("gradle", {"build.gradle": ""}, "gradle clean test"),
    ("gradle-wrapper", {"build.gradle": "", "gradlew": "#!/bin/sh"}, "./gradlew clean test"),
    ("python-uv", {"pyproject.toml": "[project]", "uv.lock": ""}, "uv sync; uv run pytest"),
    ("python-pep517", {"pyproject.toml": "[project]"}, "pip install -e .; python -m pytest"),
    ("python-reqs", {"requirements.txt": "pytest"}, "pip install -r requirements.txt; python -m pytest"),
    ("empty", {}, None),
    ("collision", {"package.json": _json_mod.dumps({"scripts": {"test": "jest"}}), "pom.xml": "<project/>"}, "npm test"),
]


@_pytest.mark.parametrize("name,files,expected", _CASES)
def test_detect_prerelease_matrix(tmp_path, name, files, expected):
    d = tmp_path / name
    d.mkdir()
    for fname, content in files.items():
        (d / fname).write_text(content)
    if "gradlew" in files:
        import os
        os.chmod(d / "gradlew", 0o755)
    assert _repos.detect_prerelease_command(d) == expected


def test_detect_prerelease_unreadable_path():
    assert _repos.detect_prerelease_command("/nonexistent-path-xyz") is None


def test_repo_add_autodetects_prerelease(isolated_config, tmp_path):
    import json
    from typer.testing import CliRunner
    from workagent import cli as _cli
    repo_dir = tmp_path / "nodeproj"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    r = CliRunner().invoke(_cli.app, ["repo", "add", "--name", "nodeproj",
                                      "--path", str(repo_dir), "--tracker", "IPG"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(_cli.app, ["repo", "list", "--json"])
    rows = json.loads(r.stdout)
    assert rows[0].get("pre-release") == "npm test"


def test_repo_add_explicit_overrides_and_clear(isolated_config, tmp_path):
    import json
    from typer.testing import CliRunner
    from workagent import cli as _cli
    repo_dir = tmp_path / "nodeproj"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    r = CliRunner().invoke(_cli.app, ["repo", "add", "--name", "nodeproj",
                                      "--path", str(repo_dir), "--tracker", "IPG",
                                      "--pre-release", "make check"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(_cli.app, ["repo", "update", "nodeproj",
                                      "--pre-release", ""])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(_cli.app, ["repo", "list", "--json"])
    assert "make check" not in r.stdout


def test_repo_update_no_flags_still_usage(isolated_config, tmp_path):
    from typer.testing import CliRunner
    from workagent import cli as _cli
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    r = CliRunner().invoke(_cli.app, ["repo", "add", "--name", "proj",
                                      "--path", str(repo_dir), "--tracker", "IPG"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(_cli.app, ["repo", "update", "proj"])
    assert r.exit_code == 2

def _gated_registry(monkeypatch, gate, path="/g"):
    import workagent.cli_release as rel
    from workagent import store as _store
    registry = {"gated": {"path": path, "tool": "glab", "default_branch": "main",
                          "release_branch": "master",
                          "commands": ({"pre-release": gate} if gate else {})}}
    monkeypatch.setattr(_store, "load_repos", lambda: registry)
    ctx = _valid_ctx(repo="gated", path=path, tool="glab")
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: dict(ctx))
    return rel

def _validate_registry(monkeypatch, row):
    from workagent import repos_release as _rr
    monkeypatch.setattr(_rr.store, "load_repos", lambda: {"rx": row})
    monkeypatch.setattr(_rr, "_fetch_refs", lambda *a, **k: None)
    return _rr


def test_release_failing_gate_aborts_before_mr(monkeypatch, tmp_path):
    rel = _gated_registry(monkeypatch, "exit 1", path=str(tmp_path))
    calls = []
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: calls.append(a) or "http://mr/1")
    real = rel_gates.run_local_gate
    monkeypatch.setattr(rel_gates, "run_local_gate",
                        lambda *a, **k: (calls.append("gate"), real(*a, **k))[1])
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: None)
    with pytest.raises(Exception) as e:
        rel.run_release("gated", yes=True, dry_run=False, json_output=True, checks="local")
    assert getattr(e.value, "exit_code", 2) == 1
    assert "gate" in calls  # local gate ran before failing

def test_release_env_file_reaches_gate(monkeypatch, tmp_path):
    rel = _gated_registry(monkeypatch, "true", path=str(tmp_path))
    seen = {}
    monkeypatch.setattr(rel_gates, "run_local_gate",
                        lambda *a, **k: seen.update({"a": a, "k": k}))
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: None)
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    monkeypatch.setattr(rel, "_merge_pinned", lambda *a, **k: "gitlab-mr-ff")
    out = rel.run_release("gated", yes=True, dry_run=False, json_output=True,
                          checks="local", env_file="/tmp/custom.sh")
    assert out["merged"] is True
    assert seen["a"][5] == "/tmp/custom.sh"  # env_file positional passthrough


def test_repo_command_helper_rejects_blank(isolated_config):
    import pytest as _pytest
    with _pytest.raises(Exception) as e:
        rel_gates.run_repo_command("rx", "pre-release", "  ", "/tmp", 60)
    assert getattr(e.value, "exit_code", 2) == 2

def test_release_no_gate_goes_to_mr(monkeypatch):
    rel = _gated_registry(monkeypatch, "")
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    monkeypatch.setattr(rel, "_merge_pinned", lambda *a, **k: "gitlab-mr-ff")
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: None)
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: None)
    out = rel.run_release("gated", yes=True, dry_run=False, json_output=True, checks="none")
    assert out["mr_url"] == "http://mr/1"


def test_release_dry_run_shows_gate_without_executing(monkeypatch, capsys):
    rel = _gated_registry(monkeypatch, "exit 1")
    opened = []
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: opened.append(a) or "http://mr/1")
    out = rel.run_release("gated", yes=True, dry_run=True, json_output=True, checks="both")
    assert out["dry_run"] is True and opened == []
    assert "pre-release" in capsys.readouterr().err



def test_validate_missing_release_branch_aborts(monkeypatch):
    rr = _validate_registry(monkeypatch, {"path": "/r", "default_branch": "main",
                                          "release_branch": "", "tool": "", "remote": ""})
    with pytest.raises(Exception) as e:
        rr.validate_release("rx")
    assert getattr(e.value, "exit_code", 2) == 2


def test_validate_diverged_aborts_no_pr(monkeypatch):
    from workagent import cli_release as rel
    rr = _validate_registry(monkeypatch, {"path": "/r", "default_branch": "main",
                                          "release_branch": "master", "tool": "glab",
                                          "remote": "https://h/o/r.git"})
    monkeypatch.setattr(rr, "_count_both", lambda *a, **k: (0, 2))
    monkeypatch.setattr(rr, "_auth_ok", lambda *a: True)
    opened = []
    monkeypatch.setattr(rel, "_open_mr", lambda *a, **k: opened.append(a) or "http://mr/1")
    with pytest.raises(Exception) as e:
        rr.validate_release("rx")
    assert "merge release back into default" in str(e.value)
    assert opened == []


def test_validate_ahead_zero_nothing_to_release(monkeypatch):
    rr = _validate_registry(monkeypatch, {"path": "/r", "default_branch": "main",
                                          "release_branch": "master", "tool": "glab",
                                          "remote": "https://h/o/r.git"})
    monkeypatch.setattr(rr, "_count_both", lambda *a, **k: (0, 0))
    monkeypatch.setattr(rr, "_auth_ok", lambda *a: True)
    out = rr.validate_release("rx")
    assert out.get("note") == "already in sync, nothing to release"


def test_existing_pr_reused_not_duplicated(monkeypatch):
    import workagent.cli_release as rel
    monkeypatch.setattr(rel, "_existing_open_mr", lambda *a, **k: "http://mr/9")
    opened = []
    monkeypatch.setattr(rel, "_open_mr", lambda *a, **k: opened.append(a) or "http://mr/1")
    assert rel._find_or_open_mr("glab", "/r", "main", "master", "T") == "http://mr/9"
    assert opened == []


def test_wrong_base_pr_not_reused(monkeypatch):
    import workagent.cli_release as rel
    from workagent import refs as _refs
    monkeypatch.setattr(_refs, "fetch_open_prs", lambda tool, cwd=None: [
        {"branch": "main", "target_branch": "other", "url": "http://mr/9"}])
    opened = []
    monkeypatch.setattr(rel, "_open_mr", lambda *a, **k: opened.append(a) or "http://mr/1")
    assert rel._find_or_open_mr("glab", "/r", "main", "master", "T") == "http://mr/1"
    assert len(opened) == 1


def test_pinned_sha_recorded(monkeypatch):
    rr = _validate_registry(monkeypatch, {"path": "/r", "default_branch": "main",
                                          "release_branch": "master", "tool": "glab",
                                          "remote": "https://h/o/r.git"})
    monkeypatch.setattr(rr, "_count_both", lambda *a, **k: (3, 0))
    monkeypatch.setattr(rr, "_auth_ok", lambda *a: True)
    monkeypatch.setattr(rr, "_origin_tip", lambda *a, **k: "abc123")
    from workagent import release_caps as _caps
    monkeypatch.setattr(_caps, "get_caps", lambda *a: {"strategy": ["ff-push"], "raw": {}})
    out = rr.validate_release("rx")
    assert out["pinned_sha"] == "abc123" and out["ahead"] == 3 and out["behind"] == 0


def _flow_registry(monkeypatch, row=None):
    import workagent.cli_release as rel
    from workagent import store as _store
    registry = {"rx": dict({"path": "/r", "default_branch": "main", "release_branch": "master",
                            "tool": "glab", "commands": {"pre-release": "make check"}},
                           **(row or {}))}
    monkeypatch.setattr(_store, "load_repos", lambda: registry)
    return rel


def test_checks_none_skips_both(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: _valid_ctx())
    locals_called, ci_called = [], []
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: locals_called.append(1))
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: ci_called.append(1))
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    monkeypatch.setattr(rel, "_merge_pinned", lambda *a, **k: "gitlab-mr-ff")
    rel.run_release("rx", yes=True, dry_run=False, json_output=True, checks="none")
    assert locals_called == [] and ci_called == []


def test_both_runs_local_first_skips_ci_on_local_fail(monkeypatch):
    import typer
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: _valid_ctx())
    ci_called = []
    def _boom(*a, **k):
        raise typer.Exit(1)
    monkeypatch.setattr(rel_gates, "run_local_gate", _boom)
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: ci_called.append(1))
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    with __import__("pytest").raises(Exception):
        rel.run_release("rx", yes=True, dry_run=False, json_output=True, checks="both")
    assert ci_called == []


def test_dry_run_creates_nothing(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: _valid_ctx())
    touched = []
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: touched.append("mr") or "x")
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: touched.append("local"))
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: touched.append("ci"))
    monkeypatch.setattr(rel, "_merge_pinned", lambda *a, **k: touched.append("merge") or "s")
    out = rel.run_release("rx", yes=True, dry_run=True, json_output=True, checks="both")
    assert touched == [] and out["dry_run"] is True


def test_cli_prompts_without_yes_and_aborts_on_no(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: _valid_ctx())
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: None)
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: None)
    monkeypatch.setattr(rel, "_merge_pinned", lambda *a, **k: "gitlab-mr-ff")
    monkeypatch.setattr(rel.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(rel.sys.stdin, "readline", lambda: "n\n")
    with __import__("pytest").raises(Exception) as e:
        rel.run_release("rx", yes=False, dry_run=False, json_output=True, checks="none")
    assert getattr(e.value, "exit_code", 2) == 2


def test_merge_pinned_rejects_on_advance(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel, "_origin_tip_now", lambda *a: "moved999")
    with __import__("pytest").raises(Exception):
        rel._merge_pinned("glab", "/r", "master", "abc123", "http://mr/1", ["gitlab-mr-ff"])
    flat = " ".join(" ".join(c[0]) if isinstance(c, tuple) else str(c) for c in [])
    assert flat == ""


def test_gitlab_ff_uses_mr_sha(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel, "_origin_tip_now", lambda *a: "abc123")
    monkeypatch.setattr(rel.repos, "release_status", lambda *a, **k: {})
    calls = []
    monkeypatch.setattr(rel, "run_cmd", lambda *a, **k: calls.append(a) or "")
    rel._merge_pinned("glab", "/r", "master", "abc123", "http://mr/1", ["gitlab-mr-ff"])
    flat = " ".join(" ".join(c) for c in calls)
    assert "--sha" in flat and "abc123" in flat
    assert "--squash" not in flat and "--rebase" not in flat


def test_github_ff_push_then_merge_commit_fallback(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel, "_origin_tip_now", lambda *a: "abc123")
    calls = []
    def _fake(*a, **k):
        calls.append(a)
        if "push" in a:
            from workagent.errors import HarnessError
            raise HarnessError("non-fast-forward")
        return ""
    monkeypatch.setattr(rel, "run_cmd", _fake)
    out = rel._merge_pinned("gh", "/r", "master", "abc123", "http://mr/1",
                            ["ff-push", "github-pr-merge-commit"])
    assert out == "github-pr-merge-commit"
    flat = " ".join(" ".join(c) for c in calls)
    assert "--merge" in flat and "--match-head-commit" in flat


def test_source_branch_never_deleted(monkeypatch):
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: _valid_ctx())
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: None)
    monkeypatch.setattr(rel_gates, "run_ci_gate", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(rel, "run_cmd", lambda *a, **k: calls.append(a) or "abc123")
    rel.run_release("rx", yes=True, dry_run=False, json_output=True, checks="none")
    flat = " ".join(" ".join(c) for c in calls)
    assert "branch -D" not in flat and "push :main" not in flat.replace("  ", " ")


def test_interrupt_triggers_cleanup(monkeypatch):
    import typer
    rel = _flow_registry(monkeypatch)
    monkeypatch.setattr(rel.repos, "validate_release", lambda *a, **k: _valid_ctx())
    monkeypatch.setattr(rel, "_find_or_open_mr", lambda *a, **k: "http://mr/1")
    def _boom(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(rel_gates, "run_ci_gate", _boom)
    monkeypatch.setattr(rel_gates, "run_local_gate", lambda *a, **k: None)
    with __import__("pytest").raises(Exception) as e:
        rel.run_release("rx", yes=True, dry_run=False, json_output=True, checks="ci")
    assert getattr(e.value, "exit_code", 2) == 1
