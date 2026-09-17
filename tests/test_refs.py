"""Ref parsing: URLs, shorthand, bare numbers, rejects."""

from harness import refs
from harness.errors import HarnessError

import pytest


def test_github_issue_url():
    p = refs.parse_ref("https://github.com/OWNER/REPO/issues/22")
    assert p == {"kind": "issue", "tool": "gh", "repo": "OWNER/REPO",
                 "number": "22", "url": "https://github.com/OWNER/REPO/issues/22"}


def test_github_pr_url():
    p = refs.parse_ref("https://github.com/o/r/pull/33")
    assert p["kind"] == "pr" and p["tool"] == "gh" and p["number"] == "33"


def test_gitlab_issue_and_mr():
    assert refs.parse_ref("https://git.example.com/g/r/-/issues/430")["tool"] == "glab"
    m = refs.parse_ref("https://git.example.com/g/r/-/merge_requests/55")
    assert m["kind"] == "mr" and m["number"] == "55"


def test_shorthand_and_bare_number():
    assert refs.parse_ref("o/r#22")["repo"] == "o/r"
    assert refs.parse_ref("22")["kind"] == "issue_or_pr"


def test_unknown_rejected():
    with pytest.raises(HarnessError):
        refs.parse_ref("not a ref at all !!!")


def test_issue_key_stable():
    p = refs.parse_ref("https://github.com/o/r/issues/22")
    assert refs.issue_key(p) == "github:o/r#22"
