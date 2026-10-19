"""Post a text comment to a linked issue (best effort, never raises).

Supports the three tracker tools via their CLIs:
  jira-cli → jira-cli issue <KEY> add-comment --body <text>
  gh       → gh issue comment (<number>|<url>) --body <text>
  glab     → glab issue note <number-or-url> --message <text>
Only issue refs are posted (never PR/MR notes); unparseable refs and
post failures return False with a stderr warning.
"""
from __future__ import annotations

from . import refs as _refs
from .cli_core import eprint
from .errors import HarnessError, run_cmd


def post_issue_comment(issue_url: str, body: str) -> bool:
    """Post *body* to the issue at *issue_url*; True on success."""
    body = (body or "").strip()
    if not body or not (issue_url or "").strip():
        return False
    try:
        parsed = _refs.parse_ref(issue_url.strip())
    except HarnessError as e:
        eprint(f"warning: cannot post summary comment ({e})")
        return False
    if parsed.get("kind") != "issue":
        return False
    tool = parsed.get("tool", "")
    number = parsed.get("number", "")
    url = parsed.get("url", "")
    try:
        if tool == "jira-cli":
            run_cmd("jira-cli", "issue", number, "add-comment", "--body", body)
        elif tool == "gh":
            target = number if parsed.get("repo") else url
            run_cmd("gh", "issue", "comment", target, "--body", body)
        elif tool == "glab":
            run_cmd("glab", "issue", "note", url or number, "--message", body)
        else:
            return False
    except HarnessError as e:
        eprint(f"warning: summary comment post failed for {issue_url}: {e}")
        return False
    eprint(f"posted session summary to {issue_url}")
    return True
