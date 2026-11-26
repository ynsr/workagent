from workagent import cli_repo_util, env, release_caps, repos, web_runs_routes


def test_release_caps_returns_isolated_copy(monkeypatch):
    release_caps._CACHE.clear()
    monkeypatch.setattr(
        release_caps,
        "_gh_caps",
        lambda *a, **kw: {"allow_merge_commit": True, "linear_required": False, "pr_required": False},
    )
    first = release_caps.get_caps("gh", "/r", "release")
    first["strategy"].append("mutated")
    second = release_caps.get_caps("gh", "/r", "release")
    assert second["strategy"] == ["ff-push", "github-pr-merge-commit"]


def test_release_caps_does_not_cache_non_auth_gh_protection_error(monkeypatch):
    release_caps._CACHE.clear()
    calls = 0

    def _run_cmd(*args, **kwargs):
        nonlocal calls
        calls += 1
        if "branches/" in " ".join(str(a) for a in args):
            raise release_caps.HarnessError("network timeout")
        return '{"allow_merge_commit": true}'

    monkeypatch.setattr(release_caps, "_origin_remote", lambda _p: "git@github.com:o/r.git")
    monkeypatch.setattr(release_caps, "run_cmd", _run_cmd)
    try:
        release_caps.get_caps("gh", "/r", "release")
        assert False, "expected HarnessError on branch protection probe failure"
    except release_caps.HarnessError:
        pass
    assert ("gh", "/r", "release") not in release_caps._CACHE


def test_no_tool_dirs_expires_after_ttl(monkeypatch, tmp_path):
    repos._NO_TOOL_DIRS.clear()
    now = [100.0]
    monkeypatch.setattr(repos.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(repos, "remote_url", lambda _p: "")
    assert repos._detect_host_cli(tmp_path) is None
    assert str(tmp_path) in repos._NO_TOOL_DIRS
    now[0] = 500.0  # past 300s TTL
    monkeypatch.setattr(repos, "remote_url", lambda _p: "git@github.com:o/r.git")
    monkeypatch.setattr(repos, "_known_gh_hosts", lambda: {"github.com"})
    assert repos._detect_host_cli(tmp_path) == "gh"


def test_db_cache_expires_negative_entry_after_60s(monkeypatch):
    cli_repo_util._DB_CACHE.clear()
    now = [100.0]
    monkeypatch.setattr(cli_repo_util.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        cli_repo_util.repos,
        "default_branch",
        lambda _p: (_ for _ in ()).throw(cli_repo_util.HarnessError("transient")),
    )
    assert cli_repo_util._repo_default_branch("/repo/x") is None
    now[0] = 200.0  # past 60s negative TTL
    monkeypatch.setattr(cli_repo_util.repos, "default_branch", lambda _p: "main")
    assert cli_repo_util._repo_default_branch("/repo/x") == "main"


def test_env_sourced_cache_expires_after_ttl(monkeypatch, tmp_path):
    env._SOURCED_CACHE.clear()
    ef = tmp_path / "env.sh"
    ef.write_text("export FOO_TEST=1\n")
    now = [100.0]
    monkeypatch.setattr(env.time, "monotonic", lambda: now[0])
    first = env.sourced_env(ef)
    assert first.get("FOO_TEST") == "1"
    # Even without mtime change on env.sh, TTL expiry re-sources
    calls = 0
    orig_run = __import__("subprocess").run

    def _spy(*a, **kw):
        nonlocal calls
        calls += 1
        return orig_run(*a, **kw)

    monkeypatch.setattr(__import__("subprocess"), "run", _spy)
    assert env.sourced_env(ef).get("FOO_TEST") == "1"
    assert calls == 0
    now[0] = 200.0  # past 60s default TTL
    assert env.sourced_env(ef).get("FOO_TEST") == "1"
    assert calls == 1


def test_options_cache_returns_isolated_deepcopy_and_falls_back_on_error(monkeypatch):
    web_runs_routes._OPTIONS_CACHE.clear()
    from types import SimpleNamespace
    from workagent import backend

    class _FakeHarness:
        supports_acp = True
        calls = 0

        def discover_options(self):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    models=[{"id": "m1", "name": "M1"}],
                    efforts=[{"id": "high", "name": "High"}],
                    model_efforts={"m1": [{"id": "high", "name": "High"}]},
                    current_model="m1",
                    current_effort="high",
                )
            raise RuntimeError("acp crashed")

    fake = _FakeHarness()
    monkeypatch.setattr(backend, "_load_harnesses", lambda: None)
    monkeypatch.setitem(backend.HARNESSES, "fake_acp", fake)
    body1, cached1, warn1 = web_runs_routes._cached_harness_options("fake_acp")
    assert cached1 is False and warn1 is None
    body1["models"].append({"id": "mutated", "name": "Mutated"})
    body2, cached2, _ = web_runs_routes._cached_harness_options("fake_acp")
    assert cached2 is True
    assert len(body2["models"]) == 1
    # Refresh fails -> falls back to last-known cached options with warning
    body3, cached3, warn3 = web_runs_routes._cached_harness_options("fake_acp", refresh=True)
    assert len(body3["models"]) == 1
    assert warn3 is not None and "acp crashed" in warn3
