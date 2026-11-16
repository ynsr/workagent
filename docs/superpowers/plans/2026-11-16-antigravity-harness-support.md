# Add Antigravity AI Harness Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Antigravity AI (`agy`) harness support via `AgyHarness` (strictly invoking `agy-guard`), convert `Harness` into a strict `abc.ABC` with zero `omp` calls or defaults outside `OmpHarness`, switch `DEFAULT_HARNESS` to `"agy"`, add `workagent config` + `GET /api/config`, and return the Harness selector to every Web UI action, launch, flow, dashboard, worktree, release, and settings surface.

**Architecture:** `src/workagent/backend.py` defines the `Harness(abc.ABC)` contract, shared process/timeout helpers, `OmpHarness`, and `HARNESSES = {"agy": AgyHarness(), "omp": OmpHarness()}`. `src/workagent/agy_harness.py` implements `AgyHarness`: `-p=`/`-i=` argv formatting, `stream-json` NDJSON streaming, `conversation_summaries.db` + `brain/` discovery, normalized `.jsonl` mirroring under `~/.config/workagent/runs/agy/…`, and `--conversation <id>` resume. All CLI, run-registry, and React UI callers route through `Harness` methods and `GET /api/config`.

**Tech Stack:** Python 3.12+ (Typer, FastAPI, stdlib `abc`/`sqlite3`/`subprocess`), React + TypeScript + TanStack Query + Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-16-antigravity-harness-support-design.md`

## Global Constraints

- Always use `agy-guard` instead of `agy` everywhere (argv, binary lookup, resume shell commands, UI warning banners, docs).
- Zero direct `omp` CLI/subprocess argv strings (`"omp"`, `--session-dir`, `--auto-approve`) or hardcoded `"omp"` fallback defaults outside `OmpHarness` (only `backend.HARNESSES` and `store.SUPPORTED_HARNESSES` list `"omp"` as a supported identifier).
- `DEFAULT_HARNESS = "agy"`; `SUPPORTED_HARNESSES = ("agy", "omp")`.
- stdout carries ONLY data; every log/progress/confirmation line goes to stderr.
- Parity checklist: CLI flag → `web_args.py` (`SPECS`/`BOOL_FLAGS`/`VAL_FLAGS`) → `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` → frontend control → `tests/test_cli_*.py` + `tests/test_webapp_data.py::test_specs_mirror_cli_flags` + `tests/test_webapp_runs.py`.
- Keep modules focused; run `uv run pytest -v` and `cd web && npm test -- --run && npm run build` after changes, and reinstall locally with `./install.sh` + `workagent doctor` at completion.

## Review Focus

- **`agy` `-p` / `-i` token consumption:** Passing `-p --output-format ...` causes `agy` to consume `--output-format` as the prompt; `AgyHarness.command_argv` MUST emit flags first and attach prompts via `f"-p={prompt}"` / `f"-i={prompt}"`, and omit `-i` when `prompt == ""` (pinned in Task 2).
- **Concurrent multi-worktree `agy` interactive / `start-terminal` sessions:** Concurrent `agy-guard` runs create distinct UUIDs in `brain/`; `watch_transcript` / `adopt_from_workspace` MUST query `conversation_summaries.db` read-only (`?mode=ro`) matching `workspace_uris` to `"file://<resolved_workdir>"` and `last_modified_time >= launched_at` with `brain/` fallback (pinned in Task 2 & Task 4).
- **`agy` validation retry & `review --fix-comments --use-review-session` continuity:** Retrying or continuing against an existing `runs/agy/…jsonl` MUST read `conversation_id` from `agy_meta` (or DB metadata) and emit `["--conversation", cid]` via `session_file_flag` (pinned in Task 2 & Task 3).
- **`sync --harness` (`bool` → `Optional[str]`) & flow override precedence:** `sync --harness <name>` auto-launches that harness on conflict without requiring `--yes`, `--yes` without `--harness` uses `default_harness()`, and `flow run --harness <x>` overrides per-step `config.harness` (`cli_harness or step_cfg.get("harness") or default_harness()`) (pinned in Task 4).
- **Web UI `@/lib/queries` mocked tests + `test_specs_mirror_cli_flags`:** Adding `useConfig()` inside `<HarnessSelect>` must also update Vitest mocks of `@/lib/queries` (e.g., `FlowWizard.test.tsx`, `ChainEditor.test.tsx`, `Runs.test.tsx`, `FlowTemplates.test.tsx`), and CLI flag edits must pass `test_specs_mirror_cli_flags` (pinned in Task 5, Task 6, Task 7).

---

### Task 1: Refactor `Harness` ABC, Isolate `OmpHarness`, Switch `DEFAULT_HARNESS` to `"agy"`, Update `doctor.py`

**Files:**
- Modify: `src/workagent/backend.py:23-187`
- Modify: `src/workagent/store.py:1-20,76-85`
- Modify: `src/workagent/doctor.py:63-75`
- Test: `tests/test_backend.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `store.load_config`, `env.merged_env`, `env.launch_env`, `errors.HarnessError`, `errors.HarnessTimeout`.
- Produces:
  - `Harness(abc.ABC)` with abstract methods: `command_argv(self, prompt: str, no_tty: bool, extra_args: list[str] | None = None) -> list[str]`, `launch(self, prompt: str, workdir: str, no_tty: bool, extra_args: list[str] | None = None, env_file: str | None = None, timeout_s: int | None = None, session_file: str | None = None, on_conversation_id=None) -> int`, `session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]`, `resume_argv(self, session_file: str, metadata: dict | None = None) -> list[str]`, `bare_terminal_argv(self, scope_dir: str = "") -> list[str]`, `watch_transcript(self, workdir: str, scope_dir: Path | None, before: Path | None, run_id: int | None, db: Path, result: dict | None = None, session_file: str = "", launched_at: float = 0.0, stop_event=None) -> None`, `extract_last_assistant_text(self, transcript_path: str, max_chars: int | None = 4000) -> str`, `tool_error_rate(self, transcript_path: str, since_line: int = 0) -> tuple[int, int] | None`.
  - Shared helpers in `backend.py`: `check_binary(binary: str) -> None`, `run_blocking_cmd(argv: list[str], workdir: str, env_file: str | None = None, timeout_s: int | None = None) -> int`, `exec_tty(argv: list[str], workdir: str, env_file: str | None = None) -> int`.
  - `OmpHarness(Harness)` owning all `omp` behavior (`resume_argv`, `bare_terminal_argv`, `watch_transcript`, `extract_last_assistant_text`, `tool_error_rate`).
  - `store.DEFAULT_HARNESS = "agy"`, `store.SUPPORTED_HARNESSES = ("agy", "omp")`, `store.default_harness() -> str`.
  - `doctor.check` inspecting `("git-wt", "agy-guard", "omp", "gh", "glab", "jira")`.

- [ ] **Step 1: Write failing tests in `tests/test_backend.py` and `tests/test_store.py`**

```python
# tests/test_backend.py
def test_harness_is_abstract():
    import pytest
    from workagent.backend import Harness
    with pytest.raises(TypeError):
        Harness()


def test_omp_harness_resume_and_bare_terminal_argv(tmp_path):
    from workagent.backend import OmpHarness
    h = OmpHarness()
    assert h.binary == "omp"
    assert h.bare_terminal_argv("") == ["omp"]
    assert h.bare_terminal_argv("/tmp/scope") == ["omp", "--session-dir", "/tmp/scope"]
    missing = tmp_path / "fresh.jsonl"
    assert h.resume_argv(str(missing)) == ["omp", "--session-dir", str(tmp_path)]
    ready = tmp_path / "ready.jsonl"
    ready.write_text('{"type":"session"}\n', encoding="utf-8")
    assert h.resume_argv(str(ready)) == ["omp", "--resume", str(ready)]


# tests/test_store.py
def test_default_harness_is_agy(isolated_config):
    from workagent import store
    assert store.DEFAULT_HARNESS == "agy"
    assert set(store.SUPPORTED_HARNESSES) == {"agy", "omp"}
    assert store.default_harness() == "agy"
    cfg = store.load_config()
    assert cfg["default_harness"] == "agy"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_backend.py tests/test_store.py -v`
Expected: FAIL (`TypeError` not raised for `Harness()`, `OmpHarness.resume_argv` missing, `DEFAULT_HARNESS == "omp"`).

- [ ] **Step 3: Implement `Harness(abc.ABC)`, shared process helpers, `OmpHarness` methods, `store.default_harness()`, and `doctor.py` update**

```python
# src/workagent/backend.py (core structure)
import abc
import signal
from pathlib import Path
from typing import Callable


def check_binary(binary: str) -> None:
    if shutil.which(binary) is None:
        raise HarnessError(f"`{binary}` not found on PATH")


def run_blocking_cmd(argv: list[str], workdir: str, env_file: str | None = None,
                     timeout_s: int | None = None) -> int:
    from . import env as _env
    from .errors import HarnessTimeout as _HarnessTimeout
    merged = _env.merged_env(env_file)
    if timeout_s is None:
        return subprocess.run(argv, cwd=workdir, env=merged).returncode
    proc = subprocess.Popen(argv, cwd=workdir, env=merged, start_new_session=True)
    try:
        proc.wait(timeout=timeout_s)
        return proc.returncode
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        raise _HarnessTimeout(f"command timed out: {' '.join(argv)}")


def exec_tty(argv: list[str], workdir: str, env_file: str | None = None) -> int:
    from . import env as _env
    os.environ.update(_env.launch_env(env_file))
    cd_worktree(workdir)
    os.execvp(argv[0], argv)
    return 0


class Harness(abc.ABC):
    """Abstract AI harness interface."""
    name: str = ""
    binary: str = ""
    judge_argv: list[str] = []

    @abc.abstractmethod
    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]: ...

    @abc.abstractmethod
    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None,
               timeout_s: int | None = None,
               session_file: str | None = None,
               on_conversation_id: Callable[[str], None] | None = None) -> int: ...

    @abc.abstractmethod
    def session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]: ...

    @abc.abstractmethod
    def resume_argv(self, session_file: str, metadata: dict | None = None) -> list[str]: ...

    @abc.abstractmethod
    def bare_terminal_argv(self, scope_dir: str = "") -> list[str]: ...

    @abc.abstractmethod
    def watch_transcript(self, workdir: str, scope_dir: Path | None,
                         before: Path | None, run_id: int | None,
                         db: Path, result: dict | None = None,
                         session_file: str = "", launched_at: float = 0.0,
                         stop_event=None) -> None: ...

    @abc.abstractmethod
    def extract_last_assistant_text(self, transcript_path: str,
                                    max_chars: int | None = 4000) -> str: ...

    @abc.abstractmethod
    def tool_error_rate(self, transcript_path: str,
                        since_line: int = 0) -> tuple[int, int] | None: ...
```

Update `OmpHarness(Harness)` in `backend.py` to implement all 8 abstract methods (`command_argv` omits blank `prompt` in TTY mode, `launch` uses `check_binary`, `run_blocking_cmd`, `exec_tty`, `watch_transcript` polls `scope_dir` via `store_sqlite.newest_transcript`, `extract_last_assistant_text` & `tool_error_rate` call `harness_summary`).
Update `store.py` (`DEFAULT_HARNESS = "agy"`, `SUPPORTED_HARNESSES = ("agy", "omp")`, `def default_harness() -> str: return str(load_config().get("default_harness") or DEFAULT_HARNESS)`).
Update `doctor.py:67` to check `("git-wt", "agy-guard", "omp", "gh", "glab", "jira")`.
Update existing `tests/test_store.py` default assertion from `"omp"` to `"agy"`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_backend.py tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/backend.py src/workagent/store.py src/workagent/doctor.py tests/test_backend.py tests/test_store.py
git commit -m "[113] Make Harness an ABC, isolate OmpHarness, default to agy"
```

---

### Task 2: Implement `AgyHarness` (`agy-guard`), Normalized Mirroring, `conversation_summaries.db` Discovery, & `harness_summary` Support

**Files:**
- Create: `src/workagent/agy_harness.py`
- Modify: `src/workagent/backend.py` (register `"agy": AgyHarness()` in `HARNESSES`)
- Modify: `src/workagent/harness_summary.py:10-134`
- Test: `tests/test_backend.py`, `tests/test_harness_summary.py`

**Interfaces:**
- Consumes: `backend.Harness`, `backend.check_binary`, `backend.exec_tty`, `env.merged_env`, `errors.HarnessTimeout`, `store_sqlite.set_run_file_path`, `store_sqlite.set_run_metadata`, `store_sqlite.run_by_file_path`, `store_sqlite.db_path`.
- Produces:
  - `agy_harness.AgyHarness(Harness)` (`name = "agy"`, `binary = "agy-guard"`, `judge_argv = ["--model", "gemini-3.8-flash-low", "--disable-slash-commands", "--mode", "plan"]`).
  - `agy_harness.antigravity_home() -> Path` (respects `ANTIGRAVITY_HOME`, defaults to `Path.home() / ".gemini" / "antigravity-cli"`).
  - `agy_harness.resolve_conversation_id(path: str, metadata: dict | None = None) -> str`.
  - `agy_harness.mirror_brain_transcript(cid: str, dest_path: str) -> bool`.
  - `agy_harness.adopt_from_workspace(worktree: str, created_ts: float, dest_path: str) -> tuple[str, str]` (returns `(conversation_id, dest_path)`).
  - `harness_summary.last_assistant_text` & `harness_summary.tool_error_rate` parsing both `omp` and `agy` (`PLANNER_RESPONSE` / `GENERIC` / `stream-json` `result` & `step_update`) records.

- [ ] **Step 1: Write failing tests in `tests/test_backend.py` and `tests/test_harness_summary.py`**

```python
# tests/test_backend.py
def test_agy_command_argv_attaches_prompt_with_equals():
    from workagent.agy_harness import AgyHarness
    h = AgyHarness()
    assert h.binary == "agy-guard"
    argv_headless = h.command_argv("fix --flag bug", no_tty=True, extra_args=["--model", "gemini-3.8-flash-low"])
    assert argv_headless == [
        "agy-guard",
        "--dangerously-skip-permissions",
        "--output-format", "stream-json",
        "--model", "gemini-3.8-flash-low",
        "-p=fix --flag bug",
    ]
    assert h.command_argv("hello", no_tty=False) == ["agy-guard", "-i=hello"]
    assert h.command_argv("", no_tty=False) == ["agy-guard"]
    assert h.bare_terminal_argv("/tmp/ignored") == ["agy-guard"]
    assert h.judge_argv == ["--model", "gemini-3.8-flash-low", "--disable-slash-commands", "--mode", "plan"]


def test_agy_session_file_flag_and_resume_argv(tmp_path):
    import json
    from workagent.agy_harness import AgyHarness
    h = AgyHarness()
    fresh = tmp_path / "fresh.jsonl"
    assert h.session_file_flag(str(fresh)) == []
    assert h.resume_argv(str(fresh)) == ["agy-guard"]
    assert h.session_file_flag(str(fresh), metadata={"conversation_id": "cid-1"}) == ["--conversation", "cid-1"]
    fresh.write_text(json.dumps({"type": "agy_meta", "conversation_id": "cid-2"}) + "\n", encoding="utf-8")
    assert h.session_file_flag(str(fresh)) == ["--conversation", "cid-2"]
    assert h.resume_argv(str(fresh)) == ["agy-guard", "--conversation", "cid-2"]


def test_agy_launch_headless_streams_and_mirrors(tmp_path, monkeypatch):
    import io, json
    from workagent import agy_harness
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "agy-home"))
    monkeypatch.setattr("workagent.backend.shutil.which", lambda n: f"/usr/bin/{n}")
    session = tmp_path / "runs" / "agy" / "feat" / "run.jsonl"
    session.parent.mkdir(parents=True)
    ndjson = "\n".join([
        json.dumps({"event": "init", "conversation_id": "conv-123"}),
        json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Working...\n"}}),
        json.dumps({"event": "result", "result": {"conversation_id": "conv-123", "status": "SUCCESS", "response": "Done\nJOB_DONE"}}),
    ]) + "\n"

    class FakeProc:
        stdout = io.StringIO(ndjson)
        returncode = 0
        pid = 12345
        def wait(self, timeout=None): return 0

    monkeypatch.setattr(agy_harness.subprocess, "Popen", lambda *a, **k: FakeProc())
    seen_cid = []
    code = agy_harness.AgyHarness().launch(
        "do work", str(tmp_path), no_tty=True,
        session_file=str(session), on_conversation_id=seen_cid.append,
    )
    assert code == 0
    assert seen_cid == ["conv-123"]
    assert agy_harness.resolve_conversation_id(str(session)) == "conv-123"
    assert agy_harness.AgyHarness().extract_last_assistant_text(str(session), max_chars=None) == "Done\nJOB_DONE"


def test_agy_adopt_from_workspace_disambiguates_concurrent(tmp_path, monkeypatch):
    import json, sqlite3
    from workagent import agy_harness
    home = tmp_path / "agy-home"
    home.mkdir()
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    wt_a = tmp_path / "wt-a"
    wt_b = tmp_path / "wt-b"
    wt_a.mkdir(); wt_b.mkdir()
    for cid in ("cid-a", "cid-b"):
        log_dir = home / "brain" / cid / ".system_generated" / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "transcript.jsonl").write_text(
            json.dumps({"type": "PLANNER_RESPONSE", "status": "DONE", "content": f"reply-{cid}"}) + "\n",
            encoding="utf-8",
        )
    db = home / "conversation_summaries.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT, last_modified_time TEXT)")
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-a", json.dumps([wt_a.resolve().as_uri()]), "2026-11-16T22:00:05+00:00"))
        conn.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)",
                     ("cid-b", json.dumps([wt_b.resolve().as_uri()]), "2026-11-16T22:00:06+00:00"))
    dest = tmp_path / "runs" / "out.jsonl"
    cid, path = agy_harness.adopt_from_workspace(str(wt_a), 1794866400.0, str(dest))
    assert cid == "cid-a"
    assert agy_harness.AgyHarness().extract_last_assistant_text(path) == "reply-cid-a"


# tests/test_harness_summary.py
def test_agy_planner_response_and_generic_error_rate(tmp_path):
    p = _write(tmp_path / "agy.jsonl", [
        {"type": "agy_meta", "conversation_id": "c-1"},
        {"type": "GENERIC", "status": "DONE", "content": "tool ok"},
        {"type": "GENERIC", "status": "ERROR", "content": "tool failed"},
        {"type": "PLANNER_RESPONSE", "status": "DONE", "content": "final answer"},
    ])
    assert hs.last_assistant_text(p) == "final answer"
    assert hs.tool_error_rate(p) == (1, 2)
    assert hs.tool_error_rate(p, since_line=2) == (1, 1)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_backend.py tests/test_harness_summary.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'workagent.agy_harness'`, `agy` lines ignored by `harness_summary`).

- [ ] **Step 3: Implement `src/workagent/agy_harness.py`, register in `backend.py`, and extend `src/workagent/harness_summary.py`**

```python
# src/workagent/agy_harness.py
"""Antigravity AI harness (`agy-guard` binary, NDJSON streaming, conversation DB)."""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .backend import Harness, check_binary, exec_tty


def antigravity_home() -> Path:
    override = os.environ.get("ANTIGRAVITY_HOME")
    return Path(override).expanduser() if override else Path.home() / ".gemini" / "antigravity-cli"


def resolve_conversation_id(path: str, metadata: dict | None = None) -> str:
    if isinstance(metadata, dict):
        cid = str(metadata.get("conversation_id") or "").strip()
        if cid:
            return cid
    if path:
        try:
            with Path(path).open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        cid = str(obj.get("conversation_id") or "").strip()
                        if cid:
                            return cid
        except OSError:
            pass
        try:
            from . import store_sqlite as _sq
            db = _sq.db_path()
            if db.exists():
                row = _sq.run_by_file_path(db, path, include_deleted_worktrees=True)
                cid = str(((row or {}).get("metadata") or {}).get("conversation_id") or "").strip()
                if cid:
                    return cid
        except Exception:
            pass
    return ""


def _brain_transcript_path(cid: str) -> Path:
    return antigravity_home() / "brain" / cid / ".system_generated" / "logs" / "transcript.jsonl"


def mirror_brain_transcript(cid: str, dest_path: str) -> bool:
    """Copy brain/<cid>/.../transcript.jsonl into dest_path with an agy_meta header."""
    if not cid or not dest_path:
        return False
    src = _brain_transcript_path(cid)
    if not src.is_file() or src.stat().st_size == 0:
        return False
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({"type": "agy_meta", "conversation_id": cid})
    body = src.read_text(encoding="utf-8", errors="replace")
    dest.write_text(header + "\n" + body.lstrip("\n"), encoding="utf-8")
    return True


def _parse_iso_ts(raw: str) -> float:
    try:
        # Handle Go-style nanoseconds or Z suffix safely.
        s = raw.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def find_conversation_for_workspace(workdir: str, since_ts: float,
                                    exclude_ids: set[str] | None = None) -> str:
    """Find newest Antigravity conversation matching *workdir* at/after *since_ts*."""
    home = antigravity_home()
    excluded = exclude_ids or set()
    target_uri = ""
    if workdir:
        try:
            target_uri = Path(workdir).expanduser().resolve().as_uri()
        except Exception:
            target_uri = f"file://{workdir}"
    sum_db = home / "conversation_summaries.db"
    if sum_db.is_file():
        try:
            uri = f"file:{sum_db}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
                rows = conn.execute(
                    "SELECT conversation_id, workspace_uris, last_modified_time "
                    "FROM conversation_summaries ORDER BY last_modified_time DESC LIMIT 50"
                ).fetchall()
            for cid, uris_raw, mod_raw in rows:
                if not cid or cid in excluded:
                    continue
                if _parse_iso_ts(str(mod_raw or "")) + 1.0 < since_ts:
                    continue
                try:
                    uris = json.loads(uris_raw or "[]")
                except ValueError:
                    uris = []
                if not target_uri or target_uri in uris:
                    return str(cid)
        except Exception:
            pass
    # Fallback: scan brain/<cid>/.system_generated/logs/transcript.jsonl mtime.
    brain = home / "brain"
    if brain.is_dir():
        candidates: list[tuple[float, str]] = []
        for d in brain.iterdir():
            if not d.is_dir() or d.name in excluded:
                continue
            t = d / ".system_generated" / "logs" / "transcript.jsonl"
            try:
                mt = t.stat().st_mtime if t.is_file() and t.stat().st_size > 0 else d.stat().st_mtime
            except OSError:
                continue
            if mt + 1.0 >= since_ts:
                candidates.append((mt, d.name))
        if candidates:
            candidates.sort()
            return candidates[-1][1]
    return ""


def adopt_from_workspace(worktree: str, created_ts: float, dest_path: str) -> tuple[str, str]:
    cid = find_conversation_for_workspace(worktree, created_ts)
    if not cid or not dest_path:
        return "", ""
    if mirror_brain_transcript(cid, dest_path):
        return cid, dest_path
    return cid, ""


class AgyHarness(Harness):
    name = "agy"
    binary = "agy-guard"
    judge_argv: list[str] = ["--model", "gemini-3.8-flash-low",
                             "--disable-slash-commands", "--mode", "plan"]

    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None) -> list[str]:
        extra = list(extra_args or [])
        if no_tty:
            return [self.binary, "--dangerously-skip-permissions",
                    "--output-format", "stream-json", *extra, f"-p={prompt}"]
        if prompt:
            return [self.binary, *extra, f"-i={prompt}"]
        return [self.binary, *extra]

    def session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]:
        cid = resolve_conversation_id(path, metadata)
        return ["--conversation", cid] if cid else []

    def resume_argv(self, session_file: str, metadata: dict | None = None) -> list[str]:
        cid = resolve_conversation_id(session_file, metadata)
        return [self.binary, "--conversation", cid] if cid else [self.binary]

    def bare_terminal_argv(self, scope_dir: str = "") -> list[str]:
        return [self.binary]

    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None,
               timeout_s: int | None = None,
               session_file: str | None = None,
               on_conversation_id: Callable[[str], None] | None = None) -> int:
        from . import env as _env
        from .errors import HarnessTimeout as _HarnessTimeout
        argv = self.command_argv(prompt, no_tty, extra_args)
        check_binary(argv[0])
        if not no_tty:
            return exec_tty(argv, workdir, env_file)
        merged = _env.merged_env(env_file)
        proc = subprocess.Popen(
            argv, cwd=workdir, env=merged,
            stdout=subprocess.PIPE, stderr=None, text=True, bufsize=1,
            start_new_session=(timeout_s is not None),
        )
        cid = ""
        result_status = "SUCCESS"
        saw_delta = False
        sf_path = Path(session_file) if session_file else None
        if sf_path is not None:
            sf_path.parent.mkdir(parents=True, exist_ok=True)
        timer: threading.Timer | None = None
        timed_out = False
        if timeout_s is not None:
            def _kill() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                time.sleep(2.0)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            timer = threading.Timer(timeout_s, _kill)
            timer.daemon = True
            timer.start()
        try:
            fh = sf_path.open("a", encoding="utf-8") if sf_path is not None else None
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip("\r\n")
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        print(line, flush=True)
                        continue
                    if not isinstance(ev, dict):
                        continue
                    etype = ev.get("event")
                    if etype == "init":
                        new_cid = str(ev.get("conversation_id") or "").strip()
                        if new_cid and not cid:
                            cid = new_cid
                            if fh is not None:
                                fh.write(json.dumps({"type": "agy_meta", "conversation_id": cid}) + "\n")
                                fh.flush()
                            if on_conversation_id is not None:
                                on_conversation_id(cid)
                    elif etype == "step_update":
                        su = ev.get("step_update") or {}
                        delta = su.get("text_delta")
                        if isinstance(delta, str) and delta:
                            saw_delta = True
                            sys.stdout.write(delta)
                            sys.stdout.flush()
                    elif etype == "result":
                        res = ev.get("result") or {}
                        result_status = str(res.get("status") or "SUCCESS")
                        resp = res.get("response")
                        if not saw_delta and isinstance(resp, str) and resp:
                            if not resp.endswith("\n"):
                                resp += "\n"
                            sys.stdout.write(resp)
                            sys.stdout.flush()
                    if fh is not None:
                        fh.write(line + "\n")
                        fh.flush()
            finally:
                if fh is not None:
                    fh.close()
            rc = proc.wait()
        finally:
            if timer is not None:
                timer.cancel()
        if timed_out:
            raise _HarnessTimeout(f"command timed out: {' '.join(argv)}")
        if cid and session_file:
            mirror_brain_transcript(cid, session_file)
        if rc != 0:
            return rc
        return 0 if result_status.upper() == "SUCCESS" else 1

    def watch_transcript(self, workdir: str, scope_dir: Path | None,
                         before: Path | None, run_id: int | None,
                         db: Path, result: dict | None = None,
                         session_file: str = "", launched_at: float = 0.0,
                         stop_event=None) -> None:
        from . import store_sqlite as _sq
        since = launched_at or time.time()
        brain = antigravity_home() / "brain"
        existing = {d.name for d in brain.iterdir() if d.is_dir()} if brain.is_dir() else set()
        target_path = session_file or (str(scope_dir / f"{_sq.gen_session_id()}.jsonl") if scope_dir else "")
        time.sleep(1.0)
        while stop_event is None or not stop_event.is_set():
            cid = find_conversation_for_workspace(workdir, since, exclude_ids=existing)
            if cid:
                if target_path:
                    mirror_brain_transcript(cid, target_path)
                    if not Path(target_path).is_file():
                        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
                        Path(target_path).write_text(
                            json.dumps({"type": "agy_meta", "conversation_id": cid}) + "\n",
                            encoding="utf-8",
                        )
                    if result is not None:
                        result["session_file"] = target_path
                    if run_id and db.exists():
                        _sq.set_run_file_path(db, run_id, target_path)
                if run_id and db.exists():
                    try:
                        _sq.set_run_metadata(db, run_id, {"conversation_id": cid})
                    except Exception:
                        pass
                return
            if stop_event is not None and stop_event.wait(2.0):
                return
            if stop_event is None:
                time.sleep(2.0)

    def extract_last_assistant_text(self, transcript_path: str,
                                    max_chars: int | None = 4000) -> str:
        from . import harness_summary as _hs
        return _hs.last_assistant_text(transcript_path, max_chars=max_chars)

    def tool_error_rate(self, transcript_path: str,
                        since_line: int = 0) -> tuple[int, int] | None:
        from . import harness_summary as _hs
        return _hs.tool_error_rate(transcript_path, since_line=since_line)
```

Also extend `harness_summary.py` (`last_assistant_text` & `tool_error_rate`) to recognize:
- `type == "PLANNER_RESPONSE"` → `content` string
- `event == "result"` → `(data.get("result") or {}).get("response")`
- `event == "step_update"` (`step_type == "agent_response"`) → accumulated `text_delta`
- `type == "GENERIC"` with `status in ("DONE", "ERROR")` → counts toward `(errors, total)` in `tool_error_rate`.
And register `"agy": AgyHarness()` in `backend.HARNESSES`.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_backend.py tests/test_harness_summary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/agy_harness.py src/workagent/backend.py src/workagent/harness_summary.py tests/test_backend.py tests/test_harness_summary.py
git commit -m "[113] Implement AgyHarness via agy-guard and normalized transcript mirroring"
```

---

### Task 3: Refactor `cli_harness`, `cli_validate`, `suggest_icon`, and Single-Harness CLI Modules + Add Static `omp` Guard

**Files:**
- Modify: `src/workagent/cli_harness.py:122-548,586-625,735-775`
- Modify: `src/workagent/cli_validate.py:183-201,393-400,601-617`
- Modify: `src/workagent/suggest_icon.py:381-422`
- Modify: `src/workagent/cli_start.py:65,72,193,389`
- Modify: `src/workagent/cli_review.py:235-250,519,534,578-587,694`
- Modify: `src/workagent/cli_adhoc.py:72`
- Modify: `src/workagent/cli_action.py:234,256,286,380`
- Modify: `src/workagent/cli_message.py:297`
- Modify: `src/workagent/cli_prodtest_plan.py:38,133`
- Modify: `src/workagent/cli_prodtest_run.py:32`
- Modify: `src/workagent/cli_prodtest_checks.py:276`
- Test: `tests/test_backend.py`, `tests/test_suggest_icon.py`, `tests/test_cli_review_all.py`, `tests/test_cli_validate.py`

**Interfaces:**
- Consumes: `backend.get_harness`, `store.default_harness`, `Harness.launch(..., session_file=..., on_conversation_id=...)`, `Harness.watch_transcript(...)`, `Harness.extract_last_assistant_text(...)`, `Harness.tool_error_rate(...)`.
- Produces:
  - `cli_harness.watch_and_exec(..., harness: str = "", workdir: str = "", session_file: str = "", ...)` delegating watching to `backend.get_harness(harness).watch_transcript(...)`.
  - `cli_harness._run_harness` routing `run_judge` inside `TemporaryDirectory` (`directory`) instead of `worktree`, passing `session_file` and `on_conversation_id` callback into `backend.launch`, and reading text via `harness.extract_last_assistant_text`.
  - `cli_review._child_argv` forwarding `--harness` when set in `review --all`.
  - `suggest_icon.run_suggest_icon(..., harness: str | None = None, ...)` resolving `store.default_harness()` and reading from `judge_path` or `newest_transcript(directory)`.
  - `tests/test_backend.py::test_no_omp_outside_omp_harness`.

- [ ] **Step 1: Write failing tests in `tests/test_backend.py`, `tests/test_suggest_icon.py`, `tests/test_cli_review_all.py`**

```python
# tests/test_backend.py
def test_no_omp_outside_omp_harness():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "workagent"
    violations = []

    def _docstring_nodes(tree):
        docs = set()
        for parent in ast.walk(tree):
            if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if parent.body and isinstance(parent.body[0], ast.Expr) and isinstance(parent.body[0].value, ast.Constant):
                    docs.add(parent.body[0].value)
        return docs

    for py in sorted(root.glob("*.py")):
        if py.name == "doctor.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        docs = _docstring_nodes(tree)
        stack = [tree]
        while stack:
            node = stack.pop()
            if isinstance(node, ast.ClassDef) and node.name == "OmpHarness":
                continue
            if py.name == "backend.py" and isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "HARNESSES" for t in node.targets
            ):
                continue
            if py.name == "store.py" and isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SUPPORTED_HARNESSES" for t in node.targets
            ):
                continue
            if isinstance(node, ast.Constant) and node not in docs and node.value in ("omp", "--session-dir", "--auto-approve"):
                violations.append(f"{py.name}:{node.lineno}:{node.value!r}")
            stack.extend(ast.iter_child_nodes(node))
    assert not violations, f"Found omp logic outside OmpHarness: {violations}"


# tests/test_cli_review_all.py
def test_review_all_forwards_harness_to_children(isolated_config, tmp_path, monkeypatch):
    from workagent import cli, store
    wt = tmp_path / "wt"; wt.mkdir()
    store.record_link("jira:IPG-9", {"worktree": str(wt), "branch": "feat/x", "pr_url": "https://x/pull/9"})
    monkeypatch.setattr("workagent.cli_review._reviewable_keys", lambda *a, **k: [("jira:IPG-9", "https://x/pull/9")])
    spawned = []
    class FakeProc:
        returncode = 0
        def wait(self): return 0
    monkeypatch.setattr("workagent.cli_review.subprocess.Popen", lambda argv, **kw: spawned.append(argv) or FakeProc())
    from tests.cli_helpers import _invoke
    r = _invoke("review", "--all", "--launch", "--harness", "omp", "--yes")
    assert r.exit_code == 0
    assert spawned and "--harness" in spawned[0] and "omp" in spawned[0]
```

Also add a test in `tests/test_suggest_icon.py` verifying `run_suggest_icon(prompt, harness="agy")` writes/reads `judge_path` without needing `omp` `--session-dir` file minting.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_backend.py tests/test_suggest_icon.py tests/test_cli_review_all.py -v`
Expected: FAIL.

- [ ] **Step 3: Refactor `cli_harness.py`, `cli_validate.py`, `suggest_icon.py`, and all CLI `"omp"` fallbacks**

- In `backend.launch`: accept `session_file: str | None = None` and `on_conversation_id=None`, forward them to `get_harness(harness).launch(...)`, and pass `--harness`, `--workdir`, `--session-file` to `watch-and-exec` shim.
- In `cli_harness.py`:
  - Replace `"omp"` defaults with `store.default_harness()`.
  - In `_run_harness` headless `run_once`: define `on_cid(cid)` updating `db` metadata `{"conversation_id": cid}` and `result["session_file"]`, pass `session_file=current_path, on_conversation_id=on_cid` to `backend.launch`, resolve `minted = _sq.newest_transcript(scope_dir)` if `current_path` does not yet exist (`omp`), and read text via `harness.extract_last_assistant_text(current_path, max_chars=None)`.
  - In `run_judge`: pass `directory` (the `TemporaryDirectory`) instead of `worktree or fallback_dir` to `backend.launch(..., session_file=judge_path)`, resolve `target = judge_path if Path(judge_path).is_file() else _sq.newest_transcript(Path(directory))`, extract via `harness.extract_last_assistant_text(str(target), max_chars=None)`.
  - In `_publish_headless_summary`: pass optional `harness_name` or call `backend.get_harness(...).extract_last_assistant_text`.
  - In `_watch_minted_transcript` & `watch_and_exec`: accept `--harness`, `--workdir`, `--session-file` and call `backend.get_harness(harness_name).watch_transcript(...)`.
- In `cli_validate.py`: pass `harness_name` (optional kwarg defaulting to `None`) to `validate_headless` → `_tool_error_rate` → `harness.tool_error_rate` when given, else `harness_summary.tool_error_rate` (which already handles both `omp` and `agy` formats!).
- In `suggest_icon.py`: default `harness: str | None = None` → `store.default_harness()`; pass `session_file=judge_path` to `backend.launch`; check `judge_path` if non-empty else `_sq.newest_transcript(Path(directory))`, extract via `harness_obj.extract_last_assistant_text(...)`.
- In `cli_start.py`, `cli_review.py`, `cli_adhoc.py`, `cli_action.py`, `cli_message.py`, `cli_prodtest_plan.py`, `cli_prodtest_run.py`, `cli_prodtest_checks.py`: replace every `"omp"` default with `store.default_harness()` and update `--harness` / `--session-file` help text to be harness-agnostic; forward `--harness` in `cli_review.py` `_child_argv`; update existing tests whose mocks expect `"omp"` default when `--harness` was omitted (either pass `--harness omp` or update to `agy-guard`).

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_backend.py tests/test_suggest_icon.py tests/test_cli_review_all.py tests/test_cli_validate.py tests/test_cli_start.py tests/test_cli_review.py tests/test_cli_action.py tests/test_cli_message.py tests/test_cli_prodtest_plan.py tests/test_cli_prodtest_run.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/ tests/
git commit -m "[113] Route cli_harness, suggest_icon, validate, and CLI defaults through Harness ABC"
```

---

### Task 4: `sync --harness <name>`, `release fix-default --harness`, `flow run --harness`, & Web Run Resume / Terminal Routing

**Files:**
- Modify: `src/workagent/cli_sync.py:36-116,198-383`
- Modify: `src/workagent/cli_release.py` (`fix_default` CLI option) & `src/workagent/cli_release_fix.py:330`
- Modify: `src/workagent/cli_flow.py:215,237,302` & `src/workagent/cli_flow_run.py` & `src/workagent/cli_flow_steps.py:86,166,188-256` & `src/workagent/cli_flow_util.py:53,189-256,306`
- Modify: `src/workagent/cli_message.py` (`message flow run --harness`)
- Modify: `src/workagent/web_args.py:87,138,159,164-202,466-481`
- Modify: `src/workagent/web_runs.py:38-52,364-480,720-747`
- Modify: `src/workagent/web_runs_routes.py:108-228,280-338,533-745`
- Test: `tests/test_cli_sync.py`, `tests/test_sync.py`, `tests/test_cli_release.py`, `tests/test_cli_flow.py`, `tests/test_cli_flow_gaps.py`, `tests/test_webapp_data.py`, `tests/test_webapp_runs.py`

**Interfaces:**
- Consumes: `backend.get_harness`, `agy_harness.adopt_from_workspace`, `store.default_harness`.
- Produces:
  - `sync --harness <name>` (`Optional[str]`, validated via `backend.get_harness`; auto-launches conflict harness when set OR when `--yes` is set, falling back to `store.default_harness()`).
  - `release fix-default --harness <name>` (`Optional[str]`).
  - `flow run --harness <name>` and `message flow run --harness <name>` (override precedence: `cli_harness or step_cfg.get("harness") or store.default_harness()`).
  - `cli_flow_util.TERMINAL_CAPABLE = ("start", "review", "action", "prodtest")` and `persist_terminal_pause(..., harness_name=...)`.
  - `web_runs._resume_shell_command(worktree, session_file, env_file=None, harness_name="", metadata=None)`.
  - `Run` / `RunDetail` JSON payloads gain `"harness_name": str` and `"resume_command": str`.
  - `POST /api/worktrees/{ref}/terminal` accepts `{"mode": "harness"|"prodtest", "harness": Optional[str]}`.
  - `POST /api/runs/{run_id}/start-terminal` wraps command in `watch-and-exec` when `run_id` + `worktree` + `session_file` exist.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cli_sync.py
def test_sync_harness_option_selects_harness(isolated_config, tmp_path, monkeypatch):
    from workagent import cli
    wt = tmp_path / "wt"; wt.mkdir()
    _link_session(tmp_path / "proj", wt, monkeypatch)
    monkeypatch.setattr(cli.sync_mod, "pull_branch", lambda *a: "up-to-date")
    monkeypatch.setattr(cli.sync_mod, "local_merge", lambda *a, **k: {"status": "conflict", "conflicts": ["a.txt"]})
    monkeypatch.setattr(cli.sync_mod, "auto_resolve_changelog", lambda *a: False)
    seen = []
    monkeypatch.setattr(cli, "_run_harness", lambda name, *a, **k: seen.append(name))
    r = _invoke("sync", "IPG-929", "--merge", "--harness", "omp")
    assert r.exit_code == 0
    assert seen == ["omp"]


# tests/test_webapp_runs.py
def test_resume_shell_command_uses_harness(tmp_path):
    import json
    from workagent.web_runs import _resume_shell_command
    sf = tmp_path / "a.jsonl"
    sf.write_text(json.dumps({"type": "agy_meta", "conversation_id": "conv-9"}) + "\n", encoding="utf-8")
    cmd = _resume_shell_command(str(tmp_path), str(sf), harness_name="agy")
    assert "agy-guard --conversation conv-9" in cmd
    assert "omp" not in cmd
```

Also add failing tests for:
- `release fix-default <repo> --harness omp` (`tests/test_cli_release.py`)
- `flow run <label> <ref> --harness omp` overriding step `config: {"harness": "agy"}` (`tests/test_cli_flow.py`)
- `POST /api/worktrees/{ref}/terminal` with `{"mode": "harness", "harness": "agy"}` spawning `agy-guard` and locking `"agy"` (`tests/test_webapp_runs.py`)
- `POST /api/runs/{id}/start-terminal` wrapping `watch-and-exec` (`tests/test_webapp_runs.py`)
- `Run` JSON containing `"harness_name"` and `"resume_command"` (`tests/test_webapp_runs.py`).

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_cli_sync.py tests/test_cli_release.py tests/test_cli_flow.py tests/test_webapp_runs.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement CLI & web_runs refactoring**

- `cli_sync.py`: change `harness: Optional[str] = typer.Option(None, "--harness", help="On unresolvable conflicts, launch this harness (default: configured when --yes).")`. Validate immediately if non-None via `backend.get_harness(harness)`. Pass `harness_name=harness` through `_sync_one` → `_sync_local_merge` → `_handle_merge_conflict`, where `run_harness_now = bool(harness_name) or yes` and `effective_harness = harness_name or store.default_harness()`.
- `cli_release.py` & `cli_release_fix.py`: add `harness: Optional[str]` option to `fix-default`, validate via `backend.get_harness` if provided, and pass `harness or store.default_harness()` to `_run_harness`.
- `cli_flow.py`, `cli_flow_run.py`, `cli_flow_steps.py`, `cli_flow_util.py`, `cli_message.py`:
  - Add `harness: Optional[str]` to `flow run` and `message flow run`; inject `harness_override=harness` into step execution so each step computes `step_harness = harness_override or step_cfg.get("harness") or store.default_harness()` (passing it to `run_*_step` and `spawn(..., harness=step_harness)`).
  - Update `SYNC_FLAGS` `("harness", "--harness", "str")`, `TERMINAL_CAPABLE = ("start", "review", "action", "prodtest")`, and `persist_terminal_pause(..., harness_name=...)`.
- `web_args.py`: move `"--harness"` to `VAL_FLAGS["sync"]`; add `"--harness"` to `VAL_FLAGS` for `"release"`, `"flow run"`, `"message flow run"`; update `_session_file_for` to inspect `--harness` in `args` and fall back to `store.default_harness()`.
- `web_runs.py` & `web_runs_routes.py`:
  - Update `_resume_shell_command(worktree, session_file, env_file=None, harness_name="", metadata=None)` to build `argv = backend.get_harness(hname).resume_argv(session_file, metadata)` (`" ".join(shlex.quote(a) for a in argv)`).
  - Add `harness_name` field to `Run`, derive/populate it in `_summary` & `_persisted_summary`, and compute `resume_command` cleanly when `transcript_ready` (or `""` otherwise).
  - Update `_claim_terminal_lock(worktree, ref_key="", pid=None, overwrite=False, harness_name="")`, `_open_terminal_watch` (using `harness.bare_terminal_argv(scope)` + `watch-and-exec --harness ... --workdir ... --session-file ...`), `POST /api/worktrees/{ref}/terminal` (`harness = body.get("harness")`), `POST /api/runs/{id}/start-terminal` (wrapping in `watch-and-exec` when `run_id` + `worktree` + `session_file` exist), and `_resolve_session_file` (falling back to `agy_harness.adopt_from_workspace` for `agy` runs).

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_cli_sync.py tests/test_sync.py tests/test_cli_release.py tests/test_cli_flow.py tests/test_cli_flow_gaps.py tests/test_webapp_data.py tests/test_webapp_runs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/ tests/
git commit -m "[113] Add --harness parity for sync, release, flow, and web run resume/terminal"
```

---

### Task 5: Add `workagent config` CLI & `GET /api/config` Endpoint

**Files:**
- Create: `src/workagent/cli_config.py`
- Modify: `src/workagent/cli.py` (register `config_app`)
- Modify: `src/workagent/web_args.py` (`SPECS`, `BOOL_FLAGS`, `VAL_FLAGS`, `SUBCOMMANDS`)
- Modify: `src/workagent/webapp.py` (`GET /api/config`)
- Create: `tests/test_cli_config.py`
- Modify: `tests/test_webapp_core.py`, `tests/test_webapp_data.py`

**Interfaces:**
- Consumes: `store.load_config`, `store.save_config`, `store.default_harness`, `backend.HARNESSES`, `shutil.which`.
- Produces:
  - `workagent config show [--json]`
  - `workagent config set <key> <value> [--json]` (supports `default-harness` / `default_harness`, validates `value in backend.HARNESSES`, exit `2` on invalid key/value)
  - `GET /api/config` → `{"default_harness": str, "supported_harnesses": list[str], "harnesses": [{"name": str, "label": str, "binary": str, "available": bool}]}`
  - `SPECS["config"] = {"confirm": False, "force": False, "key": "config"}`.

- [ ] **Step 1: Write failing tests in `tests/test_cli_config.py` and `tests/test_webapp_core.py`**

```python
# tests/test_cli_config.py
import json
from tests.cli_helpers import _invoke


def test_config_show_and_set_default_harness(isolated_config):
    r1 = _invoke("config", "show", "--json")
    assert r1.exit_code == 0
    assert json.loads(r1.stdout)["default_harness"] == "agy"

    r2 = _invoke("config", "set", "default-harness", "omp", "--json")
    assert r2.exit_code == 0
    assert json.loads(r2.stdout)["default_harness"] == "omp"

    r3 = _invoke("config", "set", "default-harness", "claude")
    assert r3.exit_code == 2


# tests/test_webapp_core.py
def test_get_config_lists_harnesses(client, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}" if b == "agy-guard" else None)
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["default_harness"] == "agy"
    assert body["supported_harnesses"] == ["agy", "omp"]
    by_name = {h["name"]: h for h in body["harnesses"]}
    assert by_name["agy"]["available"] is True and by_name["agy"]["binary"] == "agy-guard"
    assert by_name["omp"]["available"] is False and by_name["omp"]["binary"] == "omp"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_cli_config.py tests/test_webapp_core.py tests/test_webapp_data.py::test_specs_mirror_cli_flags -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cli_config.py`, mount in `cli.py`, register in `web_args.py`, and add `GET /api/config` in `webapp.py`**

```python
# src/workagent/cli_config.py
from __future__ import annotations
import typer
from . import backend, store
from .cli_core import EXIT_USAGE, _catch_harness_errors, _fail, _print_result, app, eprint

config_app = typer.Typer(help="Show or update workagent configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

@config_app.command("show")
@_catch_harness_errors
def config_show(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    cfg = store.load_config()
    _print_result({
        "default_harness": str(cfg.get("default_harness") or store.DEFAULT_HARNESS),
        "supported_harnesses": sorted(backend.HARNESSES),
    }, json_output)

@config_app.command("set")
@_catch_harness_errors
def config_set(
    key: str = typer.Argument(..., help="Config key (default-harness)."),
    value: str = typer.Argument(..., help="Config value."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    norm = key.strip().lower().replace("_", "-")
    if norm != "default-harness":
        _fail(f"unknown config key: {key!r} (supported: default-harness)", EXIT_USAGE)
    if value not in backend.HARNESSES:
        _fail(f"unsupported harness: {value!r} (supported: {', '.join(sorted(backend.HARNESSES))})", EXIT_USAGE)
    cfg = store.load_config()
    cfg["default_harness"] = value
    store.save_config(cfg)
    eprint(f"default_harness = {value}")
    _print_result({"default_harness": value}, json_output)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_cli_config.py tests/test_webapp_core.py tests/test_webapp_data.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_config.py src/workagent/cli.py src/workagent/web_args.py src/workagent/webapp.py tests/test_cli_config.py tests/test_webapp_core.py tests/test_webapp_data.py
git commit -m "[113] Add workagent config show/set CLI and GET /api/config endpoint"
```

---

### Task 6: Frontend `useConfig()`, `<HarnessSelect>`, Launch Pages (`Launch`, `MessageLaunch`, `ProdtestLaunch`), & `RunActions`

**Files:**
- Create: `web/src/components/HarnessSelect.tsx`
- Modify: `web/src/lib/api.ts:277-309` & `api.config`
- Modify: `web/src/lib/queries.ts:6-35,85-95,337-349` (`useConfig`, `invalidateAfterRun`)
- Modify: `web/src/lib/format.ts:76-82` (`resumeCommand`)
- Modify: `web/src/lib/launchConfig.ts:79-117` (`harness: string`)
- Modify: `web/src/lib/launchPrefs.ts:1-111` (persist `harness` per tab)
- Modify: `web/src/lib/messageLaunchConfig.ts:55-138` (`harness` + `--harness` args)
- Modify: `web/src/components/StartForm.tsx:321-420` (`StartFormFields`, `buildStartArgs`, `buildReviewArgs`, `startFlagList`)
- Modify: `web/src/components/RunActions.tsx:35-65`
- Modify: `web/src/pages/Launch.tsx:74-90,115-170,430-495,606-706`
- Modify: `web/src/pages/MessageLaunch.tsx:76-156,377-386`
- Modify: `web/src/pages/ProdtestLaunch.tsx:32-85,153-156`
- Test: `web/src/lib/launchConfig.test.ts`, `web/src/lib/messageLaunchConfig.test.ts`, `web/src/pages/Runs.test.tsx`, `web/src/components/HarnessSelect.test.tsx` (new)

**Interfaces:**
- Consumes: `GET /api/config`, `Run.harness_name`, `Run.resume_command`.
- Produces:
  - `HarnessSelect` (`idPrefix`, `value`, `onChange`, `allowDefault?: boolean`, `defaultLabel?: string`, `variant?: "segmented" | "select"`, `label?: string`, `description?: string`).
  - `useConfig()` returning `ConfigInfo`.
  - `--harness <h>` emission in `buildStartArgs`, `buildReviewArgs`, `Launch` (`sync`, `adhoc`), `buildMessageArgs` (all 5 tabs including `flow`), `ProdtestLaunch`.
  - Dynamic warning banner on `Launch.tsx` showing `agy-guard --dangerously-skip-permissions -p=…` vs `omp -p --auto-approve`.
  - `RunResumeActions` preferring `run.resume_command` (with `resumeCommand(worktree, sessionFile, harnessName)` fallback).

- [ ] **Step 1: Write failing Vitest tests**

```ts
// web/src/lib/launchConfig.test.ts additions
it("emits --harness in buildStartArgs and buildReviewArgs when selected", () => {
  expect(buildStartArgs({ ...INITIAL, ref: "IPG-1", repo: "r", harness: "omp" })).toContain("--harness")
  expect(buildStartArgs({ ...INITIAL, ref: "IPG-1", repo: "r", harness: "omp" })).toContain("omp")
  expect(buildStartArgs({ ...INITIAL, ref: "IPG-1", repo: "r", harness: "" })).not.toContain("--harness")
  expect(buildReviewArgs({ ...INITIAL, ref: "u", repo: "r", harness: "agy" })).toEqual(
    expect.arrayContaining(["--harness", "agy"]),
  )
})

// web/src/lib/messageLaunchConfig.test.ts additions
it("emits --harness across message tabs when selected", () => {
  expect(buildMessageArgs("adhoc", { ref: "m1", prompt: "hi", harness: "agy" })).toEqual(
    expect.arrayContaining(["--harness", "agy"]),
  )
  expect(buildMessageArgs("flow", { ref: "m1", label: "f1", refOverride: "wt1", harness: "omp" })).toEqual(
    expect.arrayContaining(["--harness", "omp"]),
  )
})
```

Also add test in `web/src/pages/Runs.test.tsx` verifying `RunResumeActions` renders `resume_command` (e.g. `cd '/home/x/wt' && agy-guard --conversation conv-1`) when `harness_name: "agy"`, and add `web/src/components/HarnessSelect.test.tsx` testing both `segmented` and `select` variants + `allowDefault`.

- [ ] **Step 2: Run Vitest to verify failure**

Run: `cd web && npm test -- --run`
Expected: FAIL.

- [ ] **Step 3: Implement `HarnessSelect.tsx`, `useConfig()`, Launch/MessageLaunch/ProdtestLaunch updates, and `RunActions`/`format.ts`**

- Implement `HarnessSelect.tsx`:
```tsx
export interface HarnessSelectProps {
  idPrefix: string
  value: string
  onChange: (value: string) => void
  allowDefault?: boolean
  defaultLabel?: string
  variant?: "segmented" | "select"
  label?: string
  description?: string
}

export function HarnessSelect({
  idPrefix,
  value,
  onChange,
  allowDefault = true,
  defaultLabel,
  variant = "segmented",
  label = "Harness",
  description = "AI harness used for this run (defaults to Settings).",
}: HarnessSelectProps) {
  const { data: cfg } = useConfig()
  const def = cfg?.default_harness || "agy"
  const list = cfg?.harnesses ?? [
    { name: "agy", label: "Antigravity (agy-guard)", binary: "agy-guard", available: true },
    { name: "omp", label: "OMP (omp)", binary: "omp", available: true },
  ]
  const options = [
    ...(allowDefault ? [{ value: "", label: defaultLabel ?? `Default (${def})`, hint: `Use configured default (${def})` }] : []),
    ...list.map((h) => ({
      value: h.name,
      label: h.available ? h.name : `${h.name} (missing)`,
      hint: h.available ? h.label : `${h.binary} not found on PATH`,
    })),
  ]
  if (variant === "select") {
    return (
      <div className="grid gap-1.5">
        <Label htmlFor={`${idPrefix}-harness`}>
          <FieldHelp label={label} flag="--harness" description={description} />
        </Label>
        <select
          id={`${idPrefix}-harness`}
          aria-label={label}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
        >
          {options.map((o) => (
            <option key={o.value} value={o.value} title={o.hint}>{o.label}</option>
          ))}
        </select>
      </div>
    )
  }
  return (
    <SegmentedControl
      idPrefix={`${idPrefix}-harness`}
      label={label}
      hint={description}
      options={options}
      value={value}
      onChange={onChange}
    />
  )
}
```
- Update `LaunchForm`, `LaunchPrefs`, `MessageLaunchForm`, `StartFormFields`, `Launch.tsx` (`sync`, `adhoc`, dynamic warning banner using `form.harness || config.default_harness`), `MessageLaunch.tsx`, `ProdtestLaunch.tsx`, `RunActions.tsx`, `Runs.tsx`, `RunDetail.tsx`, `format.ts`, `api.ts`, and `queries.ts` (`invalidateAfterRun` also invalidates `queryKeys.config`).

- [ ] **Step 4: Run Vitest to verify pass**

Run: `cd web && npm test -- --run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/
git commit -m "[113] Add HarnessSelect to Launch, MessageLaunch, ProdtestLaunch, and RunActions"
```

---

### Task 7: Frontend Actions, Flow Modals/Cards, Dashboard Bulk Confirms, Release, WorktreeDetail, & Settings

**Files:**
- Modify: `web/src/lib/messageRunConfig.ts:14-153`
- Modify: `web/src/components/ActionForm.tsx:40-233`
- Modify: `web/src/components/ImmediateRunForm.tsx:65-225`
- Modify: `web/src/components/ChainEditor.tsx:130-380`
- Modify: `web/src/components/FlowWizard.tsx:25-199`
- Modify: `web/src/pages/Dashboard.tsx:110-340`
- Modify: `web/src/components/ReleaseCard.tsx:203-302`
- Modify: `web/src/components/WorktreeDetail.tsx:76-98,313-325`
- Modify: `web/src/pages/Settings.tsx:33-154`
- Test: `web/src/lib/messageRunConfig.test.ts`, `web/src/components/ChainEditor.test.tsx`, `web/src/components/FlowWizard.test.tsx`, `web/src/pages/FlowTemplates.test.tsx`, `web/src/pages/Settings.test.tsx` (new)

**Interfaces:**
- Consumes: `<HarnessSelect>`, `useConfig()`, `useCreateRun()`, `POST /api/worktrees/{ref}/terminal`, `workagent config set default-harness`.
- Produces:
  - `ActionUpsertForm.harness` → `default_options.harness`; `SuggestIconButton` appends `["--harness", harness]` when set.
  - `ImmediateRunForm` / `ImmediateRunDialog`: `<HarnessSelect allowDefault>` for **both** `kind="action"` and `kind="flow"`, emitting `["--harness", h]`.
  - `ChainEditor`: `<HarnessSelect variant="select" allowDefault>` on `start`, `review`, `fix_comments`, `sync`, `action`, `prodtest_plan` (excluded on `cleanup`, `release`).
  - `FlowWizard`: top-level `<HarnessSelect allowDefault defaultLabel="Use template / default">` forwarding `["--harness", h]` to `flow run`.
  - `Dashboard.tsx`: `<HarnessSelect allowDefault>` inside `extras` of `handleSyncAll`, `handleReviewAll`, `handleFixAll` confirm dialogs.
  - `ReleaseCard.tsx`: `<HarnessSelect allowDefault>` in `FixDefaultDialog`.
  - `WorktreeDetail.tsx`: compact `<HarnessSelect variant="select">` next to `Harness` terminal button.
  - `Settings.tsx`: `Default AI Harness` card calling `createRun.mutateAsync({ command: "config", args: ["set", "default-harness", next], confirm: false })`.

- [ ] **Step 1: Write failing Vitest tests**

- Update `web/src/lib/messageRunConfig.test.ts`:
```ts
it("persists harness in action default_options and emits --harness on immediate action/flow runs", () => {
  const args = buildActionUpsertArgs(null, {
    name: "A", label: "a", icon: "Zap", prompt: "p", pre: "", post: "",
    runMode: "headless", runImmediately: false, category: "worktree", harness: "omp",
  })
  const idx = args.indexOf("--default-options")
  expect(JSON.parse(args[idx + 1]!)).toEqual({ run_mode: "headless", harness: "omp" })
  expect(buildImmediateActionArgs("a", "wt-1", "headless", "agy")).toContain("--harness")
  expect(buildImmediateWorktreeFlowArgs("f", "wt-1", "omp")).toEqual(["run", "f", "wt-1", "--harness", "omp", "--yes"])
  expect(buildImmediateFlowArgs("f", "m1", "wt-1", "agy")).toEqual(["flow", "run", "f", "m1", "--ref", "wt-1", "--harness", "agy", "--yes"])
})
```
- Update `web/src/components/ChainEditor.test.tsx`: assert `StepConfigForm` renders Harness select on `start`/`sync`/`prodtest_plan`, NOT on `cleanup`/`release`, and patches `step.config.harness`.
- Update `web/src/components/FlowWizard.test.tsx`: add `useConfig` to the `@/lib/queries` mock, click `"omp"` on the FlowWizard top-level Harness selector, click `"Run flow"`, and assert `mutateAsync` receives `["run", "ship", "jira:IPG-1", "--harness", "omp", "--yes"]`.
- Update any `@/lib/queries` mocks in `FlowTemplates.test.tsx` / `Runs.test.tsx` to provide `useConfig`.
- Create `web/src/pages/Settings.test.tsx`: verify clicking `"omp"` in the `Default AI Harness` card triggers `createRun` with `command: "config", args: ["set", "default-harness", "omp"]`.

- [ ] **Step 2: Run Vitest to verify failure**

Run: `cd web && npm test -- --run`
Expected: FAIL.

- [ ] **Step 3: Implement all remaining Web UI surfaces**

Implement updates in `messageRunConfig.ts`, `ActionForm.tsx`, `ImmediateRunForm.tsx`, `ChainEditor.tsx`, `FlowWizard.tsx`, `Dashboard.tsx`, `ReleaseCard.tsx`, `WorktreeDetail.tsx`, and `Settings.tsx`.

- [ ] **Step 4: Run Vitest and production Vite build**

Run: `cd web && npm test -- --run && npm run build`
Expected: PASS (all unit/component tests green and `web/dist` builds cleanly with zero TypeScript errors).

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "[113] Restore Harness chooser in Actions, Flows, Dashboard, Release, WorktreeDetail, and Settings"
```

---

### Task 8: Docs, Contract/Inventory Parity, CHANGELOG, Full Suite, & Local Install

**Files:**
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/FEATURES-NOTES.md`, `CHANGELOG.md`

- [ ] **Step 1: Update `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `web/FEATURES-NOTES.md`, and `CHANGELOG.md`**

- Document `GET /api/config`, `POST /api/runs` `"config"`, `POST /api/worktrees/{ref}/terminal` `"harness"` body param, `Run.harness_name` + `Run.resume_command`, `sync --harness <name>`, `release fix-default --harness <name>`, `flow run --harness <name>`, and `agy-guard` resume/preview behavior.
- Append one-liner to `CHANGELOG.md` ending with `[113]`.

- [ ] **Step 2: Run full backend + frontend test suites**

Run:
```bash
uv run pytest -v
cd web && npm test -- --run && npm run build
```
Expected: ALL backend pytest tests and ALL frontend Vitest tests + `vite build` pass.

- [ ] **Step 3: Re-install locally and verify `workagent doctor`**

Run:
```bash
./install.sh
workagent doctor
```
Expected: `doctor: ok` (`agy-guard: found`, `web_dist: ok`).

- [ ] **Step 4: Commit**

```bash
git add web/API_CONTRACT.md web/FEATURE_INVENTORY.md web/FEATURES-NOTES.md CHANGELOG.md
git commit -m "[113] Update API contract, feature inventory, and changelog"
```
