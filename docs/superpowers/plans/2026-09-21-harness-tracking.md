# Harness Tracking + review --all Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the live AI harness per worktree, refuse a second one (hard guard), show it in CLI status and the web UI, and add `review --all` (parallel/`--sequential`, `--fix`, reviewed-flag bookkeeping).

**Architecture:** New `store.py` harness-state functions over a locked `harnesses.json` (pid liveness, dead-entry self-heal); one `_run_harness`-level record/clear; a `_guard_harness` check before every harness launch in `cli.py`; `review` gains `--all`/`--sequential`/`--fix` spawning non-TTY children; `status` rows and `/api/status` gain a `harness` cell; web table + detail show it.

**Tech Stack:** Python, Typer CLI, pytest, fcntl-locked atomic JSON store, React/TS web.

**Spec:** `docs/superpowers/specs/2026-09-21-harness-tracking-design.md`

## Global Constraints

- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input. Busy-worktree guard refusal = exit 1.
- stdout carries ONLY data; every log/progress line → stderr.
- Non-interactive default: confirmations only on a TTY.
- Work in the isolated worktree (created at execution time, branch from current `main`).
- `uv run pytest -q` green after every task; `cd web && npm run build` + eslint green for web tasks.
- JSON keys backward compatible: `status --json` rows GAIN `harness` (name+pid string, or `""`), existing keys unchanged.
- Guard text verbatim: `worktree {path} already has a live harness ({name}, pid {pid}) — wait for it to finish or kill it`.

## Review Focus

- `harnesses.json` corrupted or half-written → next read sweeps/rebuilds from liveness, never raises.
- pid reused by an unrelated process → entry is treated as live (accepted false positive; spec'd single-machine guard) — a test pins the `PermissionError`-is-alive branch, not pid-reuse detection.
- `review --all` when a worktree's PR/MR fetch fails → skipped with a stderr note, no crash, other worktrees still reviewed.
- `--sequential` without `--all` → exit 2 usage error, nothing launched.
- Harness exits between two reads → second `record_harness_run` for the same key succeeds (dead entry swept first), never a false "busy".

---

### Task 1: store harness-state functions

**Files:**
- Modify: `src/harness/store.py` (after `record_link`/`load_links` block)
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: existing `_read_json`, `_write_json` (atomic), `config_dir()`, `.lock` pattern from `record_link`.
- Produces (later tasks import verbatim):
  - `load_harnesses() -> dict` — swept mapping; persists the cleaned file when entries were dropped.
  - `record_harness_run(key: str, harness: str, worktree: str = "") -> None` — `{"harness": harness, "pid": os.getpid(), "started_at": time.time(), "worktree": worktree}` under lock; sweeps dead entries for other keys first. (Task 2's cross-key guard matches on `worktree`.)
  - `clear_harness_run(key: str) -> None`.
  - `active_harness(key: str) -> dict | None` — swept live entry or None.
  - `_pid_alive(pid: int) -> bool` — `os.kill(pid, 0)` True; `PermissionError` → True; `ProcessLookupError`/any `OSError` → False.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_store.py`, using the existing `isolated_config` fixture)

```python
def test_harness_run_roundtrip(isolated_config):
    store.record_harness_run("jira:IPG-1", "omp", "/tmp/wt-a")
    rec = store.active_harness("jira:IPG-1")
    assert rec is not None and rec["harness"] == "omp" and rec["pid"] == os.getpid()
    store.clear_harness_run("jira:IPG-1")
    assert store.active_harness("jira:IPG-1") is None


def test_harness_run_dead_pid_swept(isolated_config):
    store.save_harnesses_raw({"jira:OLD": {"harness": "omp", "pid": _dead_pid(), "started_at": 0.0}})
    assert store.active_harness("jira:OLD") is None
    assert store.load_harnesses() == {}


def test_harness_run_live_foreign_pid(isolated_config):
    # A pid we cannot signal counts as alive (PermissionError branch).
    with mock.patch.object(store.os, "kill", side_effect=PermissionError):
        store.record_harness_run("jira:P", "omp", "")
        assert store.active_harness("jira:P") is not None


def _dead_pid() -> int:
    p = subprocess.Popen(["sleep", "0"])
    p.wait()
    return p.pid
```

Add imports used by the tests (`os`, `subprocess`, `unittest.mock as mock`) if not already imported. `save_harnesses_raw` is the raw-writer helper this task also adds (thin `_write_json(config_dir() / "harnesses.json", data)`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -v -k harness`
Expected: FAIL with AttributeError (functions missing).

- [ ] **Step 3: Write the implementation** (in `store.py`; reuse the `record_link` lock pattern: open `<name>.json.lock` with fcntl LOCK_EX for read-modify-write)

```python
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _harness_path() -> Path:
    return config_dir() / "harnesses.json"


def save_harnesses_raw(data: dict) -> None:
    _write_json(_harness_path(), data)


def _sweep(data: dict) -> tuple[dict, bool]:
    alive = {k: v for k, v in data.items()
             if isinstance(v, dict) and _pid_alive(v.get("pid", -1))}
    return alive, len(alive) != len(data)


def load_harnesses() -> dict:
    data = _read_json(_harness_path(), {})
    alive, changed = _sweep(data)
    if changed:
        save_harnesses_raw(alive)
    return alive


def record_harness_run(key: str, harness: str, worktree: str = "") -> None:
    path = _harness_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data, _ = _sweep(_read_json(path, {}))
        data[key] = {"harness": harness, "pid": os.getpid(),
                     "started_at": time.time(), "worktree": worktree}
        _write_json(path, data)


def clear_harness_run(key: str) -> None:
    path = _harness_path()
    if not path.exists():
        return
    with open(path.parent / (path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _read_json(path, {})
        if key in data:
            del data[key]
            _write_json(path, data)


def active_harness(key: str) -> dict | None:
    return load_harnesses().get(key)
```

Import `time` (and confirm `fcntl`, `os` already imported in `store.py` — they are, from the #1 fix).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/store.py tests/test_store.py
git commit -m "feat: locked harnesses.json store with pid-liveness sweep"
```

---

### Task 2: guard + record/clear in harness launches

**Files:**
- Modify: `src/harness/cli.py` — new `_guard_harness`, `_run_harness` recording, sync conflict-run sites (lines ~1263-1281)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `store.record_harness_run/clear_harness_run/active_harness/load_harnesses` (Task 1); existing `_run_harness` signature; existing `_fail(msg, code)`.
- Produces: `_guard_harness(key: str | None, worktree: str) -> None` (raises `typer.Exit(1)` via `_fail` when busy); `_run_harness(..., run_key: str | None = None)` — new trailing keyword-only param defaulting None (None → no recording, preserves `--no-harness` and preview paths).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`, mirroring existing `_start_mocks`-based tests)

```python
def test_start_refused_while_harness_live(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.repos, "repo_root", lambda cwd=None: None)
    monkeypatch.setattr(cli.repos, "worktree_branch", lambda path: None)
    monkeypatch.setattr(cli.store, "active_harness",
                        lambda key: {"harness": "omp", "pid": 99999, "started_at": 1.0})
    launched = []
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": "/tmp/wt", "branch": "feat/22--add-login"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: launched.append(a))
    r = runner.invoke(cli.app, ["start", "o/r#22", "--json"])
    assert r.exit_code == 1
    assert "already has a live harness" in r.stderr
    assert launched == []


def test_run_harness_records_and_clears(monkeypatch):
    rec = []
    monkeypatch.setattr(cli.store, "record_harness_run",
                        lambda k, h: rec.append(("rec", k, h)))
    monkeypatch.setattr(cli.store, "clear_harness_run",
                        lambda k: rec.append(("clr", k)))
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: 0)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    cli._run_harness("omp", "prompt", "/tmp/wt", "/tmp", True, False,
                     {"k": "v"}, False, run_key="jira:X")
    assert ("rec", "jira:X", "omp") in rec and ("clr", "jira:X") in rec
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k "live_harness or records_and_clears"`
Expected: FAIL (guard missing → start succeeds with exit 0; `run_key` kwarg unknown → TypeError).

- [ ] **Step 3: Implement** in `cli.py`

```python
def _guard_harness(key: str | None, worktree: str) -> None:
    """Hard guard: one live harness per worktree (issue #6)."""
    if not key:
        return
    rec = store.active_harness(key)
    if rec is None:
        # Same worktree may be recorded under a different key.
        for k, v in store.load_harnesses().items():
            if k != key and v.get("worktree") == worktree:
                rec = v
                break
    if rec is not None:
        _fail(f"worktree {worktree} already has a live harness "
              f"({rec['harness']}, pid {rec['pid']}) — wait for it to "
              "finish or kill it", 1)
```

(Then `store.record_harness_run` also persists `worktree` — adjust Task 1's `record_harness_run(key, harness, worktree: str = "")` to store it; update Task 1 test accordingly. The cross-key path-match check uses this field.)

`_run_harness` changes: add trailing `run_key: str | None = None` kwarg; before `backend.launch(...)`:

```python
if run_key:
    store.record_harness_run(run_key, harness_name, worktree or fallback_dir)
try:
    backend.launch(harness_name, prompt, worktree or fallback_dir, no_tty, _HARNESS_ARGS)
finally:
    if run_key:
        store.clear_harness_run(run_key)
```

Call-site updates — pass `run_key=`:
- `start` (~line 287): `_guard_harness(key, worktree)` before `_run_harness(...)`; `run_key=key`.
- `review` reuse path (~372) and fresh path (~394): `_guard_harness(f"pr:{pr_url}", worktree)`; `run_key=f"pr:{pr_url}"`.
- sync conflict-harness sites (~1268, ~1278): `_guard_harness(key, wt)`; `run_key=key`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v -k "start or review or sync"` then `uv run pytest -q`
Expected: all PASS (218 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py src/harness/store.py tests/test_cli.py tests/test_store.py
git commit -m "feat: hard one-harness-per-worktree guard; record live runs"
```

---

### Task 3: reviewed flag + review --all/--sequential/--fix

**Files:**
- Modify: `src/harness/cli.py` — `review` options + orchestration; `repos.py` if a `branch_tip(path)` helper is missing (check `repos.py` for an existing tip/HEAD helper first; if absent add `branch_tip(worktree: str) -> str` running `git -C <path> rev-parse HEAD`).
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `worktrees.is_valid_worktree`, `worktrees.worktree_pr_url`, `store.load_links/record_link`, `repos.branch_tip`.
- Produces: link entry fields `reviewed: bool`, `reviewed_at: str` (tip sha); `review` options `--all`, `--sequential`, `--fix` plus internal `--post-comments` (hidden, parent-only); `_reviewable_keys(links) -> list[tuple[str, str]]` (key, pr_url); `_is_reviewed(key, entry) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
def test_review_all_spawns_parallel_children(isolated_config, tmp_path, monkeypatch):
    links = {
        "jira:A-1": {"branch": "feat/a", "worktree": str(tmp_path / "a"),
                     "pr_url": "https://github.com/o/r/pull/1"},
        "jira:B-2": {"branch": "feat/b", "worktree": str(tmp_path / "b"),
                     "pr_url": "https://github.com/o/r/pull/2"},
    }
    for k, v in links.items():
        Path(v["worktree"]).mkdir()
        store.record_link(k, v)
    for d in ("a", "b"):
        (tmp_path / d / ".git").mkdir()  # cheap validity stand-in
    monkeypatch.setattr(cli.worktrees, "is_valid_worktree", lambda p: True)
    monkeypatch.setattr(cli.store, "load_links", lambda: store.load_links())
    spawns = []

    class FakePopen:
        def __init__(self, argv, **kw):
            self.argv = argv
            self.pid = 4242
            spawns.append(argv)
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(cli.store, "active_harness", lambda key: None)
    r = runner.invoke(cli.app, ["review", "--all", "--json"])
    assert r.exit_code == 0, r.output
    assert len(spawns) == 2
    for argv in spawns:
        assert argv[:3] == [cli.sys.executable, "-m", "harness"]
        assert "--no-tty" in argv and "--post-comments" in argv
    out = json.loads(r.stdout)
    assert sorted(e["key"] for e in out) == ["jira:A-1", "jira:B-2"]
    assert all(e["exit_code"] == 0 for e in out)


def test_review_sequential_without_all_fails():
    r = runner.invoke(cli.app, ["review", "--sequential"])
    assert r.exit_code == 2


def test_review_marks_reviewed_with_tip(isolated_config, tmp_path, monkeypatch):
    # reuse the review test fixture style: mocked fetch_pr_info + start_worktree
    # then assert entry["reviewed"] is True and reviewed_at == mocked tip
```

(The third test mirrors the existing `test_review_*` mocks — `fetch_pr_info` → `{"head_ref": "feat/x"}`, `gitwt.start_worktree` fake, `repos.default_branch` → "main", `repos.branch_tip` → "abc123", `backend.launch` no-op — and asserts `store.load_links()["pr:<url>"]["reviewed"] is True` and `["reviewed_at"] == "abc123"`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k review`
Expected: FAIL — `--all` unknown option; reviewed fields absent.

- [ ] **Step 3: Implement**

In `review` signature add:

```python
    all_wts: bool = typer.Option(False, "--all", help="Review every not-reviewed linked worktree in parallel (non-TTY)."),
    sequential: bool = typer.Option(False, "--sequential", help="With --all: review one-by-one instead of in parallel."),
    fix: bool = typer.Option(False, "--fix", help="With --all: children auto-fix identified issues after yielding."),
    post_comments: bool = typer.Option(False, "--post-comments", hidden=True, help="Append the auto-comment prompt segment (set by --all)."),
```

Behavior:
- `if sequential and not all_wts: _fail("--sequential requires --all", EXIT_USAGE)`.
- `if all_wts: (orchestrate)` — build reviewable list:

```python
def _is_reviewed(key: str, entry: dict) -> bool:
    if not entry.get("reviewed"):
        return False
    wt = entry.get("worktree", "")
    tip = repos.branch_tip(wt) if wt and Path(wt).is_dir() else ""
    return bool(tip) and tip == entry.get("reviewed_at", "")


def _reviewable_keys(links: dict) -> list[tuple[str, str]]:
    out = []
    for k, v in links.items():
        if _is_reviewed(k, v):
            continue
        pr = worktrees.worktree_pr_url(k, v)
        if not pr or not worktrees.is_valid_worktree(v.get("worktree", "")):
            continue
        if store.active_harness(k):
            eprint(f"{k}: harness already live — skipping")
            continue
        out.append((k, pr))
    return out
```

Orchestrator: for each `(key, pr)` spawn `subprocess.Popen([sys.executable, "-m", "harness", "review", pr, "--no-tty", "--post-comments"] + (["--fix"] if fix else []), cwd=<repo main checkout root>)`; parallel = spawn all, then `wait()` each (record rc); sequential = spawn+wait one at a time. Summary rows `{"key", "pr_url", "exit_code"}` printed via `print(json.dumps(rows, indent=2))` with `--json`, else one stderr line per child + Rich/plain stdout summary table via `_print_rows(rows, json_output, csv_output=False, columns=["key", "pr_url", "exit_code"], ...)`. Zero reviewable → stderr "nothing to review", exit 0. In the orchestration branch, `ref` is unused — do NOT resolve trackers.

- Prompt segments in the single-review path:

```python
if post_comments:
    prompt += "\n\nAuto add all comments to the PR/MR at yielding and don't wait for user approval"
if fix:
    prompt += "\n\nAuto-fix all identified issues after yielding and don't wait for user approval."
```

- Reviewed marking: in both single-review launch paths, before `_run_harness`:

```python
if not no_harness:
    entry = store.load_links().get(f"pr:{pr_url}", {})
    entry["reviewed"] = True
    entry["reviewed_at"] = repos.branch_tip(worktree)
    store.record_link(f"pr:{pr_url}", entry)
```

(On the non-exec launch failure path, `backend.launch` raises before replacement — acceptable per spec: non-TTY failure is reported; the flag self-corrects because `reviewed_at` pins the tip only when the worktree tip matches.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v -k review` then `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/cli.py src/harness/repos.py tests/test_cli.py
git commit -m "feat: review --all with parallel/sequential children and reviewed flags"
```

---

### Task 4: status/web display + docs

**Files:**
- Modify: `src/harness/cli.py` (`_session_rows`, `_session_detail`), `src/harness/webapp.py` (status endpoint passthrough via `_enrich_entry`), `web/src/lib/api.ts`, `web/src/components/StatusTable.tsx`, `web/src/components/WorktreeDetail.tsx`, `CHANGELOG.md`, `AGENTS.md`
- Test: `tests/test_cli.py`, `tests/test_webapp.py`

**Interfaces:**
- Consumes: `store.active_harness` / `store.load_harnesses` (Task 1); `/api/status` shape.
- Produces: status rows/detail gain `harness` field — `"omp 4242"` when live, `""` when not; web `SessionEntry.harness?: string`; table cell + detail field.

- [ ] **Step 1: Write the failing tests**

```python
def test_status_row_shows_live_harness(isolated_config, tmp_path, monkeypatch):
    wt = tmp_path / "wt"; wt.mkdir()
    store.record_link("jira:H-1", {"branch": "feat/h", "worktree": str(wt)})
    store.record_harness_run("jira:H-1", "omp", str(wt))
    rows, columns = cli._session_rows(store.load_links(), False, False)
    assert "harness" in columns
    row = next(r for r in rows if r["key"] == "jira:H-1")
    assert row["harness"].startswith("omp ")
```

Webapp test (mirror existing `/api/status` tests):

```python
def test_api_status_includes_harness(client_env):
    # after recording a harness run for a link, GET /api/status row has "harness"
```

- [ ] **Step 2: Run to verify failure** (`uv run pytest -v -k harness or status`) — FAIL: no `harness` in columns/row.

- [ ] **Step 3: Implement** — `_session_rows`: `row["harness"] = _harness_cell(k, v.get("worktree", ""))` where `_harness_cell` returns `f"{rec['harness']} {rec['pid']}"` from `store.active_harness(k)` else `""`; add `"harness"` to `columns` (after `"branch"`); same cell into `_session_detail`. Webapp: `_enrich_entry` import already shared — add the same `harness` field there (webapp imports `_enrich_entry` from cli; the cli change suffices — verify, and if webapp defines its own, patch it). Frontend: add `harness?: string` to `SessionEntry`; StatusTable new column header `harness` + cell rendering (dim when empty); WorktreeDetail view mode shows "Harness" field when non-empty. Update `CHANGELOG.md` (Unreleased) and `AGENTS.md` (status column list + review --all).

- [ ] **Step 4: Verify** — `uv run pytest -q` green; `cd web && npm run build` + `npx eslint src/components/StatusTable.tsx src/components/WorktreeDetail.tsx src/lib/api.ts` clean.

- [ ] **Step 5: Commit**

```bash
git add -A src/harness web/src CHANGELOG.md AGENTS.md tests/
git commit -m "feat: show live harness in status table, web UI, and docs"
```

---

### Task 5: whole-feature verification

**Files:** none (verification only)

- [ ] **Step 1:** `uv run pytest -q` — all green (expect ~228).
- [ ] **Step 2:** `cd web && npm run build` — green.
- [ ] **Step 3:** Manual smoke (TTY where possible): `harness start <ref>` in one worktree, then `harness review <same-key>` → refused exit 1 with the guard message; `harness status` shows `omp <pid>`; after the harness exits, `harness status` shows `—` and a second launch succeeds.
- [ ] **Step 4:** `harness review --all --dry-run`-equivalent behavior check with a scratch fixture: skipped-without-PR note, nothing-to-review exit 0.
