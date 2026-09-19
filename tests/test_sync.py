"""sync engine: dirty check, local merge, CHANGELOG auto-resolve, push, rebase."""

from __future__ import annotations

import subprocess
from pathlib import Path


from harness import sync


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
