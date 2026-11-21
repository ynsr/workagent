# ACP Harness Fixes & Spec Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all runtime bugs and spec-vs-implementation drifts from PR #123 (`ff8b1ab`) so ACP execution, supervised question answering, post-run validation, CLI/flow forwarding, strict `env.sh` enforcement, web endpoints, and the Run page / UI forms work end-to-end.

**Architecture:** Fix ACP transport & IPC at the bottom (`run_supervised.py`, `acp_harness.py`, `harness_summary.py`), unify dual-mode ACP dispatch & validation in `cli_harness.py`, propagate `is_exec_mode` and non-colliding flags across all CLI/flow/release callers and `web_args.py`, restore `ensure_env_file` across all execution/terminal/gate paths, harden the web `/options` and `/input` endpoints + `rehydrate()`, and upgrade the React `AcpTranscript` / `RunDetail` + remaining UI forms.

**Tech Stack:** Python 3.12+ (Typer, FastAPI, stdlib `subprocess`/`signal`/`threading`, pytest via `uv run pytest -v`), TypeScript / React 18 (Vite, `react-markdown`, `remark-gfm`, Vitest via `cd web && npm test`).

**Spec:** [`docs/superpowers/specs/2026-11-21-acp-harness-fixes-design.md`](../specs/2026-11-21-acp-harness-fixes-design.md)

## Global Constraints
- **Test DB/config isolation:** Tests MUST NEVER read or modify real `~/.config/workagent/` or `~/.gemini/antigravity-cli/`. Always use `isolated_config` / `tmp_path`.
- **Stdout vs stderr:** CLI `stdout` carries ONLY data (`--json`/`--csv` or structured `ACP:` events); all warnings/logs/confirmations go to `stderr`.
- **Exit codes:** `0` success, `1` general error, `2` usage / needs-human-input (`WAITING_USER_RESPONSE`, unsupported `--acp`).
- **No secrets in logs:** `AcpClient` stderr capture must never log environment variables or credentials; cap line length and buffer size.
- **Parity check:** `tests/test_webapp_data.py::test_specs_mirror_cli_flags` must pass after every CLI/web flag change.
- **Append-only `CHANGELOG.md`:** Add one new line (≤150 chars, non-technical, ending `[128]`); `git diff CHANGELOG.md` must show only `+` lines and zero `-` lines.
- **No new Python dependencies:** Pure stdlib only on backend. Frontend adds only `react-markdown` and `remark-gfm`.

## Review Focus
1. **Orphaned `<bin> acp` on web cancel:** Sending `SIGTERM` to a running ACP child (as `POST /api/runs/{id}/cancel` does) must unblock `_read_answer` and kill the `<bin> acp` process group cleanly — pinned in Task 1.
2. **Multi-turn ACP transcript verdict extraction:** When a run asks a question, receives an `answer`, and finishes a second turn, `last_assistant_text` must return only the final turn's assistant text (not turn 1 concatenated with turn 2) — pinned in Task 2.
3. **Interactive vs headless `--acp` dispatch:** `--acp` without `--launch` must never fall into preview mode; `no_tty=True` must run `validate_headless` with `VERDICT_SUFFIX`, while `no_tty=False` must run `launch_acp` interactively reading `sys.stdin` — pinned in Task 3.
4. **Stale `pending_question` on finished/cancelled run or server restart:** `GET /api/runs/{id}` must return `pending_question: null` once a run exits/fails/cancels, and must surface `pending_question` immediately on the first read after `rehydrate()` — pinned in Task 7.
5. **Malformed first line & `{label}` options in `AcpTranscript`:** Head-truncated `output.log` (invalid JSON on line 0) must be skipped without breaking subsequent chunk coalescing, `toolCallId` merging, or `{label}` option normalization — pinned in Task 9.

---

### Task 1: Supervised `WORKAGENT_RUN_DIR`, `AcpClient` Stderr Ring Buffer, SIGTERM Cleanup & `_read_answer`

**Files:**
- Modify: `src/workagent/run_supervised.py:34-40`
- Modify: `src/workagent/acp_harness.py:24-40, 81-115, 235-274, 429-585`
- Test: `tests/test_acp.py`

**Interfaces:**
- Produces:
  - `AcpClient.stderr_tail(self, max_lines: int = 20) -> str`
  - `run_supervised(run_dir, child_argv)` exports `WORKAGENT_RUN_DIR=str(run_dir)` in child `env`
  - `AcpHarness._read_answer(ev, input_stream, no_tty)` precedence: `WORKAGENT_RUN_DIR` → `input_stream` → `sys.stdin` (when `not no_tty`) → `None` (when `no_tty`, yielding `WAITING_USER_RESPONSE` exit `2`)

- [ ] **Step 1: Write failing tests in `tests/test_acp.py`**

Append to `tests/test_acp.py`:

```python
def test_run_supervised_exports_workagent_run_dir(tmp_path):
    """run_supervised sets WORKAGENT_RUN_DIR=<run_dir> in child env."""
    import sys
    from workagent.run_supervised import run_supervised

    run_dir = tmp_path / "run-128"
    out_file = tmp_path / "seen_env.txt"
    code_str = (
        "import os, pathlib; "
        f"pathlib.Path({str(out_file)!r}).write_text(os.environ.get('WORKAGENT_RUN_DIR', ''))"
    )
    rc = run_supervised(run_dir, [sys.executable, "-c", code_str])
    assert rc == 0
    assert out_file.read_text() == str(run_dir)


def test_acp_client_captures_stderr_tail_on_init_failure(tmp_path, isolated_config):
    """Init failure surfaces stderr tail in HarnessError (no AttributeError)."""
    import sys
    import pytest
    from workagent.acp_harness import OmpAcpHarness
    from workagent.errors import HarnessError

    bad_agent = tmp_path / "bad_agent.py"
    bad_agent.write_text(
        "import sys\n"
        "sys.stderr.write('boom line 1\\nboom line 2\\n')\n"
        "sys.stderr.flush()\n"
        "sys.exit(1)\n"
    )
    h = OmpAcpHarness()
    h.acp_argv = lambda: [sys.executable, str(bad_agent)]
    with pytest.raises(HarnessError) as exc_info:
        h.launch_acp("hi", str(tmp_path), no_tty=True)
    msg = str(exc_info.value)
    assert "boom line 2" in msg


def test_read_answer_unattended_no_tty_returns_waiting_exit_2(tmp_path, isolated_config, monkeypatch):
    """no_tty=True without WORKAGENT_RUN_DIR/input_stream returns None -> WAITING_USER_RESPONSE exit 2."""
    import io
    from workagent.agy_harness import AgyAcpHarness

    monkeypatch.delenv("WORKAGENT_RUN_DIR", raising=False)
    h = AgyAcpHarness()
    assert h._read_answer({"qid": "q1"}, input_stream=None, no_tty=True) is None
    # no_tty=False without input_stream reads sys.stdin
    monkeypatch.setattr("sys.stdin", io.StringIO("yes please\n"))
    assert h._read_answer({"qid": "q1"}, input_stream=None, no_tty=False) == "yes please"


def test_launch_acp_sigterm_kills_child_process_group(tmp_path, isolated_config):
    """SIGTERM to child running launch_acp kills <bin> acp with no orphan."""
    import os
    import signal
    import subprocess
    import sys
    import time

    pid_file = tmp_path / "agent.pid"
    slow_agent = tmp_path / "slow_agent.py"
    slow_agent.write_text(
        "import json, os, pathlib, sys, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    if m == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1}}), flush=True)\n"
        "    elif m == 'authenticate':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{}}), flush=True)\n"
        "    elif m == 'session/new':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'sessionId':'s1'}}), flush=True)\n"
        "    elif m == 'session/prompt':\n"
        "        time.sleep(30)\n"
    )
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import sys\n"
        "from workagent.acp_harness import OmpAcpHarness\n"
        "h = OmpAcpHarness()\n"
        f"h.acp_argv = lambda: [sys.executable, {str(slow_agent)!r}]\n"
        f"h.launch_acp('hi', {str(tmp_path)!r}, no_tty=True)\n"
    )
    proc = subprocess.Popen([sys.executable, str(runner)], start_new_session=True)
    deadline = time.time() + 5.0
    while not pid_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert pid_file.exists()
    agent_pid = int(pid_file.read_text().strip())
    os.kill(proc.pid, signal.SIGTERM)
    proc.wait(timeout=5)
    time.sleep(0.2)
    try:
        os.kill(agent_pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, f"agent pid {agent_pid} was orphaned after SIGTERM"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_acp.py -k "run_supervised_exports or stderr_tail or read_answer_unattended or sigterm_kills" -v`
Expected: FAIL (4 failures: empty `WORKAGENT_RUN_DIR`, `AttributeError: '_stderr_tail'`, `HarnessError` instead of `None`, orphaned agent pid).

- [ ] **Step 3: Implement fixes in `src/workagent/run_supervised.py` and `src/workagent/acp_harness.py`**

In `src/workagent/run_supervised.py` lines 34-40, pass `env`:

```python
    child_env = {**os.environ, "WORKAGENT_RUN_DIR": str(run_dir)}
    try:
        proc = subprocess.Popen(child_argv, stdin=subprocess.DEVNULL,
                                stdout=log_fh,
                                stderr=subprocess.STDOUT,
                                env=child_env,
                                start_new_session=True, close_fds=True)
```

In `src/workagent/acp_harness.py`:
1. Import `collections` at the top.
2. In `AcpClient.__init__` (lines 91-110), capture stderr in a bounded `deque(maxlen=40)` drained by a daemon thread:

```python
        try:
            self._proc = subprocess.Popen(
                argv, cwd=workdir or None, env=merged,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                start_new_session=True)
        except OSError as e:
            raise HarnessError(f"cannot spawn {' '.join(argv)}: {e}")
        self._timeout = timeout_s
        self._seq = 0
        self._lock = threading.Lock()
        self._pending: dict[Any, queue.Queue] = {}
        self._notify_cb = on_notification
        self._notify_queue: queue.Queue = queue.Queue()
        self._stderr_lines: collections.deque[str] = collections.deque(maxlen=40)
        self._stderr_lock = threading.Lock()
        self._closed = False
        self.pid = self._proc.pid
        self._reader = threading.Thread(target=self._read_loop,
                                        name="acp-reader", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop,
                                               name="acp-stderr", daemon=True)
        self._stderr_reader.start()
```

3. Add `_stderr_loop` and `stderr_tail` to `AcpClient`:

```python
    def _stderr_loop(self) -> None:
        err = self._proc.stderr
        if err is None:
            return
        try:
            for raw in err:
                line = raw.rstrip("\r\n")
                if not line:
                    continue
                if len(line) > 500:
                    line = line[:500] + "…"
                with self._stderr_lock:
                    self._stderr_lines.append(line)
        except Exception:
            pass

    def stderr_tail(self, max_lines: int = 20) -> str:
        with self._stderr_lock:
            lines = list(self._stderr_lines)[-max_lines:]
        return "\n".join(lines)
```

4. In `AcpHarness.launch_acp` (lines 457-508), install a main-thread `SIGTERM` handler, reset `self._answer_stop = False`, and use `client.stderr_tail()` on init/auth/session/new failures:

```python
        self._answer_stop = False
        client = self._open_client(env_file, workdir, timeout_s)
        client.set_notification_handler(_handle)

        prev_sigterm = None
        installed_sigterm = False
        if threading.current_thread() is threading.main_thread():
            def _on_sigterm(_signum, _frame):
                self._answer_stop = True
                client.close()
                raise SystemExit(143)
            try:
                prev_sigterm = signal.signal(signal.SIGTERM, _on_sigterm)
                installed_sigterm = True
            except (ValueError, OSError):
                installed_sigterm = False

        def _with_stderr(msg: str) -> str:
            tail = client.stderr_tail()
            return f"{msg}\n{tail}" if tail else msg

        try:
            try:
                client.request("initialize", {"protocolVersion": 1})
            except HarnessError as e:
                raise HarnessError(_with_stderr(f"{self.name} ACP init failed: {e}"))
            auth = self.authenticate_method()
            if auth:
                try:
                    client.request("authenticate", {"methodId": auth})
                except HarnessError as e:
                    raise HarnessError(_with_stderr(f"{self.name} ACP auth failed: {e}"))
            try:
                new = client.request("session/new", {"cwd": workdir or None})
            except HarnessError as e:
                raise HarnessError(_with_stderr(f"{self.name} ACP session/new failed: {e}"))
            ...
        finally:
            self._answer_stop = True
            client.close()
            if installed_sigterm and prev_sigterm is not None:
                try:
                    signal.signal(signal.SIGTERM, prev_sigterm)
                except (ValueError, OSError):
                    pass
```

5. Fix `AcpHarness._read_answer` (lines 562-584):

```python
        stream = input_stream if (input_stream is not None and hasattr(input_stream, "readline")) else (
            sys.stdin if not no_tty else None
        )
        if stream is not None:
            try:
                line = stream.readline()
            except Exception:
                line = None
            if line:
                line = line.strip()
                if line:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        item = None
                    if isinstance(item, dict):
                        if not qid or str(item.get("qid") or "") in ("", qid):
                            return item.get("text", item.get("answer", item.get("choice", "")))
                    else:
                        return line
            raise HarnessError("no answer: input stream closed without an answer line")
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_acp.py -v`
Expected: PASS (all existing and 4 new tests green).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/run_supervised.py src/workagent/acp_harness.py tests/test_acp.py
git commit -m "[128] Fix WORKAGENT_RUN_DIR, AcpClient stderr tail, SIGTERM cleanup, and _read_answer"
```

---

### Task 2: Parse Mirrored ACP Transcripts in `harness_summary.py`

**Files:**
- Modify: `src/workagent/harness_summary.py:80-148, 165-223`
- Test: `tests/test_acp.py`

**Interfaces:**
- Produces:
  - `harness_summary.last_assistant_text(transcript, max_chars=4000) -> str` supports ACP-mirrored JSONL (`t == "message" | "turn_end" | "answer"`)
  - `harness_summary.tool_error_rate(transcript, since_line=0) -> tuple[int, int] | None` supports ACP-mirrored JSONL (`t == "tool_call"`, deduplicated by `id`)

- [ ] **Step 1: Write failing tests in `tests/test_acp.py`**

Append to `tests/test_acp.py`:

```python
def test_harness_summary_parses_acp_mirrored_transcript(tmp_path):
    """last_assistant_text and tool_error_rate parse ACP-mirrored JSONL."""
    import json
    from workagent import harness_summary as hs

    p = tmp_path / "acp.jsonl"
    events = [
        {"t": "thought", "id": "s1", "text": "thinking..."},
        {"t": "message", "id": "s1", "text": "Turn 1 question?"},
        {"t": "question", "id": "q1", "qid": "q1", "text": "Proceed?"},
        {"t": "turn_end", "id": "s1"},
        {"t": "answer", "id": "s1", "qid": "q1", "text": "yes"},
        {"t": "tool_call", "id": "tc1", "name": "read", "status": "running", "output": ""},
        {"t": "tool_call", "id": "tc1", "name": "read", "status": "completed", "output": "ok"},
        {"t": "tool_call", "id": "tc2", "name": "bash", "status": "failed", "output": "error: boom"},
        {"t": "message", "id": "s1", "text": "All done.\n"},
        {"t": "message", "id": "s1", "text": "JOB_DONE"},
        {"t": "turn_end", "id": "s1"},
    ]
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    assert hs.last_assistant_text(p, max_chars=None) == "All done.\nJOB_DONE"
    assert hs.tool_error_rate(p) == (1, 2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_acp.py::test_harness_summary_parses_acp_mirrored_transcript -v`
Expected: FAIL (`"" != "All done.\nJOB_DONE"` and `None != (1, 2)`).

- [ ] **Step 3: Implement ACP JSONL parsing in `src/workagent/harness_summary.py`**

In `tool_error_rate` (`src/workagent/harness_summary.py`), add an `acp_tools: dict[str, bool] = {}` map before the `for line in lines:` loop and handle `data.get("t") == "tool_call"` inside the loop:

```python
    acp_tools: dict[str, bool] = {}
    acp_anon_seq = 0
```

Inside `for line in lines:` right after `if not isinstance(data, dict): continue`:

```python
        acp_t = data.get("t")
        if isinstance(acp_t, str):
            if acp_t == "tool_call":
                st = str(data.get("status") or "").strip().lower()
                is_err_status = st in ("error", "failed", "failure")
                is_ok_status = st in ("completed", "done", "success", "ok")
                has_explicit_err = (
                    data.get("isError") is True
                    or data.get("is_error") is True
                    or bool(data.get("error"))
                )
                if is_err_status or is_ok_status or has_explicit_err:
                    tid = str(data.get("id") or "")
                    if not tid:
                        acp_anon_seq += 1
                        tid = f"__anon_{acp_anon_seq}"
                    acp_tools[tid] = is_err_status or _part_is_error(data)
            continue
```

Before `if total:` at the end of `tool_error_rate`:

```python
    for failed in acp_tools.values():
        total += 1
        if failed:
            errors += 1
```

In `last_assistant_text` (`src/workagent/harness_summary.py`), right after `if not isinstance(data, dict): continue`:

```python
        acp_t = data.get("t")
        if isinstance(acp_t, str):
            if acp_t == "message":
                chunk = data.get("text")
                if isinstance(chunk, str) and chunk:
                    stream_deltas.append(chunk)
            elif acp_t == "turn_end":
                if stream_deltas:
                    last = "".join(stream_deltas).strip()
                    stream_deltas.clear()
            elif acp_t == "answer":
                stream_deltas.clear()
                last = ""
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_acp.py::test_harness_summary_parses_acp_mirrored_transcript -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/harness_summary.py tests/test_acp.py
git commit -m "[128] Parse mirrored ACP JSONL in harness_summary"
```

---

### Task 3: Fix `_run_harness` Dual-Mode ACP Dispatch & Post-Run Validation

**Files:**
- Modify: `src/workagent/cli_harness.py:24-30, 135-158, 240-296, 360-395, 505-645`
- Test: `tests/test_acp.py`

**Interfaces:**
- Consumes: `harness.launch_acp(...)` (Task 1), `harness_summary.last_assistant_text` (Task 2)
- Produces: `cli_harness.is_exec_mode(launch: bool, acp: bool = False, run_mode: str | None = None) -> bool`

- [ ] **Step 1: Write failing tests in `tests/test_acp.py`**

Append to `tests/test_acp.py`:

```python
def test_run_harness_acp_no_tty_runs_validate_headless(tmp_path, isolated_config, monkeypatch):
    """_run_harness(acp=True, launch=False, no_tty=True) does NOT preview; runs validate_headless + VERDICT_SUFFIX."""
    from workagent import backend, cli_harness, cli_validate

    calls = {}
    h = backend.get_harness("omp")

    def fake_launch_acp(prompt, workdir, **kw):
        calls["prompt"] = prompt
        calls["kw"] = kw
        sf = kw.get("session_file")
        if sf:
            import json
            from pathlib import Path
            Path(sf).write_text(json.dumps({"t": "message", "text": "Done.\nJOB_DONE"}) + "\n")
        return 0

    monkeypatch.setattr(h, "launch_acp", fake_launch_acp)
    monkeypatch.setattr(cli_validate, "validate_headless",
                        lambda **kw: (calls.setdefault("validated", True),
                                      {"verdict": "JOB_DONE", "attempts": 1,
                                       "runs": [kw["run_once"](kw["prompt"])]})[1])
    res = {"command": "start", "branch": "feat/128", "key": "github:o/r#128"}
    cli_harness._run_harness("omp", "Fix bug", str(tmp_path), str(tmp_path),
                             no_tty=True, launch=False, result=res, json_output=True,
                             acp=True)
    assert calls.get("validated") is True
    assert backend.VERDICT_SUFFIX.strip() in calls["prompt"]
    assert calls["kw"]["input_stream"] is None
    assert calls["kw"]["no_tty"] is True


def test_run_harness_acp_tty_runs_launch_acp_interactively(tmp_path, isolated_config, monkeypatch):
    """_run_harness(acp=True, launch=False, no_tty=False) calls launch_acp with sys.stdin (no validate_headless)."""
    import sys
    from workagent import backend, cli_harness

    calls = {}
    h = backend.get_harness("omp")

    def fake_launch_acp(prompt, workdir, **kw):
        calls["prompt"] = prompt
        calls["kw"] = kw
        return 0

    monkeypatch.setattr(h, "launch_acp", fake_launch_acp)
    res = {"command": "start", "branch": "feat/128", "key": "github:o/r#128"}
    cli_harness._run_harness("omp", "Fix bug", str(tmp_path), str(tmp_path),
                             no_tty=False, launch=False, result=res, json_output=True,
                             acp=True)
    assert calls["kw"]["no_tty"] is False
    assert calls["kw"]["input_stream"] is sys.stdin
    assert backend.VERDICT_SUFFIX.strip() not in calls["prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_acp.py -k "run_harness_acp_" -v`
Expected: FAIL (`KeyError` / `calls` empty because line 363 `if not launch:` takes preview branch).

- [ ] **Step 3: Implement `is_exec_mode` and fix `_run_harness` in `src/workagent/cli_harness.py`**

Add helper right after `append_extra_prompt` in `src/workagent/cli_harness.py`:

```python
def is_exec_mode(launch: bool, acp: bool = False,
                 run_mode: str | None = None) -> bool:
    """True when flags select an executing harness mode (headless or ACP)."""
    mode = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    return bool(launch or acp or mode in ("headless", "acp"))
```

In `_launch_in_worktree` (`src/workagent/cli_harness.py:152`):
```python
    if is_exec_mode(launch, acp, run_mode):
        _guard_harness(key, worktree)
```

In `_run_harness` (`src/workagent/cli_harness.py`):
1. After line 259 (`if mode in ("preview", "terminal"): no_tty = False`), add:
```python
    is_exec = bool(launch or mode == "acp")
```
2. Change line 268 (`if no_tty and launch and not terminal:`) to:
```python
    if no_tty and is_exec and not terminal:
        prompt += backend.VERDICT_SUFFIX
```
3. Before line 363, validate ACP support and change preview check + lock check:
```python
    if mode == "acp" and not getattr(harness, "supports_acp", False):
        from .cli_core import _fail as _fail_acp, EXIT_USAGE as _EXIT_ACP
        _fail_acp(f"harness {harness_name!r} does not support --acp", _EXIT_ACP)
    if not is_exec:
        ...
```
and at line 385:
```python
    if run_key and is_exec:
```
4. Replace lines 511-543 (the old early-returning `if no_tty and mode == "acp":` block) by removing it completely, and update `run_once` inside `if no_tty:` (lines 550-580) + the `else:` branch (lines 627-637):

```python
            def run_once(attempt_prompt):
                nonlocal current_path
                if mode == "acp":
                    code = harness.launch_acp(
                        attempt_prompt, worktree or fallback_dir,
                        model=model, effort=effort,
                        session_file=current_path or None,
                        env_file=str(env_path) if env_path else None,
                        timeout_s=timeout_s,
                        input_stream=None,
                        no_tty=True,
                    )
                    if current_path and Path(current_path).is_file():
                        result["session_file"] = current_path
                        if run_id:
                            _sq.set_run_file_path(db, run_id, current_path)
                    text = (harness.extract_last_assistant_text(current_path, max_chars=None)
                            if current_path else "")
                    return {"exit_code": code, "transcript": text,
                            "transcript_path": current_path}
                flags = harness.session_file_flag(current_path) if current_path else []
                ...
```

And in the `else:` branch (`not no_tty`, line 627):

```python
        elif mode == "acp":
            exit_code = harness.launch_acp(
                prompt, worktree or fallback_dir,
                model=model, effort=effort,
                session_file=session_path or None,
                env_file=str(env_path) if env_path else None,
                timeout_s=timeout_s,
                input_stream=sys.stdin,
                no_tty=False,
            )
            if session_path and Path(session_path).is_file():
                result["session_file"] = session_path
                if run_id:
                    _sq.set_run_file_path(db, run_id, session_path)
        else:
            backend.launch(harness_name, prompt, worktree or fallback_dir, False,
                           (_HARNESS_ARGS + extra) if extra else _HARNESS_ARGS,
                           env_file=str(env_path) if env_path else None, watch=watch,
                           session_file=session_path,
                           model=model, effort=effort,
                           **_launch_kw)
            minted = _sq.newest_transcript(scope_dir) if scope_dir is not None else None
            if minted is not None and minted != before and run_id:
                _sq.set_run_file_path(db, run_id, str(minted))
```

And at line 662 (when `exit_code` is non-zero in `not no_tty` ACP mode where `validation` is `None`):
```python
        if validation is None:
            raise HarnessError(f"acp run failed (exit {exit_code})", exit_code=exit_code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_acp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_harness.py tests/test_acp.py
git commit -m "[128] Fix _run_harness dual-mode ACP dispatch and post-run validation"
```

---

### Task 4: Fix CLI Callers (`start`, `review`, `adhoc`, `sync`, `action`, `message`, `prodtest`) for ACP

**Files:**
- Modify: `src/workagent/cli_start.py:152-170, 270-312, 415-424`
- Modify: `src/workagent/cli_review.py:321-390, 436-460, 585-625, 760-845`
- Modify: `src/workagent/cli_adhoc.py:33-45, 75-84`
- Modify: `src/workagent/cli_sync.py:375-405`
- Modify: `src/workagent/cli_action.py:83-97, 315-408`
- Modify: `src/workagent/cli_message.py:272-308`
- Modify: `src/workagent/cli_prodtest_plan.py:155-185`, `src/workagent/cli_prodtest_run.py:130-165`, `src/workagent/cli_prodtest_checks.py:75-95, 290-335`
- Test: `tests/test_acp.py`

**Interfaces:**
- Consumes: `cli_harness.is_exec_mode` (Task 3)
- Produces: All CLI commands cleanly accept `--acp` / `--run-mode acp` without `launch=True` collisions and forward `--acp`/`--run-mode`/`--model`/`--effort`.

- [ ] **Step 1: Write failing tests in `tests/test_acp.py`**

Append to `tests/test_acp.py`:

```python
def test_cli_action_resolve_mode_accepts_acp():
    from workagent.cli_action import _resolve_mode
    assert _resolve_mode({}, launch=False, terminal=False, acp=True) == "acp"
    assert _resolve_mode({"default_options": {"run_mode": "acp"}}, launch=False, terminal=False) == "acp"
    assert _resolve_mode({}, launch=False, terminal=False, run_mode="acp") == "acp"


def test_cli_adhoc_acp_does_not_collide_with_launch(tmp_path, isolated_config, monkeypatch):
    from workagent import cli_adhoc, store, worktrees
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(store, "load_links", lambda: {"k": {"worktree": str(wt), "repo": str(wt), "active": 1}})
    monkeypatch.setattr(worktrees, "resolve_any", lambda ref, links: ("k", links["k"], None))
    monkeypatch.setattr(worktrees, "is_valid_worktree", lambda p: True)
    seen = {}
    monkeypatch.setattr(cli_adhoc, "_guard_harness", lambda *a, **kw: None)
    monkeypatch.setattr(cli_adhoc, "_run_harness", lambda *a, **kw: seen.update({"args": a, "kw": kw}))
    cli_adhoc.adhoc.__wrapped__(["k"], prompt="hello", launch=False, terminal=False,
                                harness="omp", acp=True, run_mode=None, model="m1", effort="high",
                                extra_prompt=None, env_file=None, session_file=None,
                                dry_run=False, yes=True, json_output=True)
    # positional arg index 5 is `launch` — must be False when acp=True
    assert seen["args"][5] is False
    assert seen["kw"]["acp"] is True
    assert seen["kw"]["model"] == "m1"


def test_cli_review_child_argv_forwards_acp_model_effort():
    from workagent import cli_review
    argv = cli_review._child_argv("github:o/r#1", fix=False, harness="omp",
                                  post_comments=False, acp=True, run_mode="acp",
                                  model="m1", effort="high")
    assert "--acp" in argv
    assert "--model" in argv and "m1" in argv
    assert "--effort" in argv and "high" in argv
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_acp.py -k "cli_action_resolve_mode or cli_adhoc_acp or cli_review_child_argv" -v`
Expected: FAIL.

- [ ] **Step 3: Apply fixes across the 7 CLI modules**

1. **`src/workagent/cli_action.py`** (`_resolve_mode` lines 83-97 and `action_run` lines 318-405):
```python
def _resolve_mode(action: dict, launch: bool, terminal: bool,
                  acp: bool = False, run_mode: str | None = None) -> str:
    """CLI flags win, then default_options.run_mode, then preview."""
    if sum(bool(x) for x in (launch, terminal, acp)) > 1:
        _fail("--launch, --terminal, and --acp are mutually exclusive", EXIT_USAGE)
    explicit = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    if explicit:
        if explicit not in ("preview", "headless", "acp", "terminal"):
            _fail(f"unknown run_mode {run_mode!r}", EXIT_USAGE)
        return explicit
    if acp:
        return "acp"
    if launch:
        return "headless"
    if terminal:
        return "terminal"
    mode = ((action.get("default_options") or {}).get("run_mode") or "preview")
    if mode not in ("preview", "headless", "acp", "terminal"):
        _fail(f"unknown run_mode {mode!r} in default_options", EXIT_USAGE)
    return mode
```
In `action_run`: call `mode = _resolve_mode(action, launch, terminal, acp=acp, run_mode=run_mode)`; set `use_launch = mode == "headless"`, `use_acp = mode == "acp"`, `use_terminal = mode == "terminal"`, `no_tty = mode in ("headless", "acp")`; run `_guard_harness` and `post_execution` when `mode in ("headless", "acp")`.

2. **`src/workagent/cli_adhoc.py`** (lines 33-42, 77-83):
```python
    if sum(bool(x) for x in (launch, terminal, acp)) > 1:
        _fail("--launch, --terminal, and --acp are mutually exclusive", EXIT_USAGE)
    rm = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    mode = rm if rm in ("preview", "headless", "acp", "terminal") else (
        "acp" if acp else ("terminal" if terminal else "headless")
    )
    use_launch = mode == "headless"
    use_acp = mode == "acp"
    use_terminal = mode == "terminal"
```
Pass `use_launch` as `launch`, `terminal=use_terminal`, `acp=use_acp` to `_run_harness`.

3. **`src/workagent/cli_sync.py`** (lines 385-404):
In `_handle_merge_conflict`, compute `use_acp = bool(acp or (run_mode or "").strip().lower() == "acp")` and pass `launch=not use_acp, acp=use_acp` to `_cli._run_harness(...)` in both branches.

4. **`src/workagent/cli_message.py`** (lines 273-305):
```python
    if sum(bool(x) for x in (launch, terminal, acp)) > 1:
        _fail("--launch, --terminal, and --acp are mutually exclusive", EXIT_USAGE)
    rm = (run_mode or "").strip().lower() if isinstance(run_mode, str) else ""
    mode = rm if rm in ("preview", "headless", "acp", "terminal") else (
        "acp" if acp else ("terminal" if terminal else ("headless" if launch else "preview"))
    )
    ...
    use_launch = mode == "headless"
    use_acp = mode == "acp"
    use_terminal = mode == "terminal"
    no_tty_flag = mode in ("headless", "acp")
    _run_harness(harness or store.default_harness(),
                 body, workdir, workdir, no_tty_flag, use_launch, ...,
                 terminal=use_terminal, env_file=env_file,
                 acp=use_acp, run_mode=run_mode, model=model, effort=effort)
```

5. **`src/workagent/cli_start.py`**:
- Import `is_exec_mode` from `.cli_harness`.
- Line 163 (`_launch_in_worktree` in reuse branch): forward `acp=acp, run_mode=run_mode, model=model, effort=effort`.
- Lines 166, 270, 276, 307, 416: replace `if launch:` with `if is_exec_mode(launch, acp, run_mode):`.

6. **`src/workagent/cli_review.py`**:
- Import `is_exec_mode` from `.cli_harness`.
- In `_child_argv` (line 585): accept `acp: bool = False, run_mode: str | None = None, model: str | None = None, effort: str | None = None`; append `--acp` when `acp`, `--run-mode <v>` when `run_mode`, `--model <v>` when `model`, `--effort <v>` when `effort`. Pass them from `_review_all`.
- In `_review_all` (line 616): change `if not launch:` to `if not is_exec_mode(launch, acp, run_mode):`.
- In `_chain_fix_command` / `_run_then_fix` (lines 321-351, 436-455): accept and forward `acp`, `run_mode`, `model`, `effort`; change `if not launch or terminal:` to `if not is_exec_mode(launch, acp, run_mode) or terminal:`.
- Lines 367, 382, 766, 791, 827, 841: replace `if launch:` with `if is_exec_mode(launch, acp, run_mode):`.

7. **`src/workagent/cli_prodtest_plan.py`, `cli_prodtest_run.py`, `cli_prodtest_checks.py`**:
- Import `is_exec_mode` from `.cli_harness` and replace bare `launch` gates (`if launch and no_tty:` / `if not launch:` / `if launch:`) with `is_exec_mode(launch, acp, run_mode)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_acp.py tests/test_cli_message.py tests/test_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_start.py src/workagent/cli_review.py src/workagent/cli_adhoc.py src/workagent/cli_sync.py src/workagent/cli_action.py src/workagent/cli_message.py src/workagent/cli_prodtest_plan.py src/workagent/cli_prodtest_run.py src/workagent/cli_prodtest_checks.py tests/test_acp.py
git commit -m "[128] Fix ACP mode detection and flag forwarding across CLI commands"
```

---

### Task 5: Fix Flow Forwarding, Release ACP Support & `web_args.py`

**Files:**
- Modify: `src/workagent/cli_flow_steps.py:94-100, 176-181, 230-235, 261-266, 330-336`
- Modify: `src/workagent/cli_flow_run.py:234-243`
- Modify: `src/workagent/cli_release.py:37-94`
- Modify: `src/workagent/cli_release_fix.py:36-38, 285-343`
- Modify: `src/workagent/web_args.py:86, 177, 364-377`
- Test: `tests/test_cli_flow_util.py`, `tests/test_webapp_data.py`

**Interfaces:**
- Consumes: `cli_harness._run_harness` (Task 3)
- Produces: `release` CLI + `web_args.py` parity for `--acp`, `--model`, `--effort`, `--run-mode acp`; flow steps work with `acp=True` without colliding with `launch=True`; `run_release_step` does not raise `TypeError`.

- [ ] **Step 1: Write failing tests in `tests/test_cli_flow_util.py`**

Append to `tests/test_cli_flow_util.py`:

```python
def test_flow_step_acp_does_not_pass_launch_true(monkeypatch):
    from workagent import cli_flow_steps, cli_start
    seen = {}
    monkeypatch.setattr(cli_start.start, "__wrapped__", lambda *a, **kw: seen.update(kw))
    cli_flow_steps.run_start_step("github:o/r#1", "", {"acp": True, "model": "m1"},
                                  json_output=True, spawn=None, manual=None)
    assert seen["acp"] is True
    assert seen["launch"] is False
    assert seen["model"] == "m1"


def test_release_fix_default_accepts_acp_model_effort():
    from workagent import cli_release_fix, web_args
    assert "acp" in cli_release_fix.RUN_MODES
    assert "--acp" in web_args.BOOL_FLAGS["release"]
    assert "--model" in web_args.VAL_FLAGS["release"]
    assert "--effort" in web_args.VAL_FLAGS["release"]


def test_web_args_rejects_acp_with_short_L_or_conflicting_run_mode():
    import pytest
    from workagent.web_args import ApiError, _validate_args
    with pytest.raises(ApiError) as e1:
        _validate_args("start", ["github:o/r#1", "--acp", "-L"])
    assert e1.value.status == 400
    with pytest.raises(ApiError) as e2:
        _validate_args("start", ["github:o/r#1", "--acp", "--run-mode", "preview"])
    assert e2.value.status == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_flow_util.py -k "flow_step_acp or release_fix_default_accepts or web_args_rejects_acp" -v`
Expected: FAIL.

- [ ] **Step 3: Implement fixes**

1. **`src/workagent/cli_flow_steps.py`**:
- In `run_start_step` (L94), `run_review_step` (L176), `run_action_step` (L231), `run_prodtest_plan_step` (L261): replace `launch=True` with `launch=not bool(config.get("acp"))`.
- In `run_release_step` (L330-335): remove `"acp", "model", "effort"` from `pick(config, ...)` so `cli_release.run_release(...)` is called only with its supported kwargs (`"checks", "diff", "pre_release_timeout", "ci_timeout", "env_file", "no_status"`).

2. **`src/workagent/cli_flow_run.py`** (L234-243):
```python
    _cascade = {"harness": harness, "acp": acp or None,
                "run_mode": run_mode, "model": model, "effort": effort}
    _cascade = {k: v for k, v in _cascade.items()
                if v is not None and v is not False}
    if _cascade:
        for s in merged_steps:
            cfg = dict(s.get("config") or {})
            cfg.update(_cascade)
            s["config"] = _futil.normalize_config(cfg)
```

3. **`src/workagent/cli_release.py`** (L50-94):
Add options to `release(...)`:
```python
    acp: bool = typer.Option(False, "--acp", help="Run fix-default via ACP stdio (only with `release fix-default NAME`)."),
    model: Optional[str] = typer.Option(None, "--model", help="Model id for fix-default harness."),
    effort: Optional[str] = typer.Option(None, "--effort", help="Effort level for fix-default harness."),
```
In `if repo == "fix-default":` forward `acp=acp, model=model, effort=effort` to `_fix.run_fix_default(...)`.
In plain `release REPO` (after line 88), reject `--acp`, `--model`, `--effort`:
```python
    if acp or model or effort:
        _fail("--acp/--model/--effort are only valid with `release fix-default NAME`.", EXIT_USAGE)
```

4. **`src/workagent/cli_release_fix.py`**:
- Line 37: `RUN_MODES = ("preview", "headless", "acp", "terminal")`
- In `run_fix_default(...)`: accept `acp: bool = False, model: Optional[str] = None, effort: Optional[str] = None`; resolve `eff_mode = "acp" if acp else run_mode`; pass `launch=(eff_mode == "headless"), terminal=(eff_mode == "terminal"), no_tty=(eff_mode in ("headless", "acp")), acp=(eff_mode == "acp"), run_mode=eff_mode, model=model, effort=effort` to `_run_harness`.

5. **`src/workagent/web_args.py`**:
- Line 86: `"release": ("--yes", "--dry-run", "--json", "--no-status", "--acp"),`
- Line 177: `"release": ("--checks", "--diff", "--pre-release-timeout", "--ci-timeout", "--env-file", "--action", "--run-mode", "--extra-prompt", "--harness", "--model", "--effort"),`
- Lines 364-376:
```python
    if "--acp" in rest and ("--launch" in rest or "-L" in rest or "--terminal" in rest):
        raise ApiError("bad_arg",
                       "--acp cannot be used with --launch/-L/--terminal "
                       "(it selects the ACP run mode itself)", 400)
    _run_mode = ""
    for _i, _a in enumerate(rest):
        if _a == "--run-mode" and _i + 1 < len(rest):
            _run_mode = rest[_i + 1]
        elif _a.startswith("--run-mode="):
            _run_mode = _a.split("=", 1)[1]
    if _run_mode and _run_mode not in ("preview", "headless", "acp", "terminal"):
        raise ApiError("bad_arg",
                       f"bad --run-mode {_run_mode!r}: use preview, headless, acp, or terminal", 400)
    if "--acp" in rest and _run_mode and _run_mode != "acp":
        raise ApiError("bad_arg",
                       f"--acp conflicts with --run-mode {_run_mode!r}", 400)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_flow_util.py tests/test_webapp_data.py::test_specs_mirror_cli_flags -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_flow_steps.py src/workagent/cli_flow_run.py src/workagent/cli_release.py src/workagent/cli_release_fix.py src/workagent/web_args.py tests/test_cli_flow_util.py
git commit -m "[128] Fix flow ACP forwarding, release ACP flags, and web_args validation"
```

---

### Task 6: Restore Strict `env.sh` Enforcement Across All Execution & Terminal Paths

**Files:**
- Modify: `src/workagent/env.py:267-298`
- Modify: `src/workagent/cli_harness.py:375-382`
- Modify: `src/workagent/cli_flow_util.py:232-243`
- Modify: `src/workagent/web_runs_routes.py:405-410, 786-812`
- Modify: `src/workagent/web_runs.py:833-840`
- Modify: `src/workagent/release_gates.py:54-60, 138-168`
- Modify: `src/workagent/cli_prodtest_gate.py:50-56`
- Modify: `src/workagent/prodtest_scripts.py:14-19`
- Test: `tests/test_env.py`

**Interfaces:**
- Produces: `env.launch_env(None)` and `env.merged_env(None)` auto-create `<config_dir>/env.sh` via `ensure_env_file(None)`; all terminal/subprocess/gate paths source `env.sh`.

- [ ] **Step 1: Write failing tests in `tests/test_env.py`**

Append to `tests/test_env.py` (and update any existing test in `tests/test_env.py` that asserted `launch_env(None)` does NOT create `env.sh`):

```python
def test_merged_env_and_launch_env_auto_create_default(isolated_config):
    from workagent import env, store
    target = store.config_dir() / "env.sh"
    assert not target.exists()
    out = env.merged_env(None)
    assert target.is_file()
    assert out.get("PI_EDIT_VARIANT") == "hashline"


def test_prodtest_scripts_env_uses_merged_env(isolated_config, monkeypatch):
    from workagent import prodtest_scripts, store
    ef = store.config_dir() / "env.sh"
    ef.write_text("export PT_SCRIPT_VAR=from_env_sh\n")
    monkeypatch.delenv("PT_SCRIPT_VAR", raising=False)
    got = prodtest_scripts._env()
    assert got.get("PT_SCRIPT_VAR") == "from_env_sh"


def test_spawn_terminal_step_wraps_with_env_sh(isolated_config, monkeypatch):
    from workagent import cli_flow_util, cli_harness, store
    seen = {}
    monkeypatch.setattr(cli_harness, "spawn_in_terminal", lambda cmd, cwd="": (seen.setdefault("cmd", cmd), 42)[1])
    cli_flow_util.spawn_terminal_step("start", ["github:o/r#1"], cwd="/tmp")
    ef = store.config_dir() / "env.sh"
    assert seen["cmd"].startswith(f"source {ef} && ")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_env.py -v`
Expected: FAIL on the 3 new tests.

- [ ] **Step 3: Implement `env.sh` wiring across all 8 files**

1. **`src/workagent/env.py`** (L269-270 and L293-294):
   Replace `env_file = resolve_env_file(None)` with `env_file = ensure_env_file(None)` in both `launch_env` and `merged_env`.
2. **`src/workagent/cli_harness.py`** (L375-380):
   Replace `os.execvp(shell, [shell])` with `os.execvpe(shell, [shell], _env.merged_env(env_path))`.
3. **`src/workagent/cli_flow_util.py`** (L234-242):
```python
    from . import env as _env
    cmd = " ".join(shlex.quote(a) for a in [kind, *argv] if a)
    full = _env.wrap_command(f"workagent {cmd}", _env.ensure_env_file())
```
4. **`src/workagent/web_runs_routes.py`**:
   - L408 (`_open_terminal_watch`): replace `_env.resolve_env_file(env_file)` with `_env.ensure_env_file(env_file)`.
   - L786-790 (`/api/runs/{id}/start-terminal`): after `if not cmd: raise ...`, add:
```python
        from . import env as _env
        if not cmd.lstrip().startswith("source "):
            cmd = _env.wrap_command(cmd, _env.ensure_env_file())
```
5. **`src/workagent/web_runs.py`** (L836): replace `_env.resolve_env_file(env_file)` with `_env.ensure_env_file(env_file)`.
6. **`src/workagent/release_gates.py`**:
   - L55 (`_run_group`): replace `_env.resolve_env_file(env_file)` with `_env.ensure_env_file(env_file)`.
   - L135-166 (`_ci_sha_status`): compute `from . import env as _env; merged = _env.merged_env()` and pass `env=merged` to the `git`, `glab`, and `gh` `subprocess.run(...)` calls.
7. **`src/workagent/cli_prodtest_gate.py`** (L52-54): pass `env=_env.merged_env()` to `subprocess.run(["bash", str(script), ...], ...)`.
8. **`src/workagent/prodtest_scripts.py`** (L14-18):
```python
def _env() -> dict:
    from . import env as _wa_env
    env = _wa_env.merged_env()
    pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _SRC + (os.pathsep + pp if pp else "")
    return env
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_env.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/env.py src/workagent/cli_harness.py src/workagent/cli_flow_util.py src/workagent/web_runs_routes.py src/workagent/web_runs.py src/workagent/release_gates.py src/workagent/cli_prodtest_gate.py src/workagent/prodtest_scripts.py tests/test_env.py
git commit -m "[128] Restore strict env.sh enforcement across all execution and terminal paths"
```

---

### Task 7: Web API Fixes (`?refresh=1`, `/input` 409 `pending_question`/`qid`, `pending_question` Clearing, Sync `rehydrate()`)

**Files:**
- Modify: `src/workagent/web_runs_routes.py:452-488, 927-974`
- Modify: `src/workagent/web_runs.py:456-464, 565-575, 730-744`
- Modify: `web/API_CONTRACT.md`
- Test: `tests/test_webapp_acp.py`

**Interfaces:**
- Produces:
  - `GET /api/harnesses/{name}/options?refresh=0|1` bypasses `_OPTIONS_CACHE` when `refresh=1`
  - `POST /api/runs/{run_id}/input` returns `409 no_pending_question` when no question pending, `409 qid_mismatch` when `qid` missing/mismatched, accepts `text` (canonical) or `answer`/`choice` aliases
  - `_summary` / `_persisted_summary` return `pending_question: None` when `state != "running"`
  - `rehydrate()` backfills `output.log` synchronously before starting tailer

- [ ] **Step 1: Write failing tests in `tests/test_webapp_acp.py`**

Append to `tests/test_webapp_acp.py`:

```python
def test_harness_options_refresh_bypasses_cache(isolated_config, monkeypatch):
    from fastapi.testclient import TestClient
    from workagent import backend, webapp, web_runs_routes
    from workagent.acp_harness import HarnessOptions

    web_runs_routes._OPTIONS_CACHE.clear()
    calls = {"n": 0}
    h = backend.get_harness("omp")

    def fake_discover(env_file=None):
        calls["n"] += 1
        return HarnessOptions(models=[{"id": f"m{calls['n']}", "name": f"M{calls['n']}"}],
                              efforts=[], current_model=f"m{calls['n']}", current_effort="")

    monkeypatch.setattr(h, "discover_options", fake_discover)
    client = TestClient(webapp.create_app())
    r1 = client.get("/api/harnesses/omp/options")
    assert r1.status_code == 200 and r1.json()["cached"] is False and r1.json()["models"][0]["id"] == "m1"
    r2 = client.get("/api/harnesses/omp/options")
    assert r2.status_code == 200 and r2.json()["cached"] is True and r2.json()["models"][0]["id"] == "m1"
    r3 = client.get("/api/harnesses/omp/options?refresh=1")
    assert r3.status_code == 200 and r3.json()["cached"] is False and r3.json()["models"][0]["id"] == "m2"


def test_run_input_enforces_pending_question_and_qid(isolated_config, tmp_path):
    from fastapi.testclient import TestClient
    from workagent import webapp, web_runs

    app = webapp.create_app()
    reg = app.state.registry
    run = web_runs.Run(id="r128", command="start", args=["github:o/r#1"], target="start")
    run.run_dir = str(tmp_path / "r128")
    reg.runs[run.id] = run
    client = TestClient(app)
    hdrs = {"Origin": "http://127.0.0.1:8765", "Content-Type": "application/json"}

    # No pending question -> 409
    r_none = client.post("/api/runs/r128/input", json={"text": "yes", "qid": "q1"}, headers=hdrs)
    assert r_none.status_code == 409
    assert r_none.json()["code"] == "no_pending_question"

    # Emit a pending question
    run.append('ACP:{"t":"question","qid":"q1","text":"Continue?","options":["yes","no"]}\n')

    # Missing or wrong qid -> 409
    r_bad = client.post("/api/runs/r128/input", json={"text": "yes", "qid": "wrong"}, headers=hdrs)
    assert r_bad.status_code == 409
    assert r_bad.json()["code"] == "qid_mismatch"

    # Matching qid with `answer` alias -> 200
    r_ok = client.post("/api/runs/r128/input", json={"answer": "yes", "qid": "q1"}, headers=hdrs)
    assert r_ok.status_code == 200

    # Once run exits, pending_question is cleared in summary
    run.state = "cancelled"
    assert web_runs._summary(run)["pending_question"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_webapp_acp.py -k "refresh_bypasses_cache or enforces_pending_question" -v`
Expected: FAIL.

- [ ] **Step 3: Implement fixes in `web_runs_routes.py`, `web_runs.py`, and `web/API_CONTRACT.md`**

1. **`src/workagent/web_runs_routes.py`** (`_cached_harness_options` L452 and `harness_options` L927):
```python
def _cached_harness_options(name: str, refresh: bool = False) -> tuple[dict, bool, str | None]:
    ...
    now = time.monotonic()
    hit = None if refresh else _OPTIONS_CACHE.get(name)
    if hit is not None and now - hit[0] < _OPTIONS_TTL_S:
        return (dict(hit[1]), True, None)
```
```python
    @app.get("/api/harnesses/{name}/options")
    def harness_options(name: str, refresh: int = 0) -> dict:
        body, cached, _warning = _cached_harness_options(name, refresh=bool(refresh))
        return {**body, "cached": cached}
```

2. **`src/workagent/web_runs_routes.py`** — add `_pending_question_from_texts` to the `from .web_runs import (...)` block at lines 28-35, and update `run_input` (L937-974):
```python
    @app.post("/api/runs/{run_id}/input")
    async def run_input(run_id: str, body: dict) -> dict:
        raw_body = body or {}
        text = str(raw_body.get("text") or raw_body.get("answer") or raw_body.get("choice") or "")
        if not text.strip():
            raise ApiError("bad_arg", "input text is required", 422)
        qid = str(raw_body.get("qid") or "").strip()
        try:
            run = registry.get(run_id)
        except ApiError:
            row = _persisted_row(run_id)
            if row is None:
                raise
            raise ApiError("not_running", f"run {run_id} is not running", 409)
        with run.lock:
            if run.state != "running":
                raise ApiError("not_running", f"run {run_id} is not running", 409)
            pending = _pending_question_from_texts([txt for _, txt in run.lines])
            if pending is None:
                raise ApiError("no_pending_question",
                               f"run {run_id} has no pending question", 409)
            pqid = str(pending.get("qid") or "")
            if pqid and qid != pqid:
                raise ApiError("qid_mismatch",
                               f"qid {qid!r} does not match pending question {pqid!r}", 409)
            run_dir = run.run_dir or ""
        if not run_dir:
            from . import run_store as _rs
            run_dir = str(_rs.runs_root() / run.id)
        try:
            Path(run_dir).mkdir(parents=True, exist_ok=True)
            payload = {"text": text, "answer": text}
            if qid or pqid:
                payload["qid"] = qid or pqid
            with open(Path(run_dir) / "input.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
        except OSError as exc:
            raise ApiError("io_error", f"cannot write input for run {run_id}: {exc}", 500)
        return {"id": run_id, "ok": True}
```

3. **`src/workagent/web_runs.py`**:
   - Line 462 (`_summary`):
     `out["pending_question"] = _pending_question_from_texts(src) if run.state == "running" else None`
   - Line 572 (`_persisted_summary`):
     `"pending_question": (_pending_question_from_texts([ln["text"] for ln in lines_list]) if state == "running" else None),`
   - Add `initial_offset: int | None = None` to `_start_tailer(run, registry, backfill: bool = False, initial_offset: int | None = None)` (using `offset = initial_offset` when `not backfill and initial_offset is not None`).
   - In `rehydrate(registry)` (L740): synchronously read existing `(d / "output.log").read_bytes()`, append decoded lines into `run`, and pass `initial_offset=len(raw)` to `_start_tailer(run, registry, backfill=False, initial_offset=len(raw))` so no bytes written between backfill and tailer startup are dropped or duplicated.

4. **`web/API_CONTRACT.md`**: Document `?refresh=0|1` on `GET /api/harnesses/{name}/options`, and document `409 no_pending_question` / `409 qid_mismatch` + `answer`/`choice` aliases on `POST /api/runs/{id}/input`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_webapp_acp.py tests/test_webapp_acp_fixes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_runs_routes.py src/workagent/web_runs.py web/API_CONTRACT.md tests/test_webapp_acp.py
git commit -m "[128] Fix options ?refresh=1, /input pending-question/qid 409, and sync rehydrate"
```

---

### Task 8: End-to-End Supervised Web ACP Question-Answering Integration Test

**Files:**
- Test: `tests/test_webapp_acp.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 7 (`run_supervised`, `AcpHarness.launch_acp`, `POST /api/runs/{id}/input`, `GET /api/runs/{id}`)

- [ ] **Step 1: Write end-to-end supervised web ACP test in `tests/test_webapp_acp.py`**

Append to `tests/test_webapp_acp.py`:

```python
def test_e2e_supervised_web_acp_question_answered_via_api(isolated_config, tmp_path):
    """End-to-end: supervised child receives WORKAGENT_RUN_DIR, emits question,
    GET /api/runs/{id} exposes pending_question, POST /input delivers answer,
    child completes turn 2 and pending_question clears."""
    import sys
    import time
    from fastapi.testclient import TestClient
    from workagent import webapp, web_runs

    fake_agent = tmp_path / "e2e_agent.py"
    fake_agent.write_text(
        "import json, sys\n"
        "turns = 0\n"
        "for raw in sys.stdin:\n"
        "    msg = json.loads(raw)\n"
        "    m = msg.get('method')\n"
        "    if m == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':1}}), flush=True)\n"
        "    elif m == 'session/new':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'sessionId':'s1'}}), flush=True)\n"
        "    elif m == 'session/prompt':\n"
        "        turns += 1\n"
        "        if turns == 1:\n"
        "            print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'s1','update':{'sessionUpdate':'tool_call','toolCallId':'q-e2e','title':'ask_question','rawInput':{'questions':[{'question':'Ship it?','options':[{'label':'Yes'},{'label':'No'}]}]}}}}), flush=True)\n"
        "            print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'stopReason':'end_turn'}}), flush=True)\n"
        "        else:\n"
        "            txt = msg['params']['prompt'][0]['text']\n"
        "            print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'s1','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':f'Got {txt}\\nJOB_DONE'}}}}), flush=True)\n"
        "            print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{'stopReason':'end_turn'}}), flush=True)\n"
    )
    child_script = tmp_path / "run_child.py"
    child_script.write_text(
        "import sys\n"
        "from workagent.agy_harness import AgyAcpHarness\n"
        "h = AgyAcpHarness()\n"
        f"h.acp_argv = lambda: [sys.executable, {str(fake_agent)!r}]\n"
        f"raise SystemExit(h.launch_acp('hello', {str(tmp_path)!r}, no_tty=True))\n"
    )

    app = webapp.create_app()
    reg = app.state.registry
    run = web_runs.Run(id="e2e-q", command="start", args=["github:o/r#1", "--acp"],
                       target="start", argv=[sys.executable, str(child_script)])
    reg.runs[run.id] = run
    web_runs._spawn(run, reg)
    client = TestClient(app)

    deadline = time.time() + 10.0
    pq = None
    while time.time() < deadline:
        detail = client.get("/api/runs/e2e-q").json()
        pq = detail.get("pending_question")
        if pq:
            break
        time.sleep(0.1)
    assert pq is not None and pq["qid"] == "q-e2e"

    hdrs = {"Origin": "http://127.0.0.1:8765", "Content-Type": "application/json"}
    r = client.post("/api/runs/e2e-q/input", json={"qid": "q-e2e", "text": "Yes"}, headers=hdrs)
    assert r.status_code == 200

    deadline = time.time() + 10.0
    while time.time() < deadline:
        detail = client.get("/api/runs/e2e-q").json()
        if detail["state"] != "running":
            break
        time.sleep(0.1)
    assert detail["state"] == "succeeded"
    assert detail["pending_question"] is None
    assert any("Got Yes" in ln["text"] for ln in detail["lines"])
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/test_webapp_acp.py::test_e2e_supervised_web_acp_question_answered_via_api -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_webapp_acp.py
git commit -m "[128] Add end-to-end supervised web ACP question-answering integration test"
```

---

### Task 9: Frontend `parseAcpBlocks` Coalescing, `toolCallId` Merging, `usage` Formatting & `{label}` Options

**Files:**
- Modify: `web/src/lib/harness.ts:20-95`
- Test: `web/src/lib/acpTranscript.test.ts`

**Interfaces:**
- Produces:
  - `parseAcpBlocks(lines)` coalesces adjacent `message` / `thought` chunks, merges `tool_call` by `id`, formats `usage`, normalizes `{label}` options, and skips malformed line 0
  - `hasAcpStructuredEvents(lines) -> boolean`

- [ ] **Step 1: Write failing tests in `web/src/lib/acpTranscript.test.ts`**

Add to `web/src/lib/acpTranscript.test.ts`:

```ts
import { describe, expect, it } from "vitest"
import { parseAcpBlocks } from "./harness"

describe("parseAcpBlocks coalescing and normalization", () => {
  it("coalesces consecutive message and thought chunks and skips malformed line 0", () => {
    const blocks = parseAcpBlocks([
      { seq: 0, text: 'ACP:{"t":"message","text":"trun' },
      { seq: 1, text: 'ACP:{"t":"thought","text":"step 1 "}' },
      { seq: 2, text: 'ACP:{"t":"thought","text":"step 2"}' },
      { seq: 3, text: 'ACP:{"t":"message","text":"Hello "}' },
      { seq: 4, text: 'ACP:{"t":"message","text":"world"}' },
    ])
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toMatchObject({ kind: "thought", text: "step 1 step 2" })
    expect(blocks[1]).toMatchObject({ kind: "message", text: "Hello world" })
  })

  it("merges tool_call updates by id, formats usage, and normalizes {label} options", () => {
    const blocks = parseAcpBlocks([
      { seq: 1, text: 'ACP:{"t":"tool_call","id":"tc1","name":"read","kind":"read","status":"running","args":{"path":"a.py"},"output":""}' },
      { seq: 2, text: 'ACP:{"t":"tool_call","id":"tc1","name":"read","kind":"read","status":"completed","output":"file body"}' },
      { seq: 3, text: 'ACP:{"t":"usage","usage":{"inputTokens":120,"outputTokens":45}}' },
      { seq: 4, text: 'ACP:{"t":"question","qid":"q1","text":"Pick one","options":[{"label":"Yes"},{"label":"No"}]}' },
    ])
    expect(blocks).toHaveLength(3)
    expect(blocks[0]).toMatchObject({ kind: "tool_call", id: "tc1", status: "completed", output: "file body" })
    expect(blocks[1].kind).toBe("usage")
    expect(blocks[1].text).not.toContain('{"t":')
    expect(blocks[1].text).toContain("120")
    expect(blocks[2]).toMatchObject({ kind: "question", qid: "q1", options: ["Yes", "No"] })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npm test -- src/lib/acpTranscript.test.ts`
Expected: FAIL.

- [ ] **Step 3: Implement `parseAcpBlocks` upgrades in `web/src/lib/harness.ts`**

Update `AcpBlock` and `parseAcpBlocks` in `web/src/lib/harness.ts`:

```ts
export interface AcpBlock {
  kind: AcpBlockKind
  text: string
  id?: string
  name?: string
  toolKind?: string
  status?: string
  args?: unknown
  output?: string
  qid?: string
  options?: string[]
  multi?: boolean
}

function formatUsage(obj: Record<string, unknown>): string {
  if (typeof obj.text === "string" && obj.text.trim()) return obj.text.trim()
  const u = (typeof obj.usage === "object" && obj.usage !== null
    ? obj.usage
    : obj) as Record<string, unknown>
  const parts: string[] = []
  const pick = (keys: string[], label: string) => {
    for (const k of keys) {
      if (typeof u[k] === "number") {
        parts.push(`${label} ${u[k]}`)
        return
      }
    }
  }
  pick(["inputTokens", "input_tokens", "promptTokens", "input"], "in")
  pick(["outputTokens", "output_tokens", "completionTokens", "output"], "out")
  pick(["totalTokens", "total_tokens", "total"], "total")
  if (parts.length > 0) return `tokens: ${parts.join(" · ")}`
  const fallback = Object.entries(u)
    .filter(([k]) => k !== "t" && k !== "id")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
  return fallback.length > 0 ? fallback.join(" · ") : "usage"
}

function normalizeOptions(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined
  return raw.map((o) =>
    typeof o === "object" && o !== null && "label" in o
      ? String((o as Record<string, unknown>).label ?? "")
      : String(o ?? ""),
  )
}

export function hasAcpStructuredEvents(lines: { seq: number; text: string }[]): boolean {
  return parseAcpBlocks(lines).some((b) => b.kind !== "raw")
}
```

In `parseAcpBlocks`:
- Maintain `toolIndex = new Map<string, number>()` mapping `toolCallId` → index in `out`.
- For `"message"` and `"thought"`: if `out.length > 0 && out[out.length - 1].kind === t`, append `text` to `out[out.length - 1].text`; otherwise push a new block.
- For `"tool_call"`: read `id = typeof obj.id === "string" ? obj.id : ""`; format `output` (string or `JSON.stringify` for non-empty arrays/objects); if `id && toolIndex.has(id)`, update that existing block in place (`name`, `toolKind`, `status`, `args` if non-empty, `output` if non-empty); else push new block and record `toolIndex.set(id, out.length - 1)`.
- For `"usage"`: push `{ kind: "usage", text: formatUsage(obj) }`.
- For `"question"`: push `{ kind: "question", text: String(obj.text ?? ""), qid: typeof obj.qid === "string" ? obj.qid : undefined, options: normalizeOptions(obj.options), multi: obj.multi === true }`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npm test -- src/lib/acpTranscript.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/harness.ts web/src/lib/acpTranscript.test.ts
git commit -m "[128] Coalesce ACP chunks, merge tool calls, format usage, and normalize options"
```

---

### Task 10: Frontend `AcpTranscript.tsx` (Collapsible `<details>`, Markdown, `rAF` Autoscroll) & `RunDetail.tsx` (`Structured / Raw` Toggle)

**Files:**
- Modify: `web/package.json` (add `react-markdown`, `remark-gfm`)
- Modify: `web/src/components/AcpTranscript.tsx`
- Modify: `web/src/pages/RunDetail.tsx:365-378`
- Create: `web/src/components/AcpTranscript.test.tsx`

**Interfaces:**
- Consumes: `parseAcpBlocks`, `hasAcpStructuredEvents` (Task 9)
- Produces: `AcpTranscript` with collapsible `<details>` (Thinking collapsed; tool calls open when running/pending/failed, collapsed when completed), `<ReactMarkdown remarkPlugins={[remarkGfm]}>` for messages, `rAF` sticky-bottom autoscroll; `RunDetail` renders `Structured / Raw` toggle instead of stacking both views.

- [ ] **Step 1: Install `react-markdown` and `remark-gfm`**

Run: `cd web && npm install react-markdown remark-gfm`

- [ ] **Step 2: Write failing tests in `web/src/components/AcpTranscript.test.tsx`**

Create `web/src/components/AcpTranscript.test.tsx`:

```tsx
import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { AcpTranscript } from "./AcpTranscript"

describe("AcpTranscript", () => {
  it("renders collapsed Thinking <details>, status-driven tool <details>, and Markdown messages", () => {
    const lines = [
      { seq: 1, text: 'ACP:{"t":"thought","text":"pondering"}' },
      { seq: 2, text: 'ACP:{"t":"tool_call","id":"t1","name":"read","kind":"read","status":"completed","output":"ok"}' },
      { seq: 3, text: 'ACP:{"t":"tool_call","id":"t2","name":"bash","kind":"execute","status":"failed","output":"boom"}' },
      { seq: 4, text: 'ACP:{"t":"message","text":"# Heading\\n\\n**bold** text"}' },
    ]
    const { container } = render(<AcpTranscript lines={lines} />)
    const details = Array.from(container.querySelectorAll("details"))
    expect(details).toHaveLength(3)
    // Thinking: collapsed
    expect(details[0].open).toBe(false)
    expect(details[0].textContent).toContain("Thinking")
    // Completed tool: collapsed
    expect(details[1].open).toBe(false)
    // Failed tool: expanded
    expect(details[2].open).toBe(true)
    // Markdown heading & bold rendered
    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument()
    expect(container.querySelector("strong")?.textContent).toBe("bold")
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd web && npm test -- src/components/AcpTranscript.test.tsx`
Expected: FAIL (no `<details>`, no heading/strong).

- [ ] **Step 4: Implement `web/src/components/AcpTranscript.tsx` and `Structured / Raw` toggle in `web/src/pages/RunDetail.tsx`**

In `web/src/components/AcpTranscript.tsx`, replace the entire file with:

```tsx
import { useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { parseAcpBlocks, type AcpBlock } from "@/lib/harness"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import type { RunLine } from "@/lib/api"

const COLLAPSED_TOOL_STATUSES = new Set(["completed", "done", "success", "ok"])

function isToolExpanded(status?: string): boolean {
  const s = (status ?? "").trim().toLowerCase()
  return !COLLAPSED_TOOL_STATUSES.has(s)
}

function toolIcon(kind?: string): string {
  switch ((kind ?? "").trim().toLowerCase()) {
    case "read":
      return "📖"
    case "edit":
    case "write":
      return "✏️"
    case "execute":
    case "bash":
      return "⚡"
    case "search":
      return "🔍"
    default:
      return "⚙"
  }
}

function formatArgs(args: unknown): string {
  if (args == null) return ""
  if (typeof args === "string") return args
  if (typeof args === "object" && Object.keys(args as Record<string, unknown>).length === 0) return ""
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

function BlockView({ block }: { block: AcpBlock }) {
  switch (block.kind) {
    case "thought":
      return (
        <details className="rounded border border-white/10 bg-white/[0.03] px-2.5 py-1.5">
          <summary className="cursor-pointer select-none text-xs text-white/50">Thinking</summary>
          <div className="mt-1.5 whitespace-pre-wrap text-xs italic text-white/55">{block.text}</div>
        </details>
      )
    case "tool_call": {
      const argsText = formatArgs(block.args)
      const outText = block.output || block.text || ""
      return (
        <details
          open={isToolExpanded(block.status)}
          className="rounded border border-white/10 bg-white/5 px-2.5 py-1.5"
        >
          <summary className="flex cursor-pointer select-none items-center gap-2 text-xs text-sky-300">
            <span>
              {toolIcon(block.toolKind)} {block.name || block.toolKind || "tool"}
            </span>
            {block.status ? <Badge>{block.status}</Badge> : null}
          </summary>
          <div className="mt-1.5 space-y-1.5 font-mono text-xs text-white/80">
            {argsText ? (
              <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[11px] text-white/70">
                {argsText}
              </pre>
            ) : null}
            {outText ? <div className="whitespace-pre-wrap break-words">{outText}</div> : null}
          </div>
        </details>
      )
    }
    case "usage":
      return <div className="text-xs text-white/40">{block.text}</div>
    case "question":
      return (
        <div className="rounded border border-sky-500/40 bg-sky-500/10 px-2.5 py-1.5" data-testid="acp-question">
          <span className="text-sky-300">❓ {block.text}</span>
        </div>
      )
    case "turn_end":
      return <div className="text-xs text-white/30">— turn end —</div>
    case "message":
      return (
        <div className="prose prose-sm prose-invert max-w-none break-words font-sans text-sm leading-relaxed">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
        </div>
      )
    default:
      return <div className="whitespace-pre-wrap break-words">{block.text || "\u00a0"}</div>
  }
}

/** Structured ACP transcript (issues #112, #128): collapsible blocks, Markdown, rAF autoscroll. */
export function AcpTranscript({ lines }: { lines: RunLine[] }) {
  const blocks = useMemo(() => parseAcpBlocks(lines), [lines])
  const structured = blocks.filter((b) => b.kind !== "raw").length
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const programmaticRef = useRef(false)
  const [follow, setFollow] = useState(true)

  useEffect(() => {
    if (!follow) return
    const el = scrollRef.current
    if (!el) return
    const rafId = window.requestAnimationFrame(() => {
      programmaticRef.current = true
      el.scrollTop = el.scrollHeight
      window.requestAnimationFrame(() => {
        programmaticRef.current = false
      })
    })
    return () => window.cancelAnimationFrame(rafId)
  }, [blocks, follow])

  function handleScroll() {
    if (programmaticRef.current) return
    const el = scrollRef.current
    if (!el) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    if (distance > 48) setFollow(false)
    else if (distance <= 16 && !follow) setFollow(true)
  }

  if (structured === 0) return null
  return (
    <div className="mb-4 rounded-lg border" data-testid="acp-transcript">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2 text-xs text-white/70">
        <span>
          ACP transcript — {structured} structured block{structured === 1 ? "" : "s"} ({blocks.length} total)
        </span>
        <label className="flex items-center gap-2 text-white/80">
          <Checkbox
            checked={follow}
            onCheckedChange={(v) => setFollow(v === true)}
            aria-label="Follow ACP transcript"
          />
          Follow output
        </label>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex max-h-[60vh] flex-col gap-2 overflow-auto px-3 py-2.5 font-mono text-[12.5px] leading-relaxed"
      >
        {blocks.map((b, i) => (
          <BlockView key={b.id ? `${b.kind}-${b.id}` : i} block={b} />
        ))}
      </div>
    </div>
  )
}
```

In `web/src/pages/RunDetail.tsx`:
- Add `const hasAcp = useMemo(() => hasAcpStructuredEvents(lines), [lines])` and `const [viewMode, setViewMode] = useState<"structured" | "raw">("structured")`.
- Replace lines 370-372 (`<AcpTranscript lines={lines} />` + `<LogViewer ... />`) with:
```tsx
      {hasAcp ? (
        <div className="mb-2 flex items-center gap-1" role="tablist" aria-label="Log view mode">
          <Button
            size="sm"
            variant={viewMode === "structured" ? "default" : "outline"}
            role="tab"
            aria-selected={viewMode === "structured"}
            onClick={() => setViewMode("structured")}
          >
            Structured
          </Button>
          <Button
            size="sm"
            variant={viewMode === "raw" ? "default" : "outline"}
            role="tab"
            aria-selected={viewMode === "raw"}
            onClick={() => setViewMode("raw")}
          >
            Raw
          </Button>
        </div>
      ) : null}

      {hasAcp && viewMode === "structured" ? (
        <AcpTranscript lines={lines} />
      ) : (
        <LogViewer lines={lines} connected={sse.connected} terminal={terminal} />
      )}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd web && npm test -- src/components/AcpTranscript.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/package.json web/package-lock.json web/src/components/AcpTranscript.tsx web/src/components/AcpTranscript.test.tsx web/src/pages/RunDetail.tsx
git commit -m "[128] Upgrade AcpTranscript with details, Markdown, autoscroll, and Structured/Raw toggle"
```

---

### Task 11: Frontend UI Drift Fixes, Changelog & Full Verification

**Files:**
- Modify: `web/src/components/CandidatesCard.tsx:472-477`
- Modify: `web/src/components/ChainEditor.tsx:332-343`
- Modify: `web/src/components/MessageRowActions.tsx:41-55, 84-88`
- Modify: `web/src/lib/messageRunConfig.ts:150-165`
- Modify: `web/src/pages/Dashboard.tsx:137-143`
- Modify: `web/src/pages/Launch.tsx:77-98`
- Modify: `web/src/components/StartForm.tsx:450-462`
- Modify: `CHANGELOG.md`
- Test: `web/src/components/AcpSelectors.test.tsx`, `web/src/pages/Launch.test.tsx`

- [ ] **Step 1: Apply the 5 UI drift fixes**

1. **`web/src/components/CandidatesCard.tsx`** (L472-477):
```tsx
      warning: form.runMode === "headless"
        ? "The agent runs headless with auto-approve (--no-tty): it can commit, push and open MRs/PRs without further prompts. The server appends --yes."
        : form.runMode === "acp"
          ? "The agent runs via ACP stdio (--acp) with structured streaming and inline question answering. The server appends --yes."
          : form.runMode === "terminal"
            ? "The agent runs in an OS terminal opened on your desktop (workagent --terminal). The server appends --yes."
            : "Preview only: prints the TTY harness command (no -p flag) without running the agent. The server appends --yes.",
```

2. **`web/src/components/ChainEditor.tsx`** (L332-343):
   Import `ModelSelect, EffortSelect` from `@/components/ModelEffortSelect`, and below `<HarnessSelect ... />` render:
```tsx
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <ModelSelect
              idPrefix={idPrefix}
              harness={harness || hintHarness}
              value={String(config.model ?? "")}
              onChange={(v) => patch("model", v)}
            />
            <EffortSelect
              idPrefix={idPrefix}
              harness={harness || hintHarness}
              value={String(config.effort ?? "")}
              onChange={(v) => patch("effort", v)}
            />
          </div>
```
   And clear `model` and `effort` when `HarnessSelect`'s `onChange` fires.

3. **`web/src/components/MessageRowActions.tsx` & `web/src/lib/messageRunConfig.ts`**:
   - In `web/src/lib/messageRunConfig.ts` (`buildImmediateMessageActionArgs` L155): extend signature to `buildImmediateMessageActionArgs(label: string, messageId: string, opts: { runMode?: RunMode; harness?: string; model?: string; effort?: string } = {})` (emitting `...messageRunModeArgs(opts.runMode ?? "headless")` and `...messageScopeArgs({ harness: opts.harness ?? "", model: opts.model ?? "", effort: opts.effort ?? "" })`).
   - In `web/src/components/MessageRowActions.tsx`:
     - Line 47 (`handleRunClick` immediate action): look up `const act = actions.find((a) => a.label === label)` and call `buildImmediateMessageActionArgs(label, row.message_id, { runMode: actionRunModeOf(act), harness: actionHarnessOf(act), model: actionModelOf(act), effort: actionEffortOf(act) })`.
     - Line 85 (`Validate` button): pass `...messageScopeArgs({ harness, model, effort }), ...messageRunModeArgs(runMode)` before `"--yes"`.

4. **`web/src/pages/Dashboard.tsx`** (L137-143):
   Add `setModel("")` and `setEffort("")` inside `HarnessSelect`'s `onChange` alongside `modelRef.current = ""` and `effortRef.current = ""`.

5. **`web/src/pages/Launch.tsx`** (L77-98) & **`web/src/components/StartForm.tsx`** (L455-456):
   Include `model: p.<slice>.model, effort: p.<slice>.effort` in `prefsForMode` and `useStartForm` hydration.

- [ ] **Step 2: Add unit tests covering the 5 UI drift fixes in `web/src/components/AcpSelectors.test.tsx`**

Append to `web/src/components/AcpSelectors.test.tsx`:

```tsx
import { loadLaunchPrefs, saveLaunchPrefs } from "@/lib/launchPrefs"
import { buildImmediateMessageActionArgs } from "@/lib/messageRunConfig"

describe("UI drift fixes (#128)", () => {
  it("persists and loads model/effort in launch prefs", () => {
    const prefs = loadLaunchPrefs()
    saveLaunchPrefs({
      ...prefs,
      start: { ...prefs.start, model: "gemini-pro", effort: "high" },
    })
    const reloaded = loadLaunchPrefs()
    expect(reloaded.start.model).toBe("gemini-pro")
    expect(reloaded.start.effort).toBe("high")
  })

  it("buildImmediateMessageActionArgs forwards runMode, harness, model, and effort", () => {
    const args = buildImmediateMessageActionArgs("triage", "m-1", {
      runMode: "acp",
      harness: "omp",
      model: "m1",
      effort: "high",
    })
    expect(args).toContain("--acp")
    expect(args).toContain("--harness")
    expect(args).toContain("omp")
    expect(args).toContain("--model")
    expect(args).toContain("m1")
    expect(args).toContain("--effort")
    expect(args).toContain("high")
  })
})
```

- [ ] **Step 3: Append `CHANGELOG.md` entry (append-only)**

Append one line under `## Unreleased` in `CHANGELOG.md` (verify `git diff CHANGELOG.md` has only `+` lines and zero `-` lines):
```markdown
- Fix ACP harness runtime bugs (supervised Q&A, stderr tail, validation, env.sh, options refresh, transcript UI) [128]
```

- [ ] **Step 4: Run full verification suite & reinstall**

Run:
```bash
uv run pytest -v && (cd web && npm test && npm run typecheck && npm run build) && ./install.sh && workagent doctor
```
Expected: All backend + frontend tests green, TypeScript clean, `web/dist` built, `workagent doctor` reports `web_dist: ok`.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/CandidatesCard.tsx web/src/components/ChainEditor.tsx web/src/components/MessageRowActions.tsx web/src/pages/Dashboard.tsx web/src/pages/Launch.tsx web/src/components/StartForm.tsx web/src/components/AcpSelectors.test.tsx CHANGELOG.md
git commit -m "[128] Fix remaining ACP UI drifts and update changelog"
```
