# Release Action Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current default→release MR + FF-only auto-merge flow with a pinned-SHA, gated, strategy-ordered release: `--checks`/`--diff` controls, detached worktree for the local gate, flock concurrency guard, merge-capability cache, split-button web UI with versioned localStorage prefs and a confirm dialog.

**Architecture:** `cli_release.py` grows from ~184 to a thin orchestrator: new modules `release_lock.py` (flock), `release_worktree.py` (detached worktree lifecycle), `release_caps.py` (merge-capability cache), `release_flow.py` (steps 1–7). `repos_release.py` keeps `release_status` (extended with SHA/behind-ahead data); `refs_ci.py`'s `fetch_ci`/`fetch_ci_status` serve the CI gate. Web: `ReleaseButton.tsx` becomes a split button, prefs in a new `lib/releasePrefs.ts`, `web_args.py` mirrors the new flags.

**Tech Stack:** Python (Typer CLI, `fcntl.flock`, `subprocess` process groups), React + TS frontend (existing `ReleaseButton`, `useConfirmedRun`), existing `gh`/`glab` host CLIs, `git worktree`.

**Spec:** User message 2026-10-16 (Release action update: options, web UI, concurrency, worktree, merge capability cache, 7-step flow, acceptance criteria/tests).

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- Python files and React components/classes must not exceed 500 lines.
- CLI owns the logic; `web_args.py` `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS` mirror it — `tests/test_webapp.py::test_specs_mirror_cli_flags` fails on drift.
- New/renamed flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli_release.py` + `tests/test_webapp.py` in the same PR.
- Terminology: **Release** everywhere; the word Deploy MUST NOT appear in command names, flags, columns, buttons, components.
- `CHANGELOG.md` entries ≤150 chars + `[no-issue]`, gap-insert additive (`PUT >N:`), verify `git diff` shows only `+`.
- Commit + push on `main` automatically after tests pass; after work re-run `./install.sh`.
- `cli_release.py` is currently 184 lines, `repos_release.py` 210 lines — both well under the 500-line cap, so new modules (not growth) keep it that way.

## Review Focus

- A second concurrent release (double-click, CLI + web) must be rejected by the lock, not run twice — the lock acquisition races a same-millisecond double invoke.
- `Ctrl-C` during the CI poll or pre-release command must still reclaim or keep-per-policy the worktree and release the lock — signal handlers that only cover the happy path leak both.
- `checks=none` on reload must reset to `ci`, not persist — a prefs write that stores `none` breaks the spec's reset rule.
- A corrupt `localStorage` value (`release.prefs.v1` = garbage, or `localStorage` throwing) must not break the page — unguarded `JSON.parse` whitescreens.
- The pinned SHA advancing between validation and merge must reject the merge — a merge that trusts step-1 data without re-checking releases the wrong commits.

---

### Task 1: Lock + duration parsing + config validation primitives

**Files:**
- Create: `src/workagent/release_lock.py`
- Create: `src/workagent/release_util.py`
- Test: `tests/test_release_lock.py`

**Interfaces:**
- Consumes: nothing new (stdlib `fcntl`, `re`, `pathlib`).
- Produces: `release_lock.lock_path(base: str, repo_name: str) -> Path`; `release_lock.held_lock(path: Path) -> context manager` (acquires non-blocking `LOCK_EX|LOCK_NB`, raises `HarnessError("release already running for <name>", exit_code=1)` when held); `release_util.parse_duration(s: str) -> int` (seconds; accepts `10m`, `15m`, `90s`, `2h`, bare seconds; raises `HarnessError` exit 2 on garbage); `release_util.worktree_base() -> Path` (`WORKTREES_DIR` env or `$HOME/dev/worktrees`); `release_util.validate_unique_repo_names(registry: dict) -> None` (raises `HarnessError` exit 2 on duplicate `<repo-name>` keys — note: dict keys are unique by construction, so this validates the *values'* implied names if the registry ever maps aliases; at minimum it asserts the requested name resolves to exactly one row).

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_duration_units(tmp_path):
    from workagent.release_util import parse_duration
    assert parse_duration("10m") == 600
    assert parse_duration("15m") == 900
    assert parse_duration("90s") == 90
    assert parse_duration("2h") == 7200
    assert parse_duration("30") == 30

def test_parse_duration_garbage_is_usage():
    from workagent.release_util import parse_duration
    from workagent.errors import HarnessError
    with pytest.raises(HarnessError) as e:
        parse_duration("ten minutes")
    assert e.value.exit_code == 2

def test_lock_rejects_second_holder(tmp_path):
    from workagent.release_lock import held_lock, lock_path
    p = lock_path(str(tmp_path), "proj")
    with held_lock(p):
        from workagent.errors import HarnessError
        with pytest.raises(HarnessError, match="release already running"):
            with held_lock(p):
                pass

def test_lock_released_after_exit(tmp_path):
    from workagent.release_lock import held_lock, lock_path
    p = lock_path(str(tmp_path), "proj")
    with held_lock(p):
        pass
    with held_lock(p):  # must not raise
        pass

def test_worktree_base_env(tmp_path, monkeypatch):
    from workagent.release_util import worktree_base
    monkeypatch.setenv("WORKTREES_DIR", str(tmp_path))
    assert worktree_base() == tmp_path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_lock.py -v`
Expected: FAIL with "No module named 'workagent.release_lock'".

- [ ] **Step 3: Write minimal implementation**

```python
"""flock guard for releases: <worktrees-base>/<repo-name>/.release.lock."""
from __future__ import annotations
import fcntl
from contextlib import contextmanager
from pathlib import Path
from .errors import HarnessError

def lock_path(base: str, repo_name: str) -> Path:
    return Path(base) / repo_name / ".release.lock"

@contextmanager
def held_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = path.parent.name
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HarnessError(f"release already running for {name}", exit_code=1)
        try:
            yield path
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
```

```python
"""Small release helpers: durations, worktree base, name uniqueness."""
from __future__ import annotations
import os
import re
from pathlib import Path
from .errors import HarnessError

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smh]?)\s*$", re.IGNORECASE)

def parse_duration(s: str) -> int:
    m = _DURATION_RE.match(s or "")
    if not m:
        raise HarnessError(f"bad duration {s!r}: use like 10m, 90s, 2h", exit_code=2)
    mult = {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2).lower()]
    return int(m.group(1)) * mult

def worktree_base() -> Path:
    return Path(os.environ.get("WORKTREES_DIR", "") or (Path.home() / "dev" / "worktrees"))

def validate_unique_repo_names(registry: dict, name: str) -> dict:
    hits = [n for n in registry if n == name]
    if len(hits) != 1:
        raise HarnessError(f"unknown or ambiguous repo: {name}", exit_code=2)
    return registry[name]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_lock.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/release_lock.py src/workagent/release_util.py tests/test_release_lock.py
git commit -m "release lock and duration primitives [no-issue]"
```

### Task 2: Worktree lifecycle for the local gate

**Files:**
- Create: `src/workagent/release_worktree.py`
- Test: `tests/test_release_worktree.py`

**Interfaces:**
- Consumes: `release_util.worktree_base` (Task 1), `errors.run_cmd`/`HarnessError`.
- Produces: `release_worktree.release_path(base: str, repo_name: str) -> Path` (`<base>/<repo-name>/release/`); `release_worktree.prepare(repo_path: str, dest: Path, sha: str) -> Path` (reclaim stale: `git worktree remove --force`, `rm -rf` leftover, `git worktree prune`; then `git -C repo worktree add --detach dest sha`, `git -C dest clean -ffdx`, submodules/LFS init when used; returns dest); `release_worktree.has_submodules(repo_path: str) -> bool`; `release_worktree.finish(dest: Path, ok: bool) -> str | None` (ok → remove worktree, return None; not ok → keep, return str path for the failure message).

- [ ] **Step 1: Write the failing tests** (local bare repo as remote; `git` must exist)

```python
def test_prepare_detached_at_sha(tmp_path):
    from workagent.release_worktree import prepare, release_path
    src = _git_repo(tmp_path / "src")  # helper: git init + 1 commit, returns path
    sha = _head(src)
    dest = release_path(str(tmp_path / "wt"), "proj")
    prepare(str(src), dest, sha)
    assert _head(dest) == sha
    assert _branch(dest) == ""  # detached, no branch checked out

def test_prepare_reclaims_stale(tmp_path):
    from workagent.release_worktree import prepare
    src = _git_repo(tmp_path / "src")
    sha = _head(src)
    dest = tmp_path / "wt" / "proj" / "release"
    prepare(str(src), dest, sha)
    prepare(str(src), dest, sha)  # second run reclaims the first
    assert _head(dest) == sha

def test_finish_removes_on_success_keeps_on_failure(tmp_path):
    from workagent.release_worktree import prepare, finish
    src = _git_repo(tmp_path / "src")
    sha = _head(src)
    d1 = tmp_path / "w1" / "proj" / "release"
    prepare(str(src), d1, sha)
    assert finish(d1, True) is None
    assert not d1.exists()
    d2 = tmp_path / "w2" / "proj" / "release"
    prepare(str(src), d2, sha)
    kept = finish(d2, False)
    assert kept == str(d2) and d2.exists()

def test_user_clone_untouched(tmp_path):
    # uncommitted changes in src do not affect the detached worktree
    from workagent.release_worktree import prepare
    src = _git_repo(tmp_path / "src")
    (src / "dirty.txt").write_text("uncommitted")
    sha = _head(src)
    dest = tmp_path / "wt" / "proj" / "release"
    prepare(str(src), dest, sha)
    assert not (dest / "dirty.txt").exists()
```

Helpers `_git_repo` (init, config user, commit), `_head` (`rev-parse HEAD`), `_branch` (`symbolic-ref --short HEAD` or "") live in the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_worktree.py -v`
Expected: FAIL with "No module named 'workagent.release_worktree'".

- [ ] **Step 3: Write minimal implementation**

```python
"""Detached worktree lifecycle for the release local gate."""
from __future__ import annotations
import shutil
from pathlib import Path
from .errors import HarnessError, run_cmd

def release_path(base: str, repo_name: str) -> Path:
    return Path(base) / repo_name / "release"

def has_submodules(repo_path: str) -> bool:
    return (Path(repo_path) / ".gitmodules").is_file()

def _reclaim(repo_path: str, dest: Path) -> None:
    try:
        run_cmd("git", "-C", repo_path, "worktree", "remove", "--force", str(dest), echo=False)
    except HarnessError:
        pass
    if dest.exists() or dest.is_symlink():
        shutil.rmtree(dest, ignore_errors=True)
    try:
        run_cmd("git", "-C", repo_path, "worktree", "prune", echo=False)
    except HarnessError:
        pass

def prepare(repo_path: str, dest: Path, sha: str) -> Path:
    _reclaim(repo_path, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_cmd("git", "-C", repo_path, "worktree", "add", "--detach", str(dest), sha)
    run_cmd("git", "-C", str(dest), "clean", "-ffdx")
    if has_submodules(repo_path):
        run_cmd("git", "-C", str(dest), "submodule", "update", "--init", "--recursive")
    try:
        run_cmd("git", "-C", str(dest), "lfs", "pull", echo=False)
    except HarnessError:
        pass  # LFS not in use
    return dest

def finish(dest: Path, ok: bool) -> str | None:
    if ok:
        try:
            repo = run_cmd("git", "-C", str(dest), "rev-parse", "--git-common-dir", echo=False)
        except HarnessError:
            repo = ""
        shutil.rmtree(dest, ignore_errors=True)
        if repo:
            try:
                run_cmd("git", "-C", repo.strip(), "worktree", "prune", echo=False)
            except HarnessError:
                pass
        return None
    return str(dest)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_worktree.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/release_worktree.py tests/test_release_worktree.py
git commit -m "detached worktree lifecycle for release gate [no-issue]"
```

### Task 3: Merge-capability cache + strategy list

**Files:**
- Create: `src/workagent/release_caps.py`
- Test: `tests/test_release_caps.py`

**Interfaces:**
- Consumes: `errors.run_cmd`/`HarnessError`, `time.monotonic`.
- Produces: `release_caps.get_caps(tool: str, path: str, release_branch: str) -> dict` (`{"strategy": [...], "raw": {...}}` cached per `(tool, path, release_branch)` 10 min in memory; never caches auth failures; `release_caps.invalidate(tool, path, release_branch)` on merge rejection); strategy entries are `"ff-push"`, `"gitlab-mr-ff"`, `"github-pr-merge-commit"` ordered per spec §5 (GitLab ff → `["gitlab-mr-ff"]`; GitLab non-ff → `["ff-push"]`; GitHub → `["ff-push", "github-pr-merge-commit"]`); empty strategy list when capabilities say neither is possible (e.g. GitHub linear-history required + no ff possible is still `["ff-push"]` — empty only when the host reports no allowed method at all).

Capability probes: GitLab `glab api projects/:id?` → `merge_method` (`ff` or other); GitHub `gh api repos/{owner}/{repo}` → `allow_merge_commit` + branch protection (`gh api repos/{o}/{r}/branches/<release> --jq .protection`) for linear-history/PR-required notes. Owner/repo derived from origin remote via `refs_ci._split_remote`. Auth failure = exit/output matching `401|403|auth|token|unauthorized` (case-insensitive) → raise, never cache.

- [ ] **Step 1: Write the failing tests** (monkeypatch `run_cmd`; fake clock via monkeypatching `time.monotonic` inside the module)

```python
def test_gitlab_ff_strategy(monkeypatch):
    from workagent import release_caps
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: "ff")
    caps = release_caps.get_caps("glab", "/r", "main")
    assert caps["strategy"] == ["gitlab-mr-ff"]

def test_gitlab_nonff_strategy(monkeypatch):
    from workagent import release_caps
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: "merge")
    assert release_caps.get_caps("glab", "/r", "main")["strategy"] == ["ff-push"]

def test_github_strategy_order(monkeypatch):
    from workagent import release_caps
    monkeypatch.setattr(release_caps, "_gh_caps", lambda *a: {"allow_merge_commit": True})
    assert release_caps.get_caps("gh", "/r", "main")["strategy"] == ["ff-push", "github-pr-merge-commit"]

def test_second_call_cached(monkeypatch):
    from workagent import release_caps
    calls = []
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: calls.append(1) or "ff")
    release_caps.get_caps("glab", "/r", "main")
    release_caps.get_caps("glab", "/r", "main")
    assert calls == [1]

def test_invalidate_forces_refetch(monkeypatch):
    from workagent import release_caps
    calls = []
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: calls.append(1) or "ff")
    release_caps.get_caps("glab", "/r", "main")
    release_caps.invalidate("glab", "/r", "main")
    release_caps.get_caps("glab", "/r", "main")
    assert calls == [1, 1]

def test_auth_failure_not_cached(monkeypatch):
    from workagent import release_caps
    from workagent.errors import HarnessError
    def boom(*a):
        raise HarnessError("401 Unauthorized")
    monkeypatch.setattr(release_caps, "_glab_merge_method", boom)
    with pytest.raises(HarnessError):
        release_caps.get_caps("glab", "/r", "main")
    monkeypatch.setattr(release_caps, "_glab_merge_method", lambda *a: "ff")
    assert release_caps.get_caps("glab", "/r", "main")["strategy"] == ["gitlab-mr-ff"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_caps.py -v`
Expected: FAIL with "No module named 'workagent.release_caps'".

- [ ] **Step 3: Write minimal implementation** (module with `_CACHE: dict`, `_TTL = 600`, `_glab_merge_method`, `_gh_caps` helpers using `run_cmd` + `_split_remote`, auth-failure regex, `get_caps`/`invalidate`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_caps.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/release_caps.py tests/test_release_caps.py
git commit -m "merge capability cache with strategy list [no-issue]"
```

### Task 4: Validation (step 1) + PR/MR reuse (step 2)

**Files:**
- Modify: `src/workagent/repos_release.py` (extend `release_status` with `pinned_sha`, `behind`, `ahead`; add `validate_release(name, diff_mode) -> dict`)
- Modify: `src/workagent/cli_release.py` (keep `_existing_open_mr`/`_open_mr`, add `--diff` handling)
- Test: `tests/test_cli_release.py` (append new tests; existing tests keep passing)

**Interfaces:**
- Consumes: `release_util.validate_unique_repo_names`, existing `release_diff_count` pattern.
- Produces: `repos_release.validate_release(name, diff_mode="remote") -> dict` with keys `repo, path, tool, default_branch, release_branch, pinned_sha, behind, ahead, diff_count`; aborts (via `_fail`, exit 2 unless noted): missing/empty branches (step 1a), `default == release` (1b), `behind > 0` with message `"merge release back into default"` + no PR created (1c), `ahead == 0` → `{"note": "already in sync"}` early return, host CLI missing/unauthenticated exit 1 (1d — `gh auth status` / `glab auth status`), empty strategy list exit 1 (1e, via `release_caps.get_caps`). `--diff=local`: `git fetch origin` then compare *local* `origin/` refs… wait — spec says "local = compare local refs after fetch": fetch, then `rev-list --count release..default` and `default..release` on the local tracking refs (same data, no `gh`/`glab` *diff* call — but 1d still requires the CLIs installed/authed).

- [ ] **Step 1: Write the failing tests**

```python
def test_validate_missing_release_branch_aborts(tmp_path, monkeypatch):
    # registry row with release_branch "" -> SystemExit/Exit code 2, step named
def test_validate_diverged_aborts_no_pr(tmp_path, monkeypatch):
    # behind > 0: message contains "merge release back into default"; _open_mr not called
def test_validate_ahead_zero_nothing_to_release(tmp_path, monkeypatch):
    # ahead == 0 -> note "already in sync", no PR, no worktree
def test_existing_pr_reused_not_duplicated(tmp_path, monkeypatch):
    # _existing_open_mr returns URL -> _open_mr not called, url in result
def test_wrong_base_pr_not_reused(tmp_path, monkeypatch):
    # PR with wrong target_branch ignored -> _open_mr called once
def test_pinned_sha_recorded(tmp_path, monkeypatch):
    # validate returns pinned_sha == origin/default tip at validation time
```

Use the existing `tests/test_cli_release.py` `_status`-style fixtures (read the file first: helpers `_gated_registry` etc. already exist). Mock `run_cmd` per test or extend the fake-registry pattern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "validate or reused or pinned" -v`
Expected: FAIL (functions/keys missing).

- [ ] **Step 3: Write minimal implementation** in `repos_release.py` + wire `--diff` flag in `cli_release.py` (Typer option `--diff: str = "remote"`, validate value in `{"remote","local"}`, exit 2 otherwise).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -q`
Expected: PASS (all incl. pre-existing gate tests).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/repos_release.py src/workagent/cli_release.py tests/test_cli_release.py
git commit -m "release validation with pinned SHA and PR reuse [no-issue]"
```

### Task 5: Gates — local worktree + CI poll (step 3)

**Files:**
- Create: `src/workagent/release_gates.py`
- Test: `tests/test_release_gates.py`

**Interfaces:**
- Consumes: `release_worktree.prepare/finish`, `release_lock` path for messages, `refs_ci.fetch_ci_status`, `errors.run_cmd`.
- Produces: `release_gates.run_local_gate(repo_path, repo_name, sha, command, timeout_s) -> None` (log `running pre-release: <cmd>` to stderr first; missing command → `_fail` exit 2 `"pre-release command missing"`; `prepare` worktree at sha; `run_cmd("/bin/sh","-c",command,cwd=wt,timeout=...)` in its own process group (`start_new_session=True`) so timeout kills the whole group: SIGTERM, grace 10 s, SIGKILL; nonzero → `_fail` exit 1; raises so caller can `finish(wt, ok=False)`); `release_gates.run_ci_gate(tool, path, sha, timeout_s) -> None` (needs a pinned-SHA CI lookup: `gh api .../commits/<sha>/check-runs` / `glab api projects/.../repository/commits/<sha>/statuses`; success → return; failure → `_fail` exit 1; pending → poll with backoff 5→30 s until timeout → `_fail` exit 1 `"CI timed out"`; no CI configured → `_fail` exit 1 `"no CI configured for <sha>"`;CancelledError/KeyboardInterrupt propagates after cleanup by caller). `checks="both"` ordering lives in the orchestrator (Task 6), not here.

Note: `refs_ci.fetch_ci_status` is PR/MR-scoped; the pinned-SHA lookup needs new `_ci_sha_gh`/`_ci_sha_glab` helpers in this module (or extend `refs_ci.py` — prefer new helpers here to avoid touching the cached status-table path).

Fake clock: tests monkeypatch `release_gates._sleep` and `_now`.

- [ ] **Step 1: Write the failing tests**

```python
def test_local_gate_missing_command_aborts(tmp_path): ...
def test_local_gate_nonzero_aborts_and_keeps_worktree(tmp_path): ...
def test_local_gate_timeout_kills_group(tmp_path, monkeypatch): ...  # fake clock + real `sleep 30 & wait` child; assert child dead
def test_local_gate_logs_command_first(tmp_path, capsys): ...  # "running pre-release:" on stderr before output
def test_ci_gate_success_proceeds(monkeypatch): ...
def test_ci_gate_failure_aborts(monkeypatch): ...
def test_ci_gate_pending_polls_then_times_out(monkeypatch): ...  # fake clock advances past 15m
def test_ci_gate_no_ci_aborts(monkeypatch): ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_release_gates.py -v`
Expected: FAIL with "No module named 'workagent.release_gates'".

- [ ] **Step 3: Write minimal implementation.**

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_release_gates.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/release_gates.py tests/test_release_gates.py
git commit -m "release local and CI gates [no-issue]"
```

### Task 6: Orchestrator — flags, flow steps 1–7, merge strategies

**Files:**
- Modify: `src/workagent/cli_release.py` (full rewrite of `run_release`; keep `release()` Typer signature + new flags; keep `_host_tool`, `_existing_open_mr`, `_open_mr`; delete `_set_ff_auto_merge`/`_rebase_retry`/`_merge_state` auto-merge path)
- Test: `tests/test_cli_release.py` (new flow tests)

**Interfaces:**
- Consumes: Tasks 1–5 (`release_lock`, `release_util`, `release_worktree`, `release_caps`, `release_gates`, `repos_release.validate_release`).
- Produces: `run_release(repo, *, yes, dry_run, json_output, checks="ci", diff="remote", pre_release_timeout="10m", ci_timeout="15m") -> dict` result `{"repo","pinned_sha","checks","strategy","mr_url","merged","note"}`; one-line summary to stderr `released <sha> via <strategy> checks=<checks> <url>`. New Typer flags: `--checks=ci|local|both|none` (default `ci`), `--diff=remote|local` (default `remote`), `--pre-release-timeout` (default `10m`), `--ci-timeout` (default `15m`).

Flow: lock → validate (Task 4) → dry-run early exit (step 1 summary + steps 2–5 preview, creates nothing) → find/reuse-or-create PR (Task 4 helpers; never delete source branch; leave open on later failure) → gates per `checks` (both = local first, skip CI when local fails) → confirm (TTY prompt unless `--yes`; non-TTY without `--yes` → exit 2 `"needs --yes in non-interactive mode"`) → merge pinned (`_merge_pinned`: GitLab ff → `glab mr merge --sha <pinned>`; else ff-push `git push origin <pinned>:refs/heads/<release>`; GitHub ff-push then `gh pr merge --merge --match-head-commit <url>`; verify post-merge head == pinned; on rejection `release_caps.invalidate` + abort) → finally: worktree policy + lock release → summary. Ctrl-C (`KeyboardInterrupt`) in gates/poll → cleanup + exit 1. Never squash (`--squash` must not appear), never rebase-merge.

- [ ] **Step 1: Write the failing tests**

```python
def test_checks_none_skips_both(...): ...  # no worktree, no CI call
def test_both_runs_local_first_skips_ci_on_local_fail(...): ...
def test_dry_run_creates_nothing(...): ...  # no PR, no worktree, no command, no merge
def test_cli_prompts_without_yes_and_aborts_on_no(...): ...  # monkeypatch isatty/stdin
def test_merge_pinned_rejects_on_advance(...): ...  # head moved after validation -> abort, nothing released
def test_gitlab_ff_uses_mr_sha(...): ...  # assert `glab mr merge --sha` in calls, no squash/rebase flags anywhere
def test_github_ff_push_then_merge_commit_fallback(...): ...
def test_source_branch_never_deleted(...): ...  # assert no `branch -D` / `push :<branch>` in any calls
def test_interrupt_triggers_cleanup(...): ...  # KeyboardInterrupt in CI poll -> worktree kept-per-policy, lock free
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "checks_none or both_runs or dry_run_creates or prompts or pinned or gitlab_ff or github_ff or never_deleted or interrupt" -v`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation.**

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -q`
Expected: PASS (full file).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_release.py tests/test_cli_release.py
git commit -m "pinned-SHA release flow with checks and strategies [no-issue]"
```

### Task 7: Web backend mirror + contract docs

**Files:**
- Modify: `src/workagent/web_args.py` (`BOOL_FLAGS["release"]` unchanged; `VAL_FLAGS["release"]` gains `"--checks"`, `"--diff"`, `"--pre-release-timeout"`, `"--ci-timeout"`)
- Modify: `web/API_CONTRACT.md` (new flags on the release run + strategy/pinned summary shape)
- Modify: `web/FEATURE_INVENTORY.md` (release row update)
- Test: `tests/test_webapp.py` (specs-mirror automatic; add a passthrough test posting `release` with `--checks both --diff local`)

**Interfaces:**
- Consumes: Task 6 flags.
- Produces: web `release` run accepts the four new args; `_validate_args` allows them.

- [ ] **Step 1: Write the failing test**

```python
def test_release_run_accepts_new_flags(client):
    r = client.post("/api/runs", json={"command": "release",
        "args": ["proj", "--checks", "both", "--diff", "local",
                 "--pre-release-timeout", "10m", "--ci-timeout", "15m"]})
    assert r.status_code in (202, 409)  # accepted, not 400 bad_arg
```

(Follow `tests/webapp_helpers.py` `client` fixture convention.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_webapp_data.py -k "new_flags" -v`
Expected: FAIL with 400.

- [ ] **Step 3: Write minimal implementation** (`VAL_FLAGS["release"] = ("--checks", "--diff", "--pre-release-timeout", "--ci-timeout")`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webapp_data.py -k "new_flags" -v && .venv/bin/python -m pytest tests/ -k "specs_mirror" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_webapp_data.py
git commit -m "web mirror for release checks and diff flags [no-issue]"
```

### Task 8: Web UI — prefs, split buttons, confirm dialog

**Files:**
- Create: `web/src/lib/releasePrefs.ts`
- Modify: `web/src/components/ReleaseButton.tsx` (split button)
- Modify: `web/src/lib/api.ts` (only if a type is missing — check first)
- Test: frontend via `npm run typecheck && npm run build` (no JS test harness in repo)

**Interfaces:**
- Consumes: `GET /api/release/status` (existing `useReleaseStatus`), `POST /api/runs` (`useConfirmedRun` pattern — but the dialog here is custom, then invoke with `--yes`).
- Produces: `releasePrefs.ts` exports `loadPrefs(): {checks, diff}`, `savePrefs(p)`; key `"release.prefs.v1"`; `try/catch` around all storage access; validate against `["ci","local","both","none"]` / `["remote","local"]`; `none` never persisted (load maps stored `none` → `ci`); main button runs with last-used prefs; dropdown selects the 4 checks + 2 diff options; confirm dialog shows commits-ahead + first strategy (first strategy needs a backend source — use `GET /api/release/status` extended with `first_strategy`, OR compute client-side note "strategy chosen at run time"; prefer extending `/api/release/status` in Task 6's backend work — if not done, dialog shows commits + checks/diff summary and the run result reports the strategy).

- [ ] **Step 1: Write the prefs module**

```typescript
const KEY = "release.prefs.v1"
export type Checks = "ci" | "local" | "both" | "none"
export type DiffMode = "remote" | "local"
export function loadPrefs(): { checks: Checks; diff: DiffMode } {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return { checks: "ci", diff: "remote" }
    const p = JSON.parse(raw) as Partial<...>
    const checks = ["ci","local","both"].includes(p.checks) ? p.checks : "ci"  // none -> ci
    const diff = p.diff === "local" ? "local" : "remote"
    return { checks, diff }
  } catch { return { checks: "ci", diff: "remote" } }
}
export function savePrefs(checks: Checks, diff: DiffMode): void {
  try {
    if (checks === "none") return  // hmm — spec: do NOT persist none; but the run used it. Persist previous? Simplest: skip write entirely when checks==="none".
    localStorage.setItem(KEY, JSON.stringify({ checks, diff }))
  } catch { /* storage unavailable */ }
}
```

Wait — "Do NOT persist checks=none; it resets to the default on reload": skipping the write when `none` keeps the *previous* persisted value, which would restore the previous — not reset to default. Correct behavior: when checks is `none`, write `{checks: "ci", diff}` (reset to default immediately). Fix in implementation.

- [ ] **Step 2: Rework ReleaseButton into split button** (main = release with last prefs; chevron dropdown = 4 checks + 2 diff; confirm dialog with commits + first strategy; invoke `release <repo> --checks X --diff Y --yes`; no timeout/dry-run/`--yes` controls rendered).

- [ ] **Step 3: Typecheck + build**

Run: `cd web && npm run typecheck && npm run build`
Expected: clean `tsc`, successful `vite build`.

- [ ] **Step 4: Commit**

```bash
git add web/src/lib/releasePrefs.ts web/src/components/ReleaseButton.tsx web/API_CONTRACT.md
git commit -m "release split button with prefs and dialog [no-issue]"
```

### Task 9: Full verification + changelog + install

**Files:**
- Modify: `CHANGELOG.md` (gap-insert under `## Unreleased`)

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces: green suite, installed daemon, pushed `main`.

- [ ] **Step 1: Append changelog entry (gap insert, verify additive diff)**

```bash
# entry (<=150 chars):
- Release flow overhaul: pinned-SHA gated flow (--checks/--diff), detached worktree local gate, lock guard, strategy merge [no-issue]
```

Verify: `git diff -- CHANGELOG.md` shows `+1 -0`.

- [ ] **Step 2: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass (modulo the 4 known pre-existing failures fixed in `e6bd8d2` — they must stay green).

- [ ] **Step 3: Reinstall and verify**

Run: `./install.sh`
Expected: `doctor: ok`.

- [ ] **Step 4: Live check on behpardakht-ipg**

Run: `workagent release behpardakht-ipg --dry-run` — expect step-1 summary + steps 2–5 preview, no PR/MR, no worktree.

- [ ] **Step 5: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "changelog for release overhaul [no-issue]"
git push
```

## Self-Review

**1. Spec coverage:**
- `--checks`/`--diff`/timeouts/`--dry-run`/`--yes` → Tasks 6, 7, 8 (CLI, mirror, UI). ✔
- localStorage prefs (versioned key, try/catch, validate, none-reset) → Task 8. ✔
- Hidden dry-run/timeouts/`--yes` on web; dialog-only gate; non-interactive runs → Tasks 7, 8 + Task 6 (stdin abort). ✔
- flock whole-run, double-click rejection → Tasks 1, 6. ✔
- Worktree base/path/uniqueness/create-at-SHA/reclaim/clean/submodules/keep-on-fail/remove-on-success → Task 2 + Task 6 finally-block. ✔
- Capability cache (TTL 10m, no-auth-cache, invalidate-on-reject, empty-aborts) → Task 3 + Task 6. ✔
- Steps 1–7 incl. pinned SHA, diverged-abort message, no-rebase, PR-reuse, gate ordering, merge strategies, never-squash/rebase-merge, summary line → Tasks 4, 5, 6. ✔
- Acceptance tests (validation, PR/MR, gates, worktree, merge, cache, web, CLI) → Tasks 1–8 each carry their section's tests; local bare repo + mocked gh/glab per the spec's harness note. ✔

**2. Placeholder scan:** all steps carry concrete code/commands; no TBD/TODO/"appropriate handling". The one "read the file first" pointer (Task 4 fixtures) names exact helper names to look for.

**3. Type consistency:** `pinned_sha`/`behind`/`ahead` keys defined in Task 4, consumed in Tasks 5–6; strategy tokens (`ff-push`, `gitlab-mr-ff`, `github-pr-merge-commit`) defined in Task 3, consumed in Task 6 + dialog in Task 8; `held_lock`/`parse_duration`/`worktree_base` defined in Task 1, consumed in Tasks 2, 6. ✔

**4. Review Focus:** five items listed in the header; tests pinned: lock race → Task 1 (`test_lock_rejects_second_holder`); signal cleanup → Task 6 (`test_interrupt_triggers_cleanup`); none-reset → Task 8 (manual: prefs logic — add a vitest? No JS harness exists; verification is typecheck+build + code review of the `none → ci` write path); corrupt storage → Task 8 (try/catch); SHA-advance rejection → Task 6 (`test_merge_pinned_rejects_on_advance`). ✔
