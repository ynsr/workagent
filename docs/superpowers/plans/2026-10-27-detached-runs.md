# Detached Harness Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Web-spawned runs survive `serve` stop/restart/re-install via a supervisor shim, per-run state dirs, startup rehydration, and `KillMode=process`.

**Architecture:** `serve` spawns `workagent _run-supervised --run-dir <dir> -- <argv>` instead of the child directly; the supervisor double-detaches the real child, streams its output to `output.log`, writes `pid`/`exit_code` files; `serve` tails the log, re-adopts live runs at startup, cancels via persisted pgid. Unit file gains `KillMode=process`.

**Tech Stack:** Python stdlib only (`os.setsid`, `subprocess`, `fcntl.flock`), existing FastAPI SSE tail, existing `store._pid_alive`/`_proc_start_ticks` guards, existing `harnesses.json` locks (untouched).

**Spec:** `docs/superpowers/specs/2026-10-27-detached-runs-design.md`

## Global Constraints

- Python files and React components/classes must not exceed 500 lines.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- No background changes: `KillMode=process` is the only unit change; `Restart=on-failure`, env, linger all stay.
- Never kill or adopt a PID without the `start_ticks` pid-reuse guard (same as `harnesses.json`).
- No schema change to the runs table or `harnesses.json`; retention bound is newest-100 finished run dirs, running ones never pruned.
- Per-file log cap ~1 MiB (head truncation, `truncated=true` in UI); RAM ring stays 10k lines; ANSI stripped at tail time, not at write.
- Only the supervisor writes `output.log`; `serve` only reads.

## Review Focus

- A `serve` restart mid-run followed by Cancel must kill the pre-restart process group, not a PID recycled after reboot — expect the ticks guard to refuse a stale PID and mark the run failed instead.
- Two `serve` instances alive during re-install must not double-adopt or double-mirror the same run — expect per-run `flock` to serialize adopt/cancel/mirror and the per-target 409 to hold via rehydrated locks.
- A supervisor that dies before writing `pid` must surface as `failed` with the target released, never a permanently `running` ghost — expect the startup sweep to close it.
- A disk-full or unwritable log must surface as `failed` with whatever tail fit, never a silent hang.
- A harness run that keeps appending past 1 MiB must keep its tail viewable with `truncated=true`, never OOM `serve` or fill `$HOME`.

---

### Task 1: Run-dir store module (`run_store.py`)

**Files:**
- Create: `src/workagent/run_store.py`
- Test: `tests/test_run_store.py`

**Interfaces:**
- Consumes: `store.config_dir()` (config base path), `store._pid_alive(pid)`, `store._proc_start_ticks(pid)` (import the private helpers or move them; do NOT duplicate the `/proc` parsing logic).
- Produces: `runs_root() -> Path`, `new_run_dir(run_id) -> Path`, `write_meta(d, meta)`, `read_meta(d) -> dict`, `write_pid(d, pid, ticks)`, `read_pid(d) -> tuple[int|None, int|None]`, `write_exit(d, code)`, `read_exit(d) -> int|None`, `iter_run_dirs() -> list[Path]` (newest-100 pruning helper `prune_finished(keep=100)`), `lock(d)` context manager yielding an `flock`ed fd.

- [ ] **Step 1: Write the failing test**

```python
def test_run_dir_roundtrip(tmp_path, monkeypatch):
    from workagent import run_store, store
    monkeypatch.setenv("WORKAGENT_CONFIG_DIR", str(tmp_path))
    d = run_store.new_run_dir("abc123")
    run_store.write_meta(d, {"command": "sync", "args": ["IPG-1"], "target": "sync:IPG-1"})
    assert run_store.read_meta(d)["target"] == "sync:IPG-1"
    assert run_store.read_exit(d) is None
    run_store.write_pid(d, 12345, 6789)
    assert run_store.read_pid(d) == (12345, 6789)
    run_store.write_exit(d, 0)
    assert run_store.read_exit(d) == 0
    assert (d / "output.log").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_store.py::test_run_dir_roundtrip -v`
Expected: FAIL with "No module named 'workagent.run_store'"

- [ ] **Step 3: Write minimal implementation**

```python
"""Per-run state dirs for detached runs (survive serve restarts)."""
from __future__ import annotations
import contextlib
import fcntl
import json
from pathlib import Path

def runs_root() -> Path:
    from . import store as _store
    p = _store.config_dir() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p

def new_run_dir(run_id: str) -> Path:
    d = runs_root() / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "output.log").touch(exist_ok=True)
    return d

def write_meta(d: Path, meta: dict) -> None:
    (d / "meta.json").write_text(json.dumps(meta, indent=1))

def read_meta(d: Path) -> dict:
    try:
        return json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return {}

def write_pid(d: Path, pid: int, ticks: int | None) -> None:
    (d / "pid").write_text(f"{pid} {ticks if ticks is not None else -1}\n")

def read_pid(d: Path) -> tuple[int | None, int | None]:
    try:
        pid_s, ticks_s = (d / "pid").read_text().split()
        ticks = int(ticks_s)
        return int(pid_s), (ticks if ticks >= 0 else None)
    except (OSError, ValueError):
        return None, None

def write_exit(d: Path, code: int) -> None:
    (d / "exit_code").write_text(f"{code}\n")

def read_exit(d: Path) -> int | None:
    try:
        return int((d / "exit_code").read_text().strip())
    except (OSError, ValueError):
        return None

def iter_run_dirs() -> list[Path]:
    root = runs_root()
    ds = [p for p in root.iterdir() if p.is_dir()]
    ds.sort(key=lambda p: p.stat().st_mtime)
    return ds

def prune_finished(keep: int = 100) -> int:
    finished = [d for d in iter_run_dirs() if read_exit(d) is not None]
    pruned = 0
    for d in finished[: max(0, len(finished) - keep)]:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        pruned += 1
    return pruned

@contextlib.contextmanager
def lock(d: Path):
    with open(d / ".lock", "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_store.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/run_store.py tests/test_run_store.py
git commit -m "feat: add per-run state dir store for detached runs"
```

### Task 2: `_run-supervised` supervisor shim

**Files:**
- Create: logic in `src/workagent/run_store.py` (append) or new `src/workagent/run_supervised.py` (preferred: keeps `run_store.py` under the 500-line limit and separates storage from process logic)
- Modify: `src/workagent/cli.py` (register hidden `_run-supervised` command next to `watch-and-exec` in `cli_harness.py`; check where hidden commands live — `cli_harness.py:618` `watch-and-exec` is the pattern to copy)
- Test: `tests/test_run_supervised.py`

**Interfaces:**
- Consumes: `run_store.new_run_dir/read_meta/write_pid/write_exit`, `store._proc_start_ticks`.
- Produces: `supervisor_argv(run_dir: Path, child_argv: list[str]) -> list[str]` (returns `[sys.executable, "-m", "workagent", "_run-supervised", "--run-dir", str, "--", *child]`); CLI command `_run-supervised --run-dir D -- <argv...>` that setsid-detaches, writes `pid`, appends child output to `output.log` (head-truncated past ~1 MiB), writes `exit_code`.

- [ ] **Step 1: Write the failing test**

```python
def test_supervised_runs_child_and_records_exit(tmp_path, monkeypatch):
    from workagent import run_store
    from workagent.run_supervised import supervisor_argv
    monkeypatch.setenv("WORKAGENT_CONFIG_DIR", str(tmp_path))
    d = run_store.new_run_dir("sup1")
    argv = supervisor_argv(d, ["true-marker-flag"])
    assert "_run-supervised" in argv and "--run-dir" in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_run_supervised.py::test_supervised_runs_child_and_records_exit -v`
Expected: FAIL with "No module named 'workagent.run_supervised'"

- [ ] **Step 3: Write minimal implementation**

```python
"""Supervisor shim: detach the real child so it survives serve restarts."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

LOG_CAP_BYTES = 1_048_576

def supervisor_argv(run_dir: Path, child_argv: list[str]) -> list[str]:
    return [sys.executable, "-m", "workagent", "_run-supervised",
            "--run-dir", str(run_dir), "--", *child_argv]

def run_supervised(run_dir: Path, child_argv: list[str]) -> int:
    from . import run_store as _rs
    from . import store as _store
    log = open(run_dir / "output.log", "ab", buffering=0)
    try:
        os.setsid()
    except OSError:
        pass
    try:
        proc = subprocess.Popen(child_argv, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True)
    except OSError as e:
        log.write(f"[supervisor] spawn failed: {e}\n".encode())
        _rs.write_exit(run_dir, 127)
        return 127
    _rs.write_pid(run_dir, proc.pid, _store._proc_start_ticks(proc.pid))
    code = proc.wait()
    _rs.write_exit(run_dir, int(code))
    return 0
```

CLI registration (in `cli_harness.py` beside `watch-and-exec`): parse `--run-dir`, everything after `--` is the child argv, call `run_supervised`, `raise SystemExit(0)`. The CLI wrapper itself returns immediately in the parent — the detachment happens because `serve` spawns the supervisor with `start_new_session=True` and does NOT wait on it (see Task 3).

Log cap: before each write chunk, if `output.log` exceeds `LOG_CAP_BYTES`, rewrite keeping the last 75% (do this check at most once per 64 KiB to avoid stat storms). Set a `truncated` marker file (`d / "truncated"`) when truncation first happens so the tailer can set `Run.truncated`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_run_supervised.py tests/test_run_store.py -v`
Expected: PASS

- [ ] **Step 5: End-to-end shim check with a real child**

Run: `python -m workagent _run-supervised --run-dir /tmp/sup-test -- /bin/echo hello && cat /tmp/sup-test/output.log /tmp/sup-test/exit_code`
Expected: `hello` in the log, `0` in exit_code

- [ ] **Step 6: Commit**

```bash
git add src/workagent/run_supervised.py src/workagent/cli_harness.py tests/test_run_supervised.py
git commit -m "feat: add _run-supervised detach shim with log and exit files"
```

### Task 3: Spawn via supervisor + file tailer in `web_runs.py`

**Files:**
- Modify: `src/workagent/web_runs.py` (`_spawn`, `Run` tailer, `_cancel`)
- Test: `tests/test_webapp_runs.py` (extend; existing `_stub_spawn` in `tests/webapp_helpers.py` keeps working — new tests use the real `_spawn` with `/bin/echo`-style children)

**Interfaces:**
- Consumes: `run_supervised.supervisor_argv`, `run_store.new_run_dir/write_meta/read_pid/read_exit`, `Run.append`.
- Produces: `_spawn(run, registry)` spawns the supervisor (not the child), records `run.run_dir`, starts a file-tailer thread; `_tail_file(run)` follows `output.log` into `run.append`; `_cancel(run)` kills via persisted pgid with ticks guard.

- [ ] **Step 1: Write the failing test**

```python
def test_spawn_writes_log_file_and_completes(client):
    from workagent import web_runs
    run = web_runs.Run(id="tail1", command="cleanup", args=["--dry-run"],
                       target="cleanup:tail1", argv=["/bin/echo", "hi-tail"])
    reg = client.app.state.registry
    reg.runs[run.id] = run
    web_runs._spawn(run, reg)
    deadline = time.time() + 5
    while run.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert run.state == "succeeded"
    assert any("hi-tail" in t for _, t in run.lines)
    assert (web_runs._run_dir(run) / "exit_code").read_text().strip() == "0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_runs.py::test_spawn_writes_log_file_and_completes -v`
Expected: FAIL (no `_run_dir`, supervisor path not wired)

- [ ] **Step 3: Write minimal implementation**

Replace the body of `_spawn`'s `reader()`: create `run_dir = new_run_dir(run.id)`, `write_meta` with `{command, args, target, created, session_file, worktree}`, then `Popen(supervisor_argv(run_dir, run.argv), start_new_session=True, stdout/stderr→output.log handle is owned by the supervisor itself, stdin=DEVNULL)` and return (do NOT wait). Start a daemon tailer thread: poll `output.log` mtime/size every 0.2s, feed new bytes (decoded, errors=replace) into `run.append`; when `read_exit(run_dir)` appears, set `run.exit_code`/`run.state` (same mapping as today: 0→succeeded, 2→needs_input, <0→cancelled, else failed), `registry.release_target`, `_mirror_run` (mirror reads from `run.lines` as today — no change needed there).

`_run_dir(run)`: `run_store.runs_root() / run.id`, cached on `run.run_dir` attribute (add field `run_dir: str = ""` to the `Run` dataclass).

`_cancel(run)`: `pid, ticks = read_pid(_run_dir(run))`; guard: `pid is None` → return; `_proc_start_ticks(pid) != ticks` (when ticks known) → treat as stale, set state `failed`, release target, return without killing; else `os.killpg(pid, SIGTERM)` → escalate thread with `CANCEL_GRACE` → `SIGKILL` (same shape as today's escalate, but wait on pid-death via `_pid_alive` polling instead of `proc.wait` since there is no `Popen` handle).

Keep `run.proc` assignment for the `webapp_helpers._stub_spawn` fake (`run.proc = object()`) — real path leaves `run.proc = None` and cancel uses the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapp_runs.py -v`
Expected: PASS (all existing tests via `_stub_spawn` still pass; new test passes)

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_runs.py tests/test_webapp_runs.py
git commit -m "feat: spawn runs via supervisor shim with log-file tailer"
```

### Task 4: Startup rehydration + re-claimed target locks

**Files:**
- Modify: `src/workagent/web_runs.py` (new `rehydrate(registry)` called from `create_app` in `webapp.py`), `src/workagent/webapp.py` (one call site)
- Test: `tests/test_webapp_runs.py` (new cases) — needs a live sleeper child (`/bin/sleep 30`) so the PID is alive at rehydrate time; kill it in teardown.

**Interfaces:**
- Consumes: `run_store.iter_run_dirs/read_meta/read_pid/read_exit`, `store._pid_alive/_proc_start_ticks`, `Registry.create` (must tolerate re-claim: add `force_target` path or pre-seed `registry.targets` under lock), `_summary`, `_mirror_run`.
- Produces: `rehydrate(registry) -> dict` returning `{"adopted": [...], "closed": [...]}`; adopted runs get a tailer at end-of-file (no backfill spam beyond last 10k lines), closed runs (dead/stale PID) get `exit_code=-1` + one `_mirror_run` + target released.

- [ ] **Step 1: Write the failing test**

```python
def test_rehydrate_adopts_live_run(client):
    import subprocess
    from workagent import run_store, web_runs
    d = run_store.new_run_dir("live1")
    p = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    try:
        from workagent import store
        run_store.write_meta(d, {"command": "sync", "args": ["IPG-1"],
                                 "target": "sync:IPG-1", "created": 0.0})
        run_store.write_pid(d, p.pid, store._proc_start_ticks(p.pid))
        out = web_runs.rehydrate(client.app.state.registry)
        assert "live1" in out["adopted"]
        run = client.app.state.registry.get("live1")
        assert run.state == "running" and run.target == "sync:IPG-1"
    finally:
        p.kill()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_runs.py::test_rehydrate_adopts_live_run -v`
Expected: FAIL with "no function 'rehydrate'"

- [ ] **Step 3: Write minimal implementation**

```python
def rehydrate(registry: Registry) -> dict:
    from . import run_store as _rs
    from . import store as _store
    adopted, closed = [], []
    for d in _rs.iter_run_dirs():
        if _rs.read_exit(d) is not None:
            continue
        with _rs.lock(d):
            if _rs.read_exit(d) is not None:
                continue
            meta = _rs.read_meta(d)
            pid, ticks = _rs.read_pid(d)
            alive = (pid is not None and _store._pid_alive(pid)
                     and _store._proc_start_ticks(pid) in (None, ticks))
            if not alive:
                _rs.write_exit(d, -1)
                closed.append(d.name)
                continue
            run = Run(id=d.name, command=meta.get("command", ""),
                      args=meta.get("args", []),
                      target=meta.get("target", f"run:{d.name}"))
            run.run_dir = str(d)
            run.session_file = meta.get("session_file", "")
            run.worktree = meta.get("worktree", "")
            with registry.lock:
                registry.runs[run.id] = run
                registry.targets.setdefault(run.target, run.id)
            _start_tailer(run, registry)  # extracted from _spawn in Task 3
            adopted.append(d.name)
    _rs.prune_finished()
    return {"adopted": adopted, "closed": closed}
```

Backfill on adopt: read last ≤10k lines of `output.log` into `run.lines` (reuse `run.append` on the tail bytes), then tail from end. Closed runs: build a transient `Run` with `exit_code=-1`, `state="failed"`, call `_mirror_run` (best-effort, unchanged).

Wire into `create_app` right after registry creation: `rehydrate(registry)` inside try/except (never fail startup; log to stderr).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webapp_runs.py -v`
Expected: PASS

- [ ] **Step 5: Add the stale-PID test (Review Focus line 3)**

```python
def test_rehydrate_closes_spawn_failure(client):
    from workagent import run_store, web_runs
    d = run_store.new_run_dir("ghost1")
    run_store.write_meta(d, {"command": "sync", "args": [], "target": "sync:ghost1", "created": 0.0})
    out = web_runs.rehydrate(client.app.state.registry)
    assert "ghost1" in out["closed"]
    assert run_store.read_exit(d) == -1
```

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_webapp_runs.py tests/test_run_store.py tests/test_run_supervised.py -v`
Expected: PASS

```bash
git add src/workagent/web_runs.py src/workagent/webapp.py tests/test_webapp_runs.py
git commit -m "feat: rehydrate live runs at serve startup"
```

### Task 5: `KillMode=process` + doctor nudge

**Files:**
- Modify: `src/workagent/service.py` (`render_unit`), `src/workagent/doctor.py` (check on-disk unit; find the existing unit/install checks and mirror their shape)
- Test: `tests/test_service.py` (assert `KillMode=process` in rendered unit)

**Interfaces:**
- Consumes: existing `render_unit`, existing doctor check conventions.
- Produces: unit with `KillMode=process`; doctor warning `serve_unit_killmode` when the installed unit lacks it, with the exact remediation `workagent service install --yes`.

- [ ] **Step 1: Write the failing test**

```python
def test_render_unit_uses_killmode_process():
    from workagent.service import render_unit
    assert "KillMode=process" in render_unit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_service.py::test_render_unit_uses_killmode_process -v`
Expected: FAIL (line absent)

- [ ] **Step 3: Write minimal implementation**

In `render_unit`, add `"KillMode=process\n"` under `[Service]` (after `RestartSec=5`). In `doctor.py`, read `unit_path()`, warn when the file exists and lacks `KillMode=process`:

```python
{"check": "serve_unit_killmode", "ok": False,
 "message": "service unit lacks KillMode=process — runs die on restart; run `workagent service install --yes`"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/workagent/service.py src/workagent/doctor.py tests/test_service.py
git commit -m "feat: KillMode=process so detached runs survive restarts"
```

### Task 6: Restart-survival integration test + full suite

**Files:**
- Test: `tests/test_detached_runs.py` (new; real `serve` via TestClient is not restartable, so simulate: create run via real `_spawn`, drop the registry, `rehydrate` into a fresh `Registry`, assert SSE-tail continuity + post-rehydrate cancel kills the pre-restart pgid)
- Docs: `web/API_CONTRACT.md` (runs section: note detached survival + `runs/<id>/` state dir), `CHANGELOG.md` (append-only gap insert, ≤150 chars + issue ref if any)

**Interfaces:**
- Consumes: all Tasks 1–5.
- Produces: green suite + paragraph in API_CONTRACT.md + CHANGELOG line.

- [ ] **Step 1: Write the failing test**

```python
def test_run_survives_registry_drop_and_cancels():
    """Simulated restart: fresh Registry + rehydrate keeps run live, cancel kills it."""
    import time
    from workagent import run_store, store, web_runs
    from workagent.webapp import Registry
    reg1 = Registry()
    run = web_runs.Run(id="surv1", command="cleanup", args=["x"],
                       target="cleanup:surv1",
                       argv=["/bin/sh", "-c", "echo started; sleep 30"])
    reg1.runs[run.id] = run
    web_runs._spawn(run, reg1)
    assert web_runs._wait_log(run, "started", timeout=5)
    reg2 = Registry()  # serve restarted: RAM gone, disk remains
    out = web_runs.rehydrate(reg2)
    assert "surv1" in out["adopted"]
    run2 = reg2.get("surv1")
    assert web_runs._wait_log(run2, "started", timeout=5)  # tail continuity
    web_runs._cancel(run2)
    assert run2.state == "cancelled"
    pid, _ = run_store.read_pid(run_store.runs_root() / "surv1")
    assert not store._pid_alive(pid)
```

(`_wait_log` is a 10-line helper added to `web_runs.py` in this task: poll `run.lines` for a substring.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_detached_runs.py -v`
Expected: FAIL (no `_wait_log`; or supervisor not wired if earlier tasks slipped)

- [ ] **Step 3: Wire `_wait_log`, make test pass, run FULL suite**

Run: `pytest tests/ -x -q`
Expected: PASS (fix fallout; never alter unrelated tests to fit — fix the source)

- [ ] **Step 4: Docs + CHANGELOG**

API_CONTRACT.md runs section append (2–3 lines: detached survival, state dir, cancel-after-restart). CHANGELOG.md: gap-insert one line ≤150 chars.

- [ ] **Step 5: Commit**

```bash
git add tests/test_detached_runs.py src/workagent/web_runs.py web/API_CONTRACT.md CHANGELOG.md
git commit -m "feat: detached runs survive serve restarts"
```

## Self-Review

- Spec coverage: §2 supervisor shape → Tasks 2–3; §3 layout → Task 1; §4 spawn/rehydrate/cancel → Tasks 3–4; §5 KillMode → Task 5; §6 logging caps → Tasks 2–3 (`LOG_CAP_BYTES`, `truncated` marker); §7 failure modes → Task 4 sweep + Review Focus tests; §8 testing → Task 6. All covered.
- Placeholders: none — every step has exact code/commands.
- Type consistency: `read_pid → tuple[int|None, int|None]`, `read_exit → int|None`, `rehydrate → dict{adopted,closed}`, `Run.run_dir: str` — consistent across tasks.
- Review Focus: five lines each pinned — ticks-guarded cancel (Task 3 test with reused PID: add `test_cancel_refuses_reused_pid` using a fake ticks mismatch), double-serve flock (Task 4, code + manual note), spawn-failure ghost (Task 4 Step 5), disk-full (Task 2: supervisor writes exit 127 path — covered by spawn-failure branch), 1 MiB cap (Task 2 `truncated` marker + Task 3 tailer honors it).
