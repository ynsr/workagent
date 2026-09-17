"""Tests for git_wt.doctor — install receipt and sync self-check."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import git_wt
import pytest

from git_wt import doctor
from git_wt.cli import main


@pytest.fixture()
def fake_repo(tmp_path):
    """A fake source checkout: repo/src/git_wt with two modules."""
    pkg = tmp_path / "repo" / "src" / "git_wt"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '0.1.0'\n")
    (pkg / "cli.py").write_text("def main():\n    pass\n")
    return tmp_path / "repo"


def write_receipt(source_dir, source_hash):
    receipt = doctor.share_dir() / "install-receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(
        {"source_hash": source_hash, "installed_at": "2026-01-01T00:00:00+00:00",
         "source_dir": str(source_dir)}))


def sync_receipt_for(fake_repo):
    """Receipt whose hash matches the fake tree's current content."""
    write_receipt(fake_repo, doctor.source_hash(fake_repo / "src" / "git_wt"))


def test_source_hash_sensitive_and_stable(fake_repo):
    """Same content → same hash; one changed file → different hash."""
    pkg = fake_repo / "src" / "git_wt"
    first = doctor.source_hash(pkg)
    assert first == doctor.source_hash(pkg)
    (pkg / "ops.py").write_text("x = 1\n")
    assert doctor.source_hash(pkg) != first


def test_check_ok(fake_repo):
    sync_receipt_for(fake_repo)
    status, message = doctor.check()
    assert status == "ok"
    assert "in sync" in message


def test_check_stale_after_edit(fake_repo):
    """Receipt taken before an edit reports stale with the fix command."""
    sync_receipt_for(fake_repo)
    (fake_repo / "src" / "git_wt" / "ops.py").write_text("x = 2\n")
    status, message = doctor.check()
    assert status == "stale"
    assert "./install.sh" in message


def test_check_missing_receipt():
    status, message = doctor.check()
    assert status == "missing"
    assert "./install.sh" in message


def test_check_missing_source_dir(fake_repo):
    """Receipt whose recorded checkout is gone reports missing."""
    write_receipt(fake_repo / "gone", "abc")
    status, message = doctor.check()
    assert status == "missing"
    assert "./install.sh" in message


def test_cli_doctor_ok(fake_repo, capsys):
    """git-wt doctor exits 0 and prints the message when in sync."""
    sync_receipt_for(fake_repo)
    with patch("sys.argv", ["git-wt", "doctor"]):
        main()
    captured = capsys.readouterr()
    assert "in sync" in captured.out


def test_cli_doctor_stale_exits_1(fake_repo, capsys):
    """git-wt doctor exits 1 and names the fix when stale."""
    sync_receipt_for(fake_repo)
    (fake_repo / "src" / "git_wt" / "ops.py").write_text("x = 3\n")
    with patch("sys.argv", ["git-wt", "doctor"]), pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    captured = capsys.readouterr()

    assert "source changed since install" in captured.out


def test_cli_doctor_json(fake_repo, capsys):
    """--json emits a status/message object on stdout."""
    sync_receipt_for(fake_repo)
    with patch("sys.argv", ["git-wt", "doctor", "--json"]):
        main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert "message" in payload


def test_dev_warning_silent_without_receipt():
    assert doctor.dev_warning() is None


def test_dev_warning_fires_on_mismatch(fake_repo):
    """Running from the tree with a mismatched receipt warns to reinstall."""
    write_receipt(fake_repo, "deadbeef0000")
    warning = doctor.dev_warning()
    assert warning is not None
    assert "./install.sh" in warning


def test_dev_warning_silent_when_synced():
    """Receipt matching the running tree produces no warning."""
    running_pkg = Path(git_wt.__file__).parent
    write_receipt(running_pkg.parent.parent, doctor.source_hash(running_pkg))
    assert doctor.dev_warning() is None


def test_dev_warning_never_from_installed_copy(fake_repo, tmp_path):
    """A path under site-packages never warns, even with a bad receipt."""
    write_receipt(fake_repo, "deadbeef0000")
    installed = tmp_path / "site-packages" / "git_wt" / "doctor.py"
    assert doctor.dev_warning(here=installed) is None
