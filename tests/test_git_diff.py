"""Tests for src/workagent/git_diff.py (T08.1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workagent import git_diff, store


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "hello.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    _git(repo, "add", "hello.py")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "checkout", "-b", "feat/demo")
    (repo / "hello.py").write_text(
        "def greet(name='world'):\n    return f'hello {name}'\n",
        encoding="utf-8",
    )
    _git(repo, "add", "hello.py")
    _git(repo, "commit", "-m", "update greet")
    # Working-tree modification + untracked file
    (repo / "hello.py").write_text(
        "def greet(name='world'):\n    return f'hello {name}!'\n",
        encoding="utf-8",
    )
    (repo / "notes.txt").write_text("line 1\nline 2\n", encoding="utf-8")
    return repo


def test_list_changed_files_base_and_head(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    base = git_diff.list_changed_files(repo, mode="base", base_branch="main")
    assert base["mode"] == "base"
    by_path = {f["path"]: f for f in base["files"]}
    assert "hello.py" in by_path
    assert by_path["hello.py"]["status"] == "M"
    assert by_path["hello.py"]["additions"] == 2
    assert by_path["hello.py"]["deletions"] == 2
    assert "notes.txt" in by_path
    assert by_path["notes.txt"]["status"] == "?"
    assert by_path["notes.txt"]["additions"] == 2

    head = git_diff.list_changed_files(repo, mode="head", base_branch="main")
    assert head["mode"] == "head"
    head_by_path = {f["path"]: f for f in head["files"]}
    assert head_by_path["hello.py"]["additions"] == 1
    assert head_by_path["hello.py"]["deletions"] == 1
    assert "notes.txt" in head_by_path


def test_get_file_diff_hunks_and_untracked(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)

    diff = git_diff.get_file_diff(repo, "hello.py", mode="base", base_branch="main")
    assert diff["binary"] is False
    assert diff["truncated"] is False
    assert diff["additions"] == 2
    assert diff["deletions"] == 2
    assert len(diff["hunks"]) == 1
    types = [ln["type"] for ln in diff["hunks"][0]["lines"]]
    assert "del" in types and "add" in types

    untracked = git_diff.get_file_diff(repo, "notes.txt", mode="head", base_branch="main")
    assert untracked["binary"] is False
    assert untracked["additions"] == 2
    assert untracked["deletions"] == 0
    assert [ln["content"] for ln in untracked["hunks"][0]["lines"]] == ["line 1", "line 2"]


def test_path_traversal_and_invalid_mode_rejected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    for bad in ("", "../outside", "/etc/passwd", "a/../../etc/passwd", "bad\x00path"):
        with pytest.raises(git_diff.DiffPathError):
            git_diff.get_file_diff(repo, bad, mode="base", base_branch="main")
    with pytest.raises(git_diff.DiffModeError):
        git_diff.list_changed_files(repo, mode="invalid")


def test_binary_and_size_cap(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "img.bin").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    bin_diff = git_diff.get_file_diff(repo, "img.bin", mode="head", base_branch="main")
    assert bin_diff["binary"] is True
    assert bin_diff["hunks"] == []

    big_text = "\n".join(f"line {i:04d} " + "x" * 40 for i in range(200)) + "\n"
    (repo / "big.txt").write_text(big_text, encoding="utf-8")
    big_diff = git_diff.get_file_diff(repo, "big.txt", mode="head", base_branch="main", max_bytes=512)
    assert big_diff["truncated"] is True
    assert big_diff["binary"] is False
    assert len(big_diff["hunks"]) == 1


def test_read_prodtest_plan(isolated_config: Path, tmp_path: Path) -> None:
    plan = tmp_path / "IPG-42.yaml"
    plan.write_text(
        "issue: IPG-42\n"
        "revision: 2\n"
        "risk: medium\n"
        "summary: Verify checkout endpoint\n"
        "preconditions:\n"
        "  - id: PRE-1\n"
        "    title: Service healthy\n"
        "cases:\n"
        "  - id: TC-1\n"
        "    title: Happy path\n"
        "    kind: smoke\n"
        "    priority: must\n"
        "    safety: read-only\n",
        encoding="utf-8",
    )
    store.upsert_prodtest(
        "IPG-42",
        status="pending",
        plan_path=str(plan),
        plan_revision=2,
        plan_risk="medium",
    )
    out = git_diff.read_prodtest_plan("IPG-42")
    assert out["issue_key"] == "IPG-42"
    assert out["plan_revision"] == 2
    assert out["plan_risk"] == "medium"
    assert out["parse_error"] is None
    assert out["summary"] == "Verify checkout endpoint"
    assert len(out["cases"]) == 1
    assert out["cases"][0]["id"] == "TC-1"
    assert len(out["preconditions"]) == 1

    with pytest.raises(FileNotFoundError):
        git_diff.read_prodtest_plan("UNKNOWN-999")
