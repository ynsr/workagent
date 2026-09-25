"""Host-CLI detection (remote-aware) + persisted per-repo tool."""

from __future__ import annotations

import subprocess

import pytest

from workagent import cli, repos, store


def _git_repo(tmp_path, url):
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    subprocess.run(["git", "-C", str(d), "remote", "add", "origin", url],
                   check=True)
    return d


def _host_configs(tmp_path, monkeypatch, gh=("github.com",),
                  glab=("gitlab.com",)):
    gh_f = tmp_path / "gh-hosts.yml"
    gh_f.write_text("".join(f"{h}:\n    users:\n        u:\n" for h in gh))
    glab_f = tmp_path / "glab-config.yml"
    glab_f.write_text("hosts:\n"
                      + "".join(f"    {h}:\n        token:\n" for h in glab))
    monkeypatch.setattr(repos, "_GH_HOSTS_FILE", gh_f)
    monkeypatch.setattr(repos, "_GLAB_HOSTS_FILE", glab_f)


def _no_probe(monkeypatch):
    """Block gh/glab spawns; pass git through."""
    real = repos.subprocess.run

    def run(cmd, *a, **k):
        if cmd and cmd[0] in ("gh", "glab"):
            pytest.fail(f"CLI spawned: {cmd[0]}")
        return real(cmd, *a, **k)

    monkeypatch.setattr(repos.subprocess, "run", run)


@pytest.mark.parametrize(("url", "host"), [
    ("https://git.jibit.cloud/server/projectx.git", "git.jibit.cloud"),
    ("git@github.com:owner/repo.git", "github.com"),
    ("ssh://git@gitlab.example.com/srv/p.git", "gitlab.example.com"),
    ("https://user@github.com/o/r", "github.com"),
    ("https://github.com/o/r", "github.com"),
    ("not-a-url", None),
])
def test_remote_host_parse(url, host):
    assert repos._remote_host(url) == host


def test_glab_hosts_only_one_indent_level(tmp_path):
    f = tmp_path / "c.yml"
    f.write_text("hosts:\n"
                 "    git.jibit.cloud:\n"
                 "        token:\n"
                 "        nested: x\n"
                 "    gitlab.com:\n"
                 "other:\n"
                 "    decoy:\n")
    assert repos._known_glab_hosts(f) == {"git.jibit.cloud", "gitlab.com"}


def test_gh_hosts_top_level_keys(tmp_path):
    f = tmp_path / "h.yml"
    f.write_text("github.com:\n    user: u\ncomment\n")
    assert repos._known_gh_hosts(f) == {"github.com"}


def test_detect_uses_remote_host_not_cli_probe(tmp_path, monkeypatch):
    d = _git_repo(tmp_path, "https://git.jibit.cloud/server/projectx.git")
    _host_configs(tmp_path, monkeypatch, glab=("git.jibit.cloud",))
    _no_probe(monkeypatch)
    assert repos._detect_host_cli(d) == "glab"


def test_detect_github_remote(tmp_path, monkeypatch):
    d = _git_repo(tmp_path, "git@github.com:owner/repo.git")
    _host_configs(tmp_path, monkeypatch)
    _no_probe(monkeypatch)
    assert repos._detect_host_cli(d) == "gh"


def test_detect_unknown_host_falls_back_to_probe(tmp_path, monkeypatch):
    d = _git_repo(tmp_path, "https://forge.example.com/x.git")
    _host_configs(tmp_path, monkeypatch)
    calls = []

    def run(cmd, **k):
        calls.append(cmd[0])
        return subprocess.CompletedProcess(cmd, 0 if cmd[0] == "gh" else 1)

    monkeypatch.setattr(repos.subprocess, "run", run)
    assert repos._detect_host_cli(d) == "gh"
    assert [c for c in calls if c != "git"] == ["gh"]


def test_register_repo_seeds_tool(isolated_config, tmp_path, monkeypatch):
    d = _git_repo(tmp_path, "https://git.jibit.cloud/server/projectx.git")
    _host_configs(tmp_path, monkeypatch, glab=("git.jibit.cloud",))
    _no_probe(monkeypatch)
    repos.register_repo("proj", d)
    cfg = store.load_config()
    assert cfg["repos"]["proj"]["tool"] == "glab"
    assert cfg["repos"]["proj"]["remote"] == \
        "https://git.jibit.cloud/server/projectx.git"
    assert store.load_trackers()["gitlab:git.jibit.cloud/server/projectx"]["repos"] == [str(d)]


def test_repo_tool_reuses_persisted_entry(isolated_config, tmp_path, monkeypatch):
    d = _git_repo(tmp_path, "https://git.jibit.cloud/server/projectx.git")
    _host_configs(tmp_path, monkeypatch, glab=("git.jibit.cloud",))
    _no_probe(monkeypatch)
    repos.register_repo("proj", d)
    monkeypatch.setattr(repos, "_detect_host_cli",
                        lambda *a, **k: pytest.fail("re-detected"))
    assert cli._repo_tool(str(d)) == "glab"


def test_repo_tool_redetects_on_remote_change(isolated_config, tmp_path,
                                              monkeypatch):
    d = _git_repo(tmp_path, "https://git.jibit.cloud/server/projectx.git")
    _host_configs(tmp_path, monkeypatch, gh=("github.com",),
                  glab=("git.jibit.cloud",))
    _no_probe(monkeypatch)
    repos.register_repo("proj", d)
    subprocess.run(["git", "-C", str(d), "remote", "set-url", "origin",
                    "git@github.com:owner/repo.git"], check=True)
    assert cli._repo_tool(str(d)) == "gh"
    row = store.load_repos()["proj"]
    assert row["tool"] == "gh"
    assert row["remote"] == "git@github.com:owner/repo.git"


def test_repo_tool_unregistered_detected_not_persisted(isolated_config,
                                                       tmp_path, monkeypatch):
    d = _git_repo(tmp_path, "git@github.com:owner/repo.git")
    _host_configs(tmp_path, monkeypatch)
    _no_probe(monkeypatch)
    assert cli._repo_tool(str(d)) == "gh"
    assert store.load_config().get("repos") == {}
