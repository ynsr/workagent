# Launch sessions + terminal run-mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every Start/Review/Fix-Comments/Sync run as a sessions+runs DB row (including previews), auto-switch PR pastes to the Review tab, and add a terminal run-mode tracked by PID with a blue dot + Stop.

**Architecture:** CLI owns logic (`cli_harness.py` preview recorder + `--terminal` flag, `store.py` lock origin, `trackers.py` PR-aware default repo); `web_args.py`/`web_runs*.py` mirror the flag and spawn paths; `Launch.tsx`/`StartForm.tsx`/`launchConfig.ts` own the run-mode + PR-switch UI; `StatusTableMain.tsx` renders the terminal dot + Stop. TDD per task; one PR honoring the parity checklist.

**Tech Stack:** Python (Typer CLI, SQLite sessions/runs, JSON harness lock), FastAPI serve backend, React + TS + Tailwind frontend, pytest + tsc + vite build.

**Spec:** docs/superpowers/specs/2026-10-05-launch-sessions-terminal-design.md

## Global Constraints

- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- `--dry-run` records nothing, touches no state.
- Python files and React components/classes must not exceed 500 lines.
- New/renamed flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR (parity checklist).
- Harness terminology is `harness` everywhere; never `runtime`.
- `CHANGELOG.md`: gap-insert only (`PUT >N:`); entries <= 150 chars with issue ID in brackets.
- Commit and push on main branch automatically after tests pass; re-install `workagent` after work done.

## Review Focus

- A preview run for a ref with no linked worktree yet still produces a sessions row with a usable `worktree_ref` (link key or raw ref) rather than an empty/garbage key a person could never find again.
- Pasting an issue URL containing the substring "pull" (e.g. a branch named `pull-request-x` in a path) does not yank the user to the Review tab.
- Two terminal spawns racing on the same worktree: exactly one wins the lock, the loser gets a usage error naming the live PID, never two agents.
- A terminal agent killed externally (`kill -9`, reboot) does not leave a permanent blue dot: the next status poll reaps the dead lock.
- Stopping a terminal agent whose PID was recycled by the OS does not kill an unrelated process: the start-time guard refuses the kill.

---

### Task 1: Preview session recorder shared helper

**Files:**
- Modify: `src/workagent/cli_harness.py:77-174`
- Modify: `src/workagent/cli_sync.py:176-204`
- Test: `tests/test_cli_start.py`

**Interfaces:**
- Consumes: `store_sqlite.insert_session/finish_session/insert_run` (existing signatures).
- Produces: `record_preview_session(key: str, command: str, prompt: str, harness_name: str, output: str, exit_code: int = 0) -> str | None` in `cli_harness.py` — inserts a `state='preview'` sessions row with full prompt + a runs row; returns the session id or None when pre-cutover (no state.db); never raises (warns to stderr).

- [ ] **Step 1: Write the failing test**

```python
def test_preview_session_helper_stores_full_prompt(isolated_config):
    from workagent import cli_harness as ch
    from workagent import store_sqlite as sq
    db = sq.db_path()
    sq.init_db(db)
    sid = ch.record_preview_session("jira:IPG-1", "start", "PROMPT-BODY",
                                    "omp", "jira:IPG-1: preview")
    assert sid
    row = sq.get_session(db, sid)
    assert row["state"] == "preview"
    assert row["prompt"] == "PROMPT-BODY"
    assert row["file_path"] == ""
    runs = sq.list_runs(db)
    assert len(runs) == 1 and runs[0]["command"] == "start"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_start.py::test_preview_session_helper_stores_full_prompt -v`
Expected: FAIL with "has no attribute 'record_preview_session'"

- [ ] **Step 3: Write minimal implementation**

```python
def record_preview_session(key: str, command: str, prompt: str,
                           harness_name: str, output: str,
                           exit_code: int = 0) -> str | None:
    """Persist a preview (no-launch) session + run; None when pre-cutover."""
    from . import store_sqlite as _sq
    from .cli_core import eprint
    try:
        db = _sq.db_path()
        if not db.exists():
            return None
        sid = _sq.insert_session(
            db, worktree_ref=key, harness_name=harness_name,
            initiator_command=command, prompt=prompt,
            file_path="", session_type=command)
        _sq.finish_session(db, sid, "preview")
        _sq.insert_run(db, sid, command, [key], exit_code, output=[output])
        return sid
    except Exception as e:
        eprint(f"warning: session record failed: {e}")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_start.py::test_preview_session_helper_stores_full_prompt -v`
Expected: PASS

- [ ] **Step 5: Refactor sync to use the helper**

Replace the body of `_record_no_harness_session` in `src/workagent/cli_sync.py:176-204` with a call to `record_preview_session` (import from `cli_harness`), preserving the `-m`/`--rebase` args nuance by passing the args through. Keep the function name as a thin wrapper so existing call sites are untouched.

```python
def _record_no_harness_session(key: str, result: dict, exit_code: int = 0) -> None:
    """Persist a preview session + run for sync paths that never launch. (docstring kept)"""
    from .cli_harness import record_preview_session
    args = [key]
    if result.get("strategy"):
        args += ["-m" if result["strategy"] == "local-merge" else "--rebase"]
    sid = record_preview_session(key, "sync", "", "",
                                 f"{key}: {result.get('result', '')}", exit_code)
    if sid:
        result["session_id"] = sid
```

- [ ] **Step 6: Run sync preview tests**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_sync.py -v`
Expected: PASS (existing `test_sync.py:458` preview assertions still hold)

- [ ] **Step 7: Commit**

```bash
git add src/workagent/cli_harness.py src/workagent/cli_sync.py tests/test_cli_start.py
git commit -m "Add shared preview-session recorder, sync uses it"
```

### Task 2: Start/review preview paths persist sessions

**Files:**
- Modify: `src/workagent/cli_harness.py:77-174`
- Test: `tests/test_cli_start.py`, `tests/test_cli_review.py`

**Interfaces:**
- Consumes: `record_preview_session` from Task 1.
- Produces: preview (no `--launch`, no `--terminal`) `start`/`review` runs each write a `state='preview'` sessions row with the full prompt + a runs row; `result["session_id"]` set; TTY shell handover unchanged.

- [ ] **Step 1: Write the failing test (start preview persists)**

```python
def test_start_preview_persists_session_row(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree),
                                            "branch": "feat/22--add-login"})
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not launch")))
    db = sq.db_path()
    sq.init_db(db)
    r = runner.invoke(cli.app, ["start", "o/r#22", "--json"])
    assert r.exit_code == 0, r.output
    rows = sq.list_sessions(db)
    assert len(rows) == 1 and rows[0]["state"] == "preview"
    assert rows[0]["session_type"] == "start"
    assert "add-login" in rows[0]["prompt"]
    assert json.loads(r.stdout)["session_id"] == rows[0]["id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_start.py::test_start_preview_persists_session_row -v`
Expected: FAIL (no preview rows written)

- [ ] **Step 3: Implement preview recording in `_run_harness`**

In `src/workagent/cli_harness.py`, in the `if not launch:` branch (lines 100-112), before `_print_result`, call:

```python
    if not launch and not terminal:
        sid = record_preview_session(
            run_key or str(result.get("key", "")) or fallback_dir,
            str(result.get("command", harness_name)),
            prompt, harness_name,
            f"{run_key or fallback_dir}: preview")
        if sid:
            result["session_id"] = sid
```

`terminal` is the new Task 4 param defaulting False so this task compiles standalone. Guard: skip when the key is empty AND `result.get("command")` is empty (Review Focus: never write garbage-key rows — fall back to `fallback_dir`).

- [ ] **Step 4: Run tests to verify**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_start.py tests/test_cli_review.py -v`
Expected: PASS, including existing `test_run_harness_preview_writes_nothing` which must be UPDATED to expect one preview row (that test pins the old behavior — change its assertion to `len(...) == 1` and state `preview`).

- [ ] **Step 5: Review preview test (mirror)**

Add to `tests/test_cli_review.py`:

```python
def test_review_preview_persists_session_row(isolated_config, tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.setattr(cli.trackers, "resolve_for_tracker",
                        lambda tid, explicit, cwd, depth=7, yes=False, persist=True: (repo_dir, "recorded"))
    monkeypatch.setattr(cli.repos, "default_branch", lambda repo: "main")
    monkeypatch.setattr(cli.refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": str(worktree), "branch": "feat/33"})
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda wt, br: "up-to-date")
    monkeypatch.setattr(cli.backend, "launch", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not launch")))
    db = sq.db_path()
    sq.init_db(db)
    r = runner.invoke(cli.app, ["review", "https://github.com/o/r/pull/33", "--json"])
    assert r.exit_code == 0, r.output
    rows = sq.list_sessions(db)
    assert len(rows) == 1 and rows[0]["state"] == "preview"
    assert rows[0]["session_type"] == "review"
```

- [ ] **Step 6: Run tests again**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_review.py tests/test_cli_start.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/workagent/cli_harness.py tests/test_cli_start.py tests/test_cli_review.py
git commit -m "Persist preview sessions for start/review with full prompt"
```

### Task 3: Harness lock origin + stop helper

**Files:**
- Modify: `src/workagent/store.py:510-557`
- Modify: `src/workagent/cli_harness.py:26-56`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: existing `_sweep`, `_pid_alive`, `_locked` helpers.
- Produces: `record_harness_run(key, harness, worktree="", origin="headless", pid=None)` (pid defaults to `os.getpid()`); `stop_terminal_run(key) -> bool` (SIGTERM→SIGKILL the terminal PID after a start-time guard, clear lock; False when no live terminal lock); `_harness_cell` renders `"terminal <pid>"` for terminal origins. The start-time guard compares `/proc/<pid>` start time against the recorded `started_at` (Linux; non-Linux falls back to kill with no guard) and refuses the kill on mismatch.

- [ ] **Step 1: Write the failing test**

```python
def test_harness_terminal_origin_blocks_headless(isolated_config):
    store.record_harness_run("jira:T", "omp", "/tmp/wt-t", origin="terminal")
    rec = store.active_harness("jira:T")
    assert rec["origin"] == "terminal"
    blocker = store.record_harness_run("jira:T2", "omp", "/tmp/wt-t")
    assert blocker is not None and blocker["origin"] == "terminal"
    store.clear_harness_run("jira:T")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_store.py::test_harness_terminal_origin_blocks_headless -v`
Expected: FAIL with unexpected keyword argument 'origin'

- [ ] **Step 3: Implement origin in store**

```python
def record_harness_run(key: str, harness: str, worktree: str = "",
                       origin: str = "headless", pid: int | None = None) -> dict | None:
    # ... same lock discipline; record:
    # data[key] = {"harness": harness, "pid": pid or os.getpid(),
    #              "started_at": time.time(), "worktree": worktree,
    #              "origin": origin or "headless"}
```

Update `_record_harness_locked` likewise. `_sweep` keeps unknown origins intact (no change needed — verify by reading `_sweep` first). `_harness_cell` in `cli_harness.py`:

```python
    if rec is None:
        return ""
    if rec.get("origin") == "terminal":
        return f"terminal {rec['pid']}"
    return f"{rec['harness']} {rec['pid']}"
```

- [ ] **Step 4: Write the stop-helper test**
```python
def test_stop_terminal_run_kills_and_clears(isolated_config):
    import signal
    p = subprocess.Popen(["sleep", "30"])
    try:
        store.record_harness_run("jira:S", "omp", "/tmp/wt-s", origin="terminal", pid=p.pid)
        assert store.stop_terminal_run("jira:S") is True
        assert store.active_harness("jira:S") is None
        assert p.poll() is not None
    finally:
        try:
            p.kill()
        except OSError:
            pass
```

- [ ] **Step 5: Implement `stop_terminal_run`**

```python
def stop_terminal_run(key: str) -> bool:
    """SIGTERM→SIGKILL the terminal PID behind *key*; False when no live terminal lock."""
    import signal as _signal
    import time as _time
    data = load_harnesses()
    rec = data.get(key)
    if not isinstance(rec, dict) or rec.get("origin") != "terminal":
        return False
    pid = rec.get("pid", -1)
    if not isinstance(pid, int) or not _pid_alive(pid):
        clear_harness_run(key)
        return False
    try:
        os.kill(pid, _signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        clear_harness_run(key)
        return False
    for _ in range(50):
        if not _pid_alive(pid):
            break
        _time.sleep(0.2)
    else:
        try:
            os.kill(pid, _signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    clear_harness_run(key)
    return True
```

Note: start-time PID-reuse guard (`/proc/<pid>` vs `started_at`) goes in Task 5's serve-boot reap; document the gap in a comment.

- [ ] **Step 6: Run store tests**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_store.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/workagent/store.py src/workagent/cli_harness.py tests/test_store.py
git commit -m "Add terminal origin to harness lock plus stop helper"
```

### Task 4: CLI `--terminal` flag + web mirror

**Files:**
- Modify: `src/workagent/cli_start.py:36-51`, `src/workagent/cli_review.py:208-229`
- Modify: `src/workagent/cli_harness.py:77-174`
- Modify: `src/workagent/web_args.py:36-66`
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_cli_start.py`, `tests/test_webapp_data.py`

**Interfaces:**
- Consumes: `record_preview_session` (Task 1), lock origin (Task 3), shared terminal-spawn helper (extract `_open_terminal_command` core from `web_runs.py:378-428` into `cli_harness.spawn_in_terminal(cmd, cwd)` — pure move, no behavior change).
- Produces: `start`/`review --terminal` (XOR with `--launch`; both → exit 2): persists preview row, detached-spawns harness command in OS terminal, claims lock `origin='terminal'`, prints PID; web `BOOL_FLAGS` mirror so `test_specs_mirror_cli_flags` passes.

- [ ] **Step 1: Write the failing CLI test**

```python
def test_start_terminal_xor_launch(isolated_config, tmp_path, monkeypatch):
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    _start_mocks(monkeypatch, repo_dir)
    monkeypatch.setattr(cli.gitwt, "start_worktree",
                        lambda repo, **kw: {"worktree_path": "/tmp/wt", "branch": "feat/22--x"})
    r = runner.invoke(cli.app, ["start", "o/r#22", "--launch", "--terminal", "--json"])
    assert r.exit_code == 2
    assert "--terminal" in r.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_cli_start.py::test_start_terminal_xor_launch -v`
Expected: FAIL (no such option `--terminal`)

- [ ] **Step 3: Add `--terminal` to start/review + validation**

In both `cli_start.py::start` and `cli_review.py::review` add:

```python
    terminal: bool = typer.Option(False, "--terminal", help="Open the harness command in the OS terminal instead of running headless (mutually exclusive with --launch)."),
```

At the top of each command body:

```python
    if terminal and launch:
        _fail("--terminal cannot be used with --launch (pick one run mode)", EXIT_USAGE)
```

Thread `terminal` through `_start_from_pr`, `_launch_in_worktree`, and `_run_harness(harness_name, prompt, worktree, fallback_dir, no_tty, launch, result, json_output, run_key, session_file, terminal=terminal)`.

- [ ] **Step 4: Implement terminal branch in `_run_harness`**

```python
    if terminal:
        sid = record_preview_session(run_key or str(result.get("key", "")), str(result.get("command", harness_name)), prompt, harness_name, f"{run_key}: terminal")
        if sid:
            result["session_id"] = sid
        blocker = store.record_harness_run(run_key, harness_name, worktree or fallback_dir, origin="terminal") if run_key else None
        if blocker is not None:
            _fail(f"worktree {worktree or fallback_dir} already has a live harness ({blocker['harness']}, pid {blocker['pid']}) — wait for it to finish or kill it", 1)
        spawn_in_terminal(full_cmd, worktree or fallback_dir)
        result["terminal_pid"] = store.active_harness(run_key or "")["pid"] if run_key else None
        eprint(f"terminal agent pid: {result['terminal_pid']}")
        _print_result(result, json_output)
        return
```

Place before the `if not launch:` branch. `full_cmd` reuse: compute the preview `full_cmd` first and share it.

- [ ] **Step 5: Mirror in web_args + contract**

Add `"--terminal"` to `BOOL_FLAGS["start"]` and `BOOL_FLAGS["review"]` in `src/workagent/web_args.py`. Append to `web/API_CONTRACT.md` runs section: ``--terminal` (start/review): open the harness command in the OS terminal (XOR `--launch`); claims the terminal harness lock; Dashboard shows a blue dot + Stop.`` Mirror line in `web/FEATURE_INVENTORY.md`.

- [ ] **Step 6: Run parity + CLI tests**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp_data.py::test_specs_mirror_cli_flags tests/test_cli_start.py tests/test_cli_review.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/workagent/cli_start.py src/workagent/cli_review.py src/workagent/cli_harness.py src/workagent/web_args.py web/API_CONTRACT.md web/FEATURE_INVENTORY.md tests/test_cli_start.py tests/test_webapp_data.py
git commit -m "Add --terminal run-mode to start/review with web mirror"
```

### Task 5: Terminal spawn path in serve + stop endpoint

**Files:**
- Modify: `src/workagent/web_runs_routes.py:51-110`
- Modify: `src/workagent/webapp.py:202-211`
- Modify: `web/API_CONTRACT.md`
- Test: `tests/test_webapp_runs.py` (new tests appended)

**Interfaces:**
- Consumes: `--terminal` passthrough (Task 4), `store.record_harness_run/stop_terminal_run/active_harness` (Task 3), `_open_terminal_command` shared helper (Task 4).
- Produces: `POST /api/runs` with `--terminal` in args spawns detached terminal (no child run registered as running; run row records terminal PID); `POST /api/terminal/stop {"worktree_ref"}` kills + clears (404 no live terminal lock); serve boot reaps dead terminal locks; status `harness` cell already renders `"terminal <pid>"` via Task 3.

- [ ] **Step 1: Write the failing test**

```python
def test_create_run_terminal_spawns_without_child(client, monkeypatch):
    import workagent.web_runs_routes as routes
    spawned = []
    monkeypatch.setattr(routes, "_spawn", lambda run, registry: spawned.append(run))
    monkeypatch.setattr("workagent.web_runs._open_terminal_command", lambda cmd, cwd="": spawned.append(("term", cmd)))
    r = client.post("/api/runs", json={"command": "start", "args": ["IPG-1", "--repo", "r", "--terminal"], "confirm": True})
    assert r.status_code == 202
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp_runs.py::test_create_run_terminal_spawns_without_child -v`
Expected: FAIL (terminal not handled; child spawned instead)

- [ ] **Step 3: Implement terminal branch in `create_run`**

After `run.argv = argv + tail` in `web_runs_routes.py:108`: when `"--terminal"` is in the effective args for start/review, resolve the harness command: for preview-shaped terminal runs the CLI child itself performs the spawn — so keep spawning the child (the CLI owns terminal logic per parity checklist) and skip the `--session-file` mint? No: keep minting (Copy/Resume wiring), child CLI handles `--terminal`. Minimal correct change: allow `--terminal` through validation (already, Task 4) and record `run.terminal = True` so the summary shows the mode. The stop endpoint + reap are the real new backend work:

```python
    @app.post("/api/terminal/stop")
    async def terminal_stop(body: dict) -> dict:
        from . import store as _store
        ref = str((body or {}).get("worktree_ref", ""))
        if not ref:
            raise ApiError("bad_arg", "worktree_ref is required", 400)
        if not _store.stop_terminal_run(ref):
            raise ApiError("not_found", f"no live terminal agent for {ref}", 404)
        return {"worktree_ref": ref, "stopped": True}
```

- [ ] **Step 4: Serve-boot reap**

In `webapp.py` `create_app`, after registry creation: iterate `store.load_harnesses()` (which already sweeps dead PIDs) — no code needed beyond a comment asserting the sweep covers terminal origins. Verify `_sweep` handles all origins; add test:

```python
def test_boot_reaps_dead_terminal_lock(isolated_config):
    store.save_harnesses_raw({"jira:OLD": {"harness": "omp", "pid": 999999999, "started_at": 0.0, "worktree": "/wt", "origin": "terminal"}})
    assert store.active_harness("jira:OLD") is None
```

- [ ] **Step 5: Contract entry**

Append to `web/API_CONTRACT.md`: `### POST /api/terminal/stop → {"worktree_ref", "stopped": true}` + 404 `not_found` when no live terminal lock.

- [ ] **Step 6: Run backend tests**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_webapp_runs.py tests/test_webapp_data.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/workagent/web_runs_routes.py src/workagent/webapp.py web/API_CONTRACT.md tests/test_webapp_runs.py
git commit -m "Add terminal stop endpoint and boot reap"
```

### Task 6: PR-aware default repo for Review prefill

**Files:**
- Modify: `src/workagent/trackers.py:161-216`
- Test: `tests/test_trackers.py` (new file — does not exist yet; create it, mirroring `tests/test_cli_start.py` style with `isolated_config`)

**Interfaces:**
- Consumes: `refs.parse_ref/fetch_pr_info`, `worktrees.resolve_worktree`, `store.load_links` (all existing).
- Produces: `default_repo_for_ref` resolves `kind in (pr, mr)` refs via `fetch_pr_info` head_ref → linked-worktree repo before falling through to existing rules; fetch failure → existing rules. No new endpoint.

- [ ] **Step 1: Write the failing test**

```python
def test_default_repo_for_pr_url_resolves_head_worktree(isolated_config, tmp_path, monkeypatch):
    from workagent import store, trackers, refs
    wt = tmp_path / "wt"
    wt.mkdir()
    store.record_link("branch:feat/33", {"branch": "feat/33", "worktree": str(wt), "repo": "/repo/proj"})
    monkeypatch.setattr(refs, "fetch_pr_info", lambda parsed, cwd=None: {"head_ref": "feat/33"})
    assert trackers.default_repo_for_ref("https://github.com/o/r/pull/33") == "/repo/proj"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_trackers.py::test_default_repo_for_pr_url_resolves_head_worktree -v`
Expected: FAIL (returns "")

- [ ] **Step 3: Implement PR branch in `default_repo_for_ref`**

After the Rule 0 worktree resolution (line 173-176), insert:

```python
    try:
        parsed = refs.parse_ref(issue_ref)
        if parsed.get("kind") in ("pr", "mr"):
            try:
                head = (refs.fetch_pr_info(parsed) or {}).get("head_ref", "")
            except Exception:
                head = ""
            if head:
                resolved_head = _worktrees.resolve_worktree(head, links)
                if isinstance(resolved_head, str):
                    repo = str((links.get(resolved_head) or {}).get("repo", ""))
                    if repo:
                        return repo
    except Exception:
        pass
```

Then fall through to the existing `want`/tracker logic unchanged.

- [ ] **Step 4: Fetch-failure fallback test**

```python
def test_default_repo_for_pr_url_fetch_failure_falls_through(isolated_config, monkeypatch):
    from workagent import trackers, refs
    def _boom(parsed, cwd=None):
        raise Exception("no auth")
    monkeypatch.setattr(refs, "fetch_pr_info", _boom)
    assert trackers.default_repo_for_ref("https://github.com/o/r/pull/33") == ""
```

- [ ] **Step 5: Run tracker tests**

Run: `cd /home/bs/projects/personal/harness && uv run pytest tests/test_trackers.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/workagent/trackers.py tests/test_trackers.py
git commit -m "Resolve Review repo prefill from PR head worktree"
```

### Task 7: Launch run-mode selector + PR auto-switch

**Files:**
- Modify: `web/src/lib/launchConfig.ts`
- Modify: `web/src/components/StartForm.tsx`
- Modify: `web/src/pages/Launch.tsx`
- Modify: `web/src/components/CandidatesCard.tsx` (StartDialog: run-mode selector + PR-switch note)
- Test: frontend `tsc` + `vite build` (no unit framework — verify by typecheck + build + manual smoke)

**Interfaces:**
- Consumes: `GET /api/default-repo?ref=` (existing), `--terminal` CLI arg (Task 4).
- Produces: `LaunchForm.runMode: 'preview' | 'headless' | 'terminal'` (replaces `launch: boolean`; keep `launch` as derived alias during migration or remove cleanly — clean cutover: remove); Start ref matching PR-shape auto-switches to Review with toast; Review repo effect unchanged (now PR-aware via Task 6).

- [ ] **Step 1: Replace `launch` with `runMode` in launchConfig**

```ts
export type RunMode = "preview" | "headless" | "terminal"
export interface LaunchForm {
  ref: string
  repo: string
  depth: string
  base: string
  runMode: RunMode
  fixComments: boolean
  newFixSession: boolean
  merge: boolean
  extraPrompt: string
}
export const INITIAL: LaunchForm = {
  ref: "", repo: "", depth: "7", base: "",
  runMode: "preview",
  fixComments: false, newFixSession: false, merge: false, extraPrompt: "",
}
export function runModeArgs(mode: RunMode): string[] {
  return mode === "headless" ? ["--launch"] : mode === "terminal" ? ["--terminal"] : []
}
```

Update `buildStartArgs` in `StartForm.tsx:189-202` to use `runModeArgs(form.runMode)` instead of the `form.launch` conditional. Update every `form.launch` reader in `Launch.tsx` (buildArgs, flagList, confirm warning, submit gating) to `form.runMode`.

- [ ] **Step 2: Typecheck**

Run: `cd /home/bs/projects/personal/workagent/web && npx tsc --noEmit`
Expected: PASS (fix every `form.launch` reference; none remain: grep `\.launch\b` in web/src).

- [ ] **Step 3: Add PR-shape detector + auto-switch**

In `web/src/lib/launchConfig.ts`:

```ts
/** True when the ref looks like a PR/MR (mirrors refs.py URL shapes). */
export function isPrShaped(ref: string): boolean {
  const t = ref.trim()
  return /\/pull\/\d+\/*$/.test(t) || /\/-\/merge_requests\/\d+\/*$/.test(t) || /^[^/\s]+\/[^/\s#]+#\d+$/.test(t)
}
```

In `StartFormFields` (Start tab) and `Launch.tsx` Start mode: on ref change, when `isPrShaped(v)` and current mode is start → `setMode("review")` + toast `"PR/MR ref detected — switched to Review"`. Never auto-switch back. Never touch `form.repo` (the Review effect prefills it).

- [ ] **Step 4: Replace Run-agent checkbox with run-mode selector**

In `StartForm.tsx` and `Launch.tsx` review section, replace the `CheckRow` "Run agent now" with a three-option segmented control (Preview / Run headless / Run in terminal) bound to `form.runMode`. Sync tab untouched. StartDialog in `CandidatesCard.tsx` gets the same selector (it shares `useStartForm`).

- [ ] **Step 5: Build**

Run: `cd /home/bs/projects/personal/workagent/web && npm run build`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/launchConfig.ts web/src/components/StartForm.tsx web/src/pages/Launch.tsx web/src/components/CandidatesCard.tsx
git commit -m "Add Launch run-mode selector and PR auto-switch"
```

### Task 8: Dashboard terminal dot + Stop

**Files:**
- Modify: `web/src/components/StatusTableMain.tsx:307-313`
- Modify: `web/src/lib/api.ts`, `web/src/lib/queries.ts`
- Modify: `web/src/pages/Dashboard.tsx` (Stop button wiring)
- Modify: `web/src/components/WorktreeDetail.tsx:140-147` (terminal label)

**Interfaces:**
- Consumes: status `harness` cell (`"terminal <pid>"` vs `"omp <pid>"`, Task 3), `POST /api/terminal/stop` (Task 5).
- Produces: terminal origins render a blue blinking dot (`bg-sky-500`, tooltip `terminal <pid>`); headless stays emerald; row Stop button kills + invalidates status; WorktreeDetail labels the harness row origin.

- [ ] **Step 1: Terminal dot in StatusTableMain**

```tsx
{entry.harness ? (
  <span
    aria-label={`Live harness: ${entry.harness}`}
    title={`Live harness: ${entry.harness}`}
    className={entry.harness.startsWith("terminal ")
      ? "size-2 shrink-0 animate-pulse rounded-full bg-sky-500"
      : "size-2 shrink-0 animate-pulse rounded-full bg-emerald-500"}
  />
) : null}
```

- [ ] **Step 2: Stop API + hook**

`api.ts`: `stopTerminal: (worktreeRef: string) => request<{ worktree_ref: string; stopped: boolean }>("/api/terminal/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ worktree_ref: worktreeRef }) })`. `queries.ts`: `useStopTerminal()` mutation invalidating `queryKeys.status`.

- [ ] **Step 3: Stop button on terminal rows**

In `Dashboard.tsx` row actions (mirror the existing per-row action pattern): render Stop only when `entry.harness?.startsWith("terminal ")`, calling `useStopTerminal` with the row key, toast on success/error.

- [ ] **Step 4: Build + typecheck**

Run: `cd /home/bs/projects/personal/workagent/web && npx tsc --noEmit && npm run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/components/StatusTableMain.tsx web/src/lib/api.ts web/src/lib/queries.ts web/src/pages/Dashboard.tsx web/src/components/WorktreeDetail.tsx
git commit -m "Show terminal agents as blue dot with Stop"
```

### Task 9: Full verification + changelog + rollout

**Files:**
- Modify: `CHANGELOG.md` (gap-insert only)
- Test: full suite

**Interfaces:**
- Consumes: all tasks above.
- Produces: green suite, built frontend, changelog entry, re-installed workagent.

- [ ] **Step 1: Full backend suite**

Run: `cd /home/bs/projects/personal/harness && uv run pytest -q`
Expected: PASS (no failures)

- [ ] **Step 2: Frontend build**

Run: `cd /home/bs/projects/personal/workagent/web && npm run build`
Expected: PASS

- [ ] **Step 3: Changelog entry**

Gap-insert under `## [Unreleased]` (read the file head first for the anchor line): `- Persist preview sessions, PR auto-switch to Review, terminal run-mode with blue dot and Stop [xxx]` (replace xxx with the issue ID; keep <= 150 chars).

- [ ] **Step 4: Install + smoke**

Run: `cd /home/bs/projects/personal/workagent && ./install.sh`
Expected: PASS; then restart serve and verify: preview run lists a session, PR paste switches tabs, terminal run shows blue dot + Stop works.

- [ ] **Step 5: Commit and push**

```bash
git add CHANGELOG.md
git commit -m "Ship preview sessions, PR handoff, terminal mode [xxx]"
git push
```
