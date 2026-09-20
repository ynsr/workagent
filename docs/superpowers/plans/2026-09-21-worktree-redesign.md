# Worktree Entity Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename Session→Worktree with triple-key uniqueness and resolve-before-create in every subcommand.

**Architecture:** New `src/harness/worktrees.py` owns the uniqueness index, resolution, and validation; `cli.py` call sites switch from `_resolve_session_key`/`_pick_session_key` to `worktrees.resolve`; `store.py` migrates old entries lazily.

**Tech Stack:** Python, Typer CLI, pytest, existing `git-wt` wrapper.

**Spec:** `docs/superpowers/specs/2026-09-21-worktree-design.md`

## Global Constraints

- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- `uv run pytest -v` from `/home/bs/projects/personal/harness` must stay green.
- Full-width/refactor work stays out; this plan changes identity and lookup only.

## Review Focus

- `review` given a branch name that exists both locally and as `origin/` remote-tracking only resolves to the recorded worktree, not a fresh checkout.
- `resolve_worktree` given a path with `~` or symlink resolves to the same entry as the absolute path.
- `load_links` given an old entry without `ref_key`/`tracker_url` still resolves by key and backfills on save.
- `sync --all` given one stale worktree (dir deleted) continues past it and prunes the entry.
- `status <ref>` given an ambiguous substring on a non-TTY fails with exit 2 listing matches, never picks silently.

---

### Task 1: worktrees module (index + resolve + validate)

**Files:**
- Create: `src/harness/worktrees.py`
- Test: `tests/test_worktrees.py`

**Interfaces:**
- Consumes: `store.load_links() -> dict`, `refs.parse_ref`, `refs.issue_key`, `pick.pick`.
- Produces: `resolve_worktree(ref: str, links: dict) -> str | list[str] | None`, `pick_worktree(ref: str, resolved, links: dict) -> str`, `is_valid_worktree(path: str) -> bool`, `worktree_pr_url(key: str, entry: dict) -> str | None` — later tasks import these names verbatim.

- [ ] **Step 1: Write the failing test**

```python
"""Triple-key worktree resolution."""
from harness import worktrees

LINKS = {
    "jira:IPG-929": {"branch": "feat/IPG-929--x", "worktree": "/tmp/wt-a",
                     "repo": "/tmp/proj", "pr_url": "https://x/mr/1"},
}

def test_resolve_by_branch():
    assert worktrees.resolve_worktree("feat/IPG-929--x", LINKS) == "jira:IPG-929"

def test_resolve_by_path():
    assert worktrees.resolve_worktree("/tmp/wt-a", LINKS) == "jira:IPG-929"

def test_resolve_by_key():
    assert worktrees.resolve_worktree("jira:IPG-929", LINKS) == "jira:IPG-929"

def test_miss_returns_none():
    assert worktrees.resolve_worktree("jira:NOPE-1", LINKS) is None

def test_invalid_path_is_false(tmp_path):
    assert worktrees.is_valid_worktree(str(tmp_path / "gone")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worktrees.py -v`
Expected: FAIL with "No module named harness.worktrees" (or import error).

- [ ] **Step 3: Write minimal implementation**

```python
"""Worktree identity: unique by ref key, branch name, and path."""
from __future__ import annotations

from pathlib import Path

from . import pick as _pick
from . import refs, store
from .errors import HarnessError


def _norm_path(p: str) -> str:
    return str(Path(p).expanduser().resolve()) if p else ""


def resolve_worktree(ref: str, links: dict) -> str | list[str] | None:
    """Resolve ref to a worktree key via key, branch, or path.

    Exact ref key first, then parseable issue/PR refs, then branch-name
    match, then path match, then substring over keys/branches/paths.
    Returns the key, a list on ambiguity, or None.
    """
    if ref in links:
        return ref
    try:
        parsed = refs.parse_ref(ref)
    except HarnessError:
        parsed = None
    needle = ref
    exact: str | None = None
    if parsed is not None:
        key = refs.issue_key(parsed)
        pr_key = f"pr:{parsed['url']}" if parsed.get("repo") else key
        if key in links:
            exact = key
        elif pr_key in links:
            exact = pr_key
        needle = parsed.get("number", "") or ref
    for k, v in links.items():
        if needle and needle == (v.get("branch", "") or ""):
            return k
    if needle:
        want = _norm_path(needle)
        for k, v in links.items():
            if want and want == _norm_path(v.get("worktree", "") or ""):
                return k
    matches = [k for k, v in links.items()
               if needle in k
               or needle in (v.get("worktree", "") or "")
               or needle in (v.get("branch", "") or "")]
    if not matches:
        bare = needle.split("/")[-1].split("--")[0].strip()
        if bare and bare != needle:
            matches = [k for k, v in links.items()
                       if bare in k
                       or bare in (v.get("worktree", "") or "")
                       or bare in (v.get("branch", "") or "")]
    if exact is not None and exact not in matches:
        matches = [exact, *matches]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches


def pick_worktree(ref: str, resolved: str | list[str], links: dict | None = None) -> str:
    """Disambiguate multiple fuzzy matches interactively."""
    import sys
    from .cli import _fail, EXIT_USAGE
    if isinstance(resolved, str):
        return resolved
    if not sys.stdin.isatty():
        _fail(f"ambiguous ref {ref!r} matches: {', '.join(resolved)}.\n"
              "  Re-run with the exact worktree key.", EXIT_USAGE)
    options = [(k, (links or {}).get(k, {}).get("branch", "")) for k in resolved]
    idx = _pick.pick(f"multiple worktrees match {ref!r}:", options)
    if idx is None:
        _fail("aborted", EXIT_USAGE)
    return resolved[idx]


def is_valid_worktree(path: str) -> bool:
    """True when path exists and is a live git worktree."""
    from .errors import run_cmd
    p = Path(path).expanduser()
    if not path or not p.is_dir():
        return False
    if run_cmd("git", "-C", str(p), "rev-parse", "--git-common-dir",
               check=False) is None:
        return False
    common = run_cmd("git", "-C", str(p), "rev-parse",
                     "--path-format=absolute", "--git-common-dir",
                     check=False)
    if not common:
        return False
    out = run_cmd("git", "-C", str(p), "worktree", "list", "--porcelain",
                  check=False) or ""
    want = str(p.resolve())
    listed = [ln[9:] for ln in out.splitlines() if ln.startswith("worktree ")]
    return any(str(Path(x).resolve()) == want for x in listed)


def worktree_pr_url(key: str, entry: dict) -> str | None:
    """Recorded PR/MR URL; falls back to a live branch query."""
    if entry.get("pr_url"):
        return entry["pr_url"]
    repo = entry.get("repo", "")
    branch = entry.get("branch", "")
    if not (repo and branch and Path(repo).exists()):
        return None
    from .cli import _repo_tool
    tool = _repo_tool(repo)
    if not tool:
        return None
    try:
        pr = refs.latest_pr(refs.fetch_pr_list_for_branch(tool, branch, cwd=repo))
    except HarnessError:
        return None
    if pr:
        store.record_link(key, {"pr_url": pr["url"]})
        return pr["url"]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_worktrees.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/harness/worktrees.py tests/test_worktrees.py
git commit -m "feat: add worktree triple-key resolution module"
```

### Task 2: store lazy migration

**Files:**
- Modify: `src/harness/store.py` (`load_links`, `save_links`)
- Test: `tests/test_store.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `load_links()` backfills `ref_key` (= dict key) on entries missing it; behavior otherwise unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_load_links_backfills_ref_key(isolated_config):
    store.save_links({"jira:IPG-1": {"branch": "feat/x", "worktree": "/tmp/wt"}})
    links = store.load_links()
    assert links["jira:IPG-1"]["ref_key"] == "jira:IPG-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store.py::test_load_links_backfills_ref_key -v`
Expected: FAIL with KeyError or AssertionError.

- [ ] **Step 3: Write minimal implementation**

In `src/harness/store.py`, replace `load_links` body:

```python
def load_links() -> dict:
    links = _read_json(config_dir() / "links.json", {})
    for key, entry in links.items():
        if isinstance(entry, dict):
            entry.setdefault("ref_key", key)
    return links
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/store.py tests/test_store.py
git commit -m "feat: backfill ref_key on worktree link load"
```

### Task 3: review resolve-before-create

**Files:**
- Modify: `src/harness/cli.py:314-371` (`review` session-ref branch + worktree creation)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `worktrees.resolve_worktree`, `worktrees.pick_worktree`, `worktrees.worktree_pr_url` from Task 1.
- Produces: `review` reuses existing worktree on branch/path/key hit; unchanged output keys.

- [ ] **Step 1: Write the failing test**

```python
def test_review_reuses_existing_worktree_by_branch(isolated_config, tmp_path, monkeypatch):
    """review with a branch that has a recorded worktree reuses it (issue #2)."""
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    wt = tmp_path / "wt-existing"
    wt.mkdir()
    store.record_link("jira:IPG-929", {"branch": "feat/IPG-929--x",
                                       "worktree": str(wt),
                                       "repo": str(repo_dir),
                                       "pr_url": "https://git.example.com/g/p/-/merge_requests/1699"})
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    started = []
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: started.append(kw) or {"worktree_path": "SHOULD-NOT-HAPPEN",
                                                                  "branch": kw.get("branch")})
    launched = []
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["review", "feat/IPG-929--x", "--no-tty", "--no-harness", "--json"])
    assert r.exit_code == 0, r.output
    assert started == []  # no new worktree created
    assert json.loads(r.stdout)["worktree_path"] == str(wt)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_review_reuses_existing_worktree_by_branch -v`
Expected: FAIL (review tries `git-wt start` or errors with no recorded PR/MR).

- [ ] **Step 3: Write minimal implementation**

In `src/harness/cli.py`, add `from . import worktrees` to the module imports.
In `review`, replace the session-ref block (the `if parsed["kind"] not in ("pr", "mr")` branch) to use `worktrees.resolve_worktree` / `worktrees.pick_worktree` / `worktrees.worktree_pr_url` (same logic, new names, prompt text "worktree" instead of "session"). After `head_ref` is known and before `gitwt.start_worktree`, insert a reuse check:

```python
    reuse_key = worktrees.resolve_worktree(head_ref, store.load_links())
    if isinstance(reuse_key, str):
        reuse_entry = store.load_links().get(reuse_key, {})
        if reuse_entry.get("worktree") and worktrees.is_valid_worktree(reuse_entry["worktree"]):
            worktree = reuse_entry["worktree"]
            branch = reuse_entry.get("branch", head_ref)
            store.record_link(f"pr:{pr_url}", {"pr_url": pr_url, "worktree": worktree,
                                               "branch": branch, "repo": str(repo_dir)})
            prompt = backend.prompt_for_review(pr_url, worktree=worktree, branch=branch)
            result = {"worktree_path": worktree, "branch": branch, "pr_url": pr_url,
                      "harness": harness_name}
            eprint(f"worktree: {worktree}  branch: {branch}")
            _run_harness(harness_name, prompt, worktree, str(repo_dir), no_tty, no_harness,
                         result, json_output)
            return
```

Delete the `slug="review"` fallback is already gone (from the #3 fix); keep the actionable `_fail` when `head_ref` is empty.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -k review -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py tests/test_cli.py
git commit -m "feat: review reuses existing worktree by branch/path/key"
```

### Task 4: sync/cleanup/cd/status cutover + terminology

**Files:**
- Modify: `src/harness/cli.py` (`cleanup`, `cd_cmd`, `status`, `sync` resolve sites), `src/harness/webapp.py` (`SessionMap` import/type), `AGENTS.md`, `CHANGELOG.md`
- Test: `tests/test_cli.py`, `tests/test_webapp.py` (existing suites must pass; rename assertions where they say "session")

**Interfaces:**
- Consumes: Task 1 names.
- Produces: no `_resolve_session_key` callers remain; user-facing "session" becomes "worktree" in prompts, errors, and table titles.

- [ ] **Step 1: Write the failing test**

```python
def test_cleanup_resolves_by_branch(isolated_config, tmp_path, monkeypatch):
    store.record_link("jira:IPG-929", {"branch": "feat/IPG-929--x", "repo": "/tmp/proj",
                                       "worktree": "/tmp/wt"})
    r = runner.invoke(cli.app, ["cleanup", "feat/IPG-929--x", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["branch"] == "feat/IPG-929--x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cleanup_resolves_by_branch -v`
Expected: FAIL only if branch resolution differs from substring behavior; if it already passes, extend the assertion to the new "worktree" wording in stderr and watch it fail.

- [ ] **Step 3: Write minimal implementation**

Replace each `_resolve_session_key(` call with `worktrees.resolve_worktree(`, each `_pick_session_key(` with `worktrees.pick_worktree(`, each `_session_pr_url(` with `worktrees.worktree_pr_url(` in `src/harness/cli.py` and `src/harness/webapp.py`. Keep the old private functions as thin deprecated aliases for one release OR delete them if no external importers exist (check `webapp.py` imports first). Change user-facing strings: "session" → "worktree", "Linked sessions" → "Linked worktrees", "multiple sessions match" → "multiple worktrees match", "no linked state" stays. Update `AGENTS.md` terminology row and add a `CHANGELOG.md` entry under Unreleased.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q`
Expected: PASS (full suite green).

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py src/harness/webapp.py tests/test_cli.py tests/test_webapp.py AGENTS.md CHANGELOG.md
git commit -m "refactor: Session→Worktree terminology and resolve cutover"
```
