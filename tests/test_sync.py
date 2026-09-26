"""sync engine: dirty check, local merge, CHANGELOG auto-resolve, push, rebase."""

from __future__ import annotations

import subprocess
from pathlib import Path


from workagent import sync


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _init_repo(path: Path, default: str = "main") -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", "-b", default, cwd=path)
    _git("config", "user.email", "t@t", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    return path


def _commit(path: Path, files: dict[str, str], msg: str) -> None:
    for name, content in files.items():
        p = Path(path) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", msg, cwd=path)


def _bare_origin(tmp_path: Path, default: str = "main") -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", default, str(origin)],
                   check=True, capture_output=True)
    return origin


def _seed_and_clone(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path]:
    """Bare origin + working clone with an initial commit pushed."""
    origin = _bare_origin(tmp_path)
    seed = tmp_path / "seed"
    _git("clone", "-q", str(origin), str(seed), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=seed)
    _git("config", "user.name", "t", cwd=seed)
    _commit(seed, files, "c0")
    _git("push", "-q", "origin", "main", cwd=seed)
    wt = tmp_path / "wt"
    _git("clone", "-q", str(origin), str(wt), cwd=tmp_path)
    _git("config", "user.email", "t@t", cwd=wt)
    _git("config", "user.name", "t", cwd=wt)
    return origin, wt


def _push_from_sibling(tmp_path, wt: Path, files: dict[str, str]) -> None:
    """Advance origin/main from a sibling clone (origin is bare)."""
    other = tmp_path / "other"
    if not other.exists():
        _git("clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        _git("config", "user.email", "t@t", cwd=other)
        _git("config", "user.name", "t", cwd=other)
    _commit(other, files, "on-main")
    _git("push", "-q", "origin", "main", cwd=other)
    return other


def test_dirty_files(tmp_path):
    wt = _init_repo(tmp_path / "wt")
    _commit(wt, {"a.txt": "a\n", "b.txt": "b\n"}, "c0")
    (wt / "a.txt").write_text("dirty\n")
    (wt / "c.txt").write_text("untracked\n")
    assert sync.dirty_files(wt) == ["a.txt", "c.txt"]


def test_dirty_files_empty(tmp_path):
    wt = _init_repo(tmp_path / "clean")
    _commit(wt, {"a.txt": "a\n"}, "c0")
    assert sync.dirty_files(wt) == []


def test_local_merge_up_to_date(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    out = sync.local_merge(wt, "main")
    assert out["status"] == "up-to-date"


def test_local_merge_fast_forward(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    _git("checkout", "-q", "-b", "feat/x", cwd=wt)
    _commit(wt, {"feat.txt": "f\n"}, "feat")
    _push_from_sibling(tmp_path, wt, {"main.txt": "m\n"})
    out = sync.local_merge(wt, "main")
    assert out["status"] == "merged"
    assert (wt / "main.txt").read_text() == "m\n"
    assert (wt / "feat.txt").read_text() == "f\n"


def test_local_merge_conflict_lists_files(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"shared.txt": "base\n"})
    _git("checkout", "-q", "-b", "feat/x", cwd=wt)
    _commit(wt, {"shared.txt": "feature\n"}, "feat")
    _push_from_sibling(tmp_path, wt, {"shared.txt": "mainline\n"})
    out = sync.local_merge(wt, "main")
    assert out["status"] == "conflict"
    assert out["conflicts"] == ["shared.txt"]


CHANGELOG_OURS = """# Changelog

## Unreleased

- ours one
- ours two

## 1.0.0

- old
"""

CHANGELOG_THEIRS = """# Changelog

## Unreleased

- theirs only
- ours one

## 1.0.0

- old
"""

CHANGELOG_MERGED = """# Changelog

## Unreleased

- ours one
- ours two
- theirs only

## 1.0.0

- old
"""


def test_unreleased_union_keeps_both():
    assert sync.unreleased_union(CHANGELOG_OURS, CHANGELOG_THEIRS) == CHANGELOG_MERGED


def test_unreleased_union_rejects_other_conflicts():
    theirs = CHANGELOG_THEIRS.replace("- old", "- old changed")
    assert sync.unreleased_union(CHANGELOG_OURS, theirs) is None


def test_unreleased_union_rejects_missing_section():
    theirs = CHANGELOG_THEIRS.replace("## Unreleased", "## Changes")
    assert sync.unreleased_union(CHANGELOG_OURS, theirs) is None


CHANGELOG_BASE = CHANGELOG_OURS
CHANGELOG_THEIRS_RELEASED = """# Changelog

## Unreleased

- theirs only
- ours one

## 1.2.0 - 2026-09-20

- new release

## 1.0.0

- old
"""

CHANGELOG_RELEASED_MERGED = """# Changelog

## Unreleased

- ours one
- ours two
- theirs only

## 1.2.0 - 2026-09-20

- new release

## 1.0.0

- old
"""


def test_unreleased_union_with_base_keeps_released_sections():
    assert sync.unreleased_union(
        CHANGELOG_OURS, CHANGELOG_THEIRS_RELEASED,
        base=CHANGELOG_BASE) == CHANGELOG_RELEASED_MERGED


def test_unreleased_union_with_base_rejects_released_change():
    ours_changed = CHANGELOG_OURS.replace("- old", "- old changed")
    assert sync.unreleased_union(
        ours_changed, CHANGELOG_THEIRS_RELEASED,
        base=CHANGELOG_BASE) is None


def test_unreleased_union_without_base_still_strict():
    assert sync.unreleased_union(
        CHANGELOG_OURS, CHANGELOG_THEIRS_RELEASED) is None


def test_auto_resolve_changelog_with_released_addition(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"CHANGELOG.md": CHANGELOG_OURS})
    _git("checkout", "-q", "-b", "feat/x", cwd=wt)
    _commit(wt, {"CHANGELOG.md": CHANGELOG_OURS.replace("- ours one\n", "- ours one\n- ours two\n")}, "feat")
    _push_from_sibling(tmp_path, wt, {"CHANGELOG.md": CHANGELOG_THEIRS_RELEASED})
    out = sync.local_merge(wt, "main")
    assert out["conflicts"] == ["CHANGELOG.md"]
    assert sync.auto_resolve_changelog(wt, out["conflicts"]) is True
    assert (wt / "CHANGELOG.md").read_text() == CHANGELOG_RELEASED_MERGED
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                       cwd=str(wt), capture_output=True, text=True, check=False)
    assert r.stdout.strip() == ""


def test_auto_resolve_changelog(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"CHANGELOG.md": CHANGELOG_OURS})
    _git("checkout", "-q", "-b", "feat/x", cwd=wt)
    # Duplicate "- ours two" right after "- ours one" → same-region edit.
    _commit(wt, {"CHANGELOG.md": CHANGELOG_OURS.replace("- ours one\n", "- ours one\n- ours two\n")}, "feat")
    _push_from_sibling(tmp_path, wt, {"CHANGELOG.md": CHANGELOG_THEIRS})
    out = sync.local_merge(wt, "main")
    assert out["conflicts"] == ["CHANGELOG.md"]
    assert sync.auto_resolve_changelog(wt, out["conflicts"]) is True
    merged = (wt / "CHANGELOG.md").read_text()
    assert "- theirs only" in merged and "- ours two" in merged and "- old" in merged
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                       cwd=str(wt), capture_output=True, text=True, check=False)
    assert r.stdout.strip() == ""


def test_auto_resolve_rejects_other_files(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"a.txt": "base\n"})
    _git("checkout", "-q", "-b", "feat/x", cwd=wt)
    _commit(wt, {"a.txt": "feature\n"}, "feat")
    _push_from_sibling(tmp_path, wt, {"a.txt": "mainline\n"})
    out = sync.local_merge(wt, "main")
    assert out["conflicts"] == ["a.txt"]
    assert sync.auto_resolve_changelog(wt, out["conflicts"]) is False


def test_push(tmp_path):
    _, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    _commit(wt, {"g.txt": "2\n"}, "c1")
    sync.push(wt, "main")
    r = subprocess.run(
        ["git", "log", "--oneline", "-1", "main"],
        cwd=str(tmp_path / "origin.git"), capture_output=True, text=True, check=False)
    assert "c1" in r.stdout


def test_rebase_gh(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "run_cmd",
                        lambda *a, **k: calls.append(a) or "")
    sync.rebase_remote("gh", {"number": 12, "url": "https://github.com/o/r/pull/12"},
                       Path("/tmp/wt"))
    assert calls[0][:4] == ("gh", "pr", "update-branch", "--rebase")
    assert calls[0][-1] == "https://github.com/o/r/pull/12"


def test_rebase_glab(monkeypatch):
    calls = []
    monkeypatch.setattr(sync, "run_cmd",
                        lambda *a, **k: calls.append(a) or "")
    sync.rebase_remote("glab", {"number": 1701, "url": "https://x/mr/1701"},
                       Path("/tmp/wt"))
    assert calls[0][:3] == ("glab", "mr", "rebase")
    assert calls[0][-1] == "1701"


# ── CLI-level sync flow (auto-push, --yes harness, --all continuation) ──


def _cli_link(isolated_config, tmp_path, monkeypatch, cli, store, wt_dir):
    wt_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(wt_dir)], check=True,
                   capture_output=True)
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli, "_repo_tool", lambda repo: "glab")
    store.record_link("jira:IPG-929", {"issue": "IPG-929",
                                       "worktree": str(wt_dir),
                                       "branch": "feat/IPG-929--x",
                                       "repo": str(tmp_path / "proj")})


def test_sync_local_merge_pushes_automatically(isolated_config, tmp_path,
                                               monkeypatch):
    from workagent import cli, store
    wt_dir = tmp_path / "wt"
    _cli_link(isolated_config, tmp_path, monkeypatch, cli, store, wt_dir)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda wt, db: {"status": "merged", "conflicts": []})
    pushed = []
    monkeypatch.setattr(cli.sync_mod, "push",
                        lambda wt, br: pushed.append((str(wt), br)))
    r = cli_test_invoke("sync", "IPG-929", "--merge", "--yes", "--json")
    assert r.exit_code == 0
    import json as _json
    out = _json.loads(r.stdout)
    assert out["result"] == "merged" and out.get("pushed") is True
    assert pushed == [(str(wt_dir), "feat/IPG-929--x")]


def cli_test_invoke(*args):
    from typer.testing import CliRunner
    from workagent import cli
    return CliRunner().invoke(cli.app, list(args))


def test_sync_conflict_with_yes_runs_non_tty_harness(isolated_config,
                                                     tmp_path, monkeypatch):
    from workagent import cli, store
    wt_dir = tmp_path / "wt"
    _cli_link(isolated_config, tmp_path, monkeypatch, cli, store, wt_dir)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda wt, db: {"status": "conflict",
                                        "conflicts": ["a.txt"]})
    monkeypatch.setattr(cli.sync_mod, "auto_resolve_changelog",
                        lambda wt, c: False)
    launched = []
    monkeypatch.setattr(cli, "_run_harness",
                        lambda name, prompt, wt, fb, no_tty, launch,
                        result, json_output, run_key=None, session_file=None:
                        launched.append((no_tty, launch, prompt)))
    r = cli_test_invoke("sync", "IPG-929", "--merge", "--yes")
    assert r.exit_code == 0
    assert launched and launched[0][0] is True and launched[0][1] is True
    assert "push" in launched[0][2]


def test_sync_conflict_without_harness_flags_reports_conflict(
        isolated_config, tmp_path, monkeypatch):
    from workagent import cli, store
    wt_dir = tmp_path / "wt"
    _cli_link(isolated_config, tmp_path, monkeypatch, cli, store, wt_dir)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda wt, db: {"status": "conflict",
                                        "conflicts": ["a.txt"]})
    monkeypatch.setattr(cli.sync_mod, "auto_resolve_changelog",
                        lambda wt, c: False)
    launched = []
    monkeypatch.setattr(cli, "_run_harness", lambda *a, **k: launched.append(a))
    r = cli_test_invoke("sync", "IPG-929", "--merge", "--yes", "--json")
    assert r.exit_code == 0
    import json as _json
    out = _json.loads(r.stdout)
    assert out["result"] == "conflict-harness"
    assert launched  # --yes auto-launches the harness non-interactively


def test_sync_all_continues_after_failure(isolated_config, tmp_path,
                                          monkeypatch):
    from workagent import cli, store
    wt1 = tmp_path / "wt1"; wt2 = tmp_path / "wt2"
    _cli_link(isolated_config, tmp_path, monkeypatch, cli, store, wt1)
    wt2.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(wt2)], check=True,
                   capture_output=True)
    store.record_link("jira:IPG-932", {"issue": "IPG-932",
                                       "worktree": str(wt2),
                                       "branch": "feat/IPG-932--y",
                                       "repo": str(tmp_path / "proj")})
    calls = []
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge",
                        lambda wt, db: calls.append(str(wt))
                        or ({"status": "conflict", "conflicts": ["x.txt"]}
                            if "wt1" in str(wt)
                            else {"status": "merged", "conflicts": []}))
    monkeypatch.setattr(cli.sync_mod, "auto_resolve_changelog",
                        lambda wt, c: False)
    monkeypatch.setattr(cli, "_run_harness", lambda *a, **k: None)
    monkeypatch.setattr(cli.sync_mod, "push", lambda wt, br: None)
    r = cli_test_invoke("sync", "--all", "--merge", "--yes", "--json")
    assert r.exit_code == 0
    import json as _json
    out = _json.loads(r.stdout)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["result"] == "conflict-harness"
    assert out[1]["result"] == "merged"
    assert wt2.name in calls[-1]  # second session still synced


# ── pull_rebased: local twin of a server-side rebase ──────────────────


def test_pull_rebased_fast_forward(tmp_path):
    origin, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    _push_from_sibling(tmp_path, wt, {"f.txt": "1\n", "g.txt": "2\n"})
    subprocess.run(["git", "-C", str(wt), "fetch", "-q", "origin"],
                   check=True, capture_output=True)
    assert sync.pull_rebased(wt, "main") == "fast-forward"
    assert (wt / "g.txt").read_text() == "2\n"


def test_pull_rebased_reset_after_server_rebase(tmp_path):
    origin, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    _git("checkout", "-q", "-b", "b", cwd=wt)
    _commit(wt, {"b.txt": "p\n"}, "p")
    _git("push", "-q", "origin", "b", cwd=wt)
    # sibling clone: advance main, rebase b onto it, force-push
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    _git("checkout", "-q", "b", cwd=other)
    _git("checkout", "-q", "main", cwd=other)
    _commit(other, {"m.txt": "m\n"}, "m")
    _git("push", "-q", "origin", "main", cwd=other)
    _git("checkout", "-q", "b", cwd=other)
    _git("rebase", "-q", "main", cwd=other)
    _git("push", "-q", "--force", "origin", "b", cwd=other)
    # local worktree still sits on the pre-rebase commit
    assert sync.pull_rebased(wt, "b") == "reset"
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout
    ob = subprocess.run(["git", "-C", str(wt), "rev-parse", "origin/b"],
                        check=True, capture_output=True, text=True).stdout
    assert head == ob
    assert (wt / "b.txt").read_text() == "p\n"
    assert (wt / "m.txt").read_text() == "m\n"


def test_pull_rebased_skips_genuinely_new_local_commits(tmp_path):
    origin, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    _git("checkout", "-q", "-b", "b", cwd=wt)
    _commit(wt, {"b.txt": "p\n"}, "p")
    _git("push", "-q", "origin", "b", cwd=wt)
    # sibling: rebase b (same patch, new base) and force-push
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)],
                   check=True, capture_output=True)
    _git("checkout", "-q", "b", cwd=other)
    _git("checkout", "-q", "main", cwd=other)
    _commit(other, {"m.txt": "m\n"}, "m")
    _git("push", "-q", "origin", "main", cwd=other)
    _git("checkout", "-q", "b", cwd=other)
    _git("rebase", "-q", "main", cwd=other)
    _git("push", "-q", "--force", "origin", "b", cwd=other)
    # local adds a genuinely new commit the server rebase never saw
    _commit(wt, {"c.txt": "n\n"}, "n")
    assert sync.pull_rebased(wt, "b") == "skipped-local-commits"
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout
    assert (wt / "c.txt").read_text() == "n\n"  # untouched


def test_pull_branch_ff_and_merge(tmp_path):
    origin, wt = _seed_and_clone(tmp_path, {"f.txt": "1\n"})
    _git("checkout", "-q", "-b", "b", cwd=wt)
    _commit(wt, {"b.txt": "p\n"}, "p")
    _git("push", "-q", "origin", "b", cwd=wt)
    # sibling pushes a remote-only commit on b
    other = tmp_path / "other"
    import subprocess as _sp
    _sp.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    _git("checkout", "-q", "b", cwd=other)
    _commit(other, {"r.txt": "r\n"}, "r")
    _git("push", "-q", "origin", "b", cwd=other)
    # local b is behind only → fast-forward
    assert sync.pull_branch(wt, "b") == "fast-forward"
    assert (wt / "r.txt").read_text() == "r\n"
    # divergent: local + remote commits → merge commit
    _commit(wt, {"l.txt": "l\n"}, "l")
    _commit(other, {"r2.txt": "r2\n"}, "r2")
    _git("push", "-q", "origin", "b", cwd=other)
    assert sync.pull_branch(wt, "b") == "merged"
    assert (wt / "r2.txt").read_text() == "r2\n"
    # push the merge so remote matches, then already current → up-to-date
    _git("push", "-q", "origin", "b", cwd=wt)
    assert sync.pull_branch(wt, "b") == "up-to-date"
