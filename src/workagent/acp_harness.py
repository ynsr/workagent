"""Generic ACP (Agent Client Protocol) harness layer (issue #112).

`AcpClient`: stdio JSON-RPC 2.0 transport — line-delimited JSON, id
correlation, reader thread, notification callback, defensive auto-allow of
agent→client `session/request_permission`, SIGTERM cleanup (no orphans).
Always spawns with `env.merged_env(env_file)` so `<config_dir>/env.sh`
is sourced for every ACP run and options probe. Errors → HarnessError;
timeouts → HarnessTimeout.

`AcpHarness(Harness, abc.ABC)`: `supports_acp = True`; shared ACP
lifecycle `initialize → authenticate → session/new|load → apply
model/effort → prompt turns → close` via `launch_acp(...)` and
`discover_options(env_file=None)`. Abstract hooks: `acp_argv()`,
`authenticate_method()`, `parse_options()`, `extract_question()`,
`model_config_id()`, `effort_config_id()`. Unknown model/effort warns on
stderr and skips `set_config_option` (harness default applies).

`OmpAcpHarness(AcpHarness)`: `name = "omp"` — owns all non-ACP methods
with `--approval-mode=yolo` (and `--auto-approve` in `-p` headless) on
every argv builder; `model_effort_cli_args` → `--model=<v>`,
`--thinking=<v>`; ACP hooks map `model`/`thinking` configOptions.
"""

from __future__ import annotations

import abc
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .backend import Harness, check_binary, exec_tty, run_blocking_cmd
from .errors import HarnessError, HarnessTimeout


@dataclass
class HarnessOptions:
    """ACP-discovered model/effort selection. Empty list ⇒ UI disables input."""
    models: list[dict] = field(default_factory=list)
    efforts: list[dict] = field(default_factory=list)
    current_model: str = ""
    current_effort: str = ""


@dataclass
class Question:
    qid: str
    text: str
    options: list[dict] = field(default_factory=list)
    multi: bool = False


def _emit(event: dict) -> None:
    """Structured ACP event: one JSON object per stdout line prefixed `ACP:`."""
    sys.stdout.write("ACP:" + json.dumps(event) + "\n")
    sys.stdout.flush()


def _mirror(session_file: str | None, event: dict) -> None:
    if not session_file:
        return
    try:
        p = Path(session_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass


_DEFAULT_REQUEST_TIMEOUT_S = 300.0


_DEFAULT_REQUEST_TIMEOUT_S = 300.0


_DEFAULT_REQUEST_TIMEOUT_S = 300.0


_DEFAULT_REQUEST_TIMEOUT_S = 300.0


_DEFAULT_REQUEST_TIMEOUT_S = 300.0


class AcpClient:
    """Stdio JSON-RPC 2.0 client for `<bin> acp` agents."""

    def __init__(self, argv: list[str], workdir: str = "",
                 env_file: str | None = None,
                 timeout_s: int | None = None,
                 on_notification: Callable[[dict], None] | None = None) -> None:
        from . import env as _env
        check_binary(argv[0])
        merged = _env.merged_env(env_file if env_file is not None else None)
        try:
            self._proc = subprocess.Popen(
                argv, cwd=workdir or None, env=merged,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True)
        except OSError as e:
            raise HarnessError(f"cannot spawn {' '.join(argv)}: {e}")
        self._timeout = timeout_s
        self._seq = 0
        self._lock = threading.Lock()
        self._pending: dict[Any, queue.Queue] = {}
        self._notify_cb = on_notification
        self._notify_queue: queue.Queue = queue.Queue()
        self._closed = False
        self.pid = self._proc.pid
        self._reader = threading.Thread(target=self._read_loop,
                                        name="acp-reader", daemon=True)
        self._reader.start()

    def set_notification_handler(self, cb: Callable[[dict], None] | None) -> None:
        self._notify_cb = cb

    def _send(self, payload: dict) -> None:
        if self._proc.stdin is None:
            raise HarnessError("ACP agent stdin is closed")
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise HarnessError(f"ACP agent write failed: {e}")

    def _read_loop(self) -> None:
        out = self._proc.stdout
        if out is None:
            return
        try:
            for raw in out:
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # malformed lines are skipped
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    self._handle_agent_request(msg)
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    with self._lock:
                        q = self._pending.get(msg["id"])
                    if q is not None:
                        q.put(msg)
                    continue
                if msg.get("method") == "session/update":
                    params = msg.get("params") or {}
                    self._notify_queue.put(params)
                    cb = self._notify_cb
                    if cb is not None:
                        try:
                            cb(params)
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            # Unblock waiters on child death.
            with self._lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for q in pending:
                try:
                    q.put({"error": {"code": -32000, "message": "agent exited"}})
                except Exception:
                    pass

    def _handle_agent_request(self, msg: dict) -> None:
        method = str(msg.get("method") or "")
        rid = msg.get("id")
        params = msg.get("params") or {}
        if method == "session/request_permission":
            # Defensive auto-allow: no permission UI (spec decision).
            result: dict = {"outcome": {"outcome": "approved"}}
            opts = params.get("options") or []
            if isinstance(opts, list) and opts:
                first = opts[0] if isinstance(opts[0], dict) else {}
                oid = first.get("optionId", "allow")
                result = {"outcome": {"outcome": "selected", "optionId": oid}}
            try:
                self._send({"jsonrpc": "2.0", "id": rid, "result": result})
            except HarnessError:
                pass
            return
        try:
            self._send({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": f"unknown method {method}"}})
        except HarnessError:
            pass

    def request(self, method: str, params: dict | None = None,
                timeout_s: float | None = None) -> Any:
        with self._lock:
            if self._closed:
                raise HarnessError("ACP client is closed")
            self._seq += 1
            rid = self._seq
            q: queue.Queue = queue.Queue()
            self._pending[rid] = q
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
        except HarnessError:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        wait = timeout_s if timeout_s is not None else self._timeout
        if wait is None:
            wait = _DEFAULT_REQUEST_TIMEOUT_S
        try:
            msg = q.get(timeout=wait)
        except queue.Empty:
            with self._lock:
                self._pending.pop(rid, None)
            self._kill()
            raise HarnessTimeout(f"ACP request timed out: {method}")
        if isinstance(msg, dict) and "error" in msg:
            err = msg["error"]
            raise HarnessError(
                f"ACP {method} failed: {err}" if isinstance(err, dict) else f"ACP {method} failed")
        if isinstance(msg, dict) and "result" in msg:
            return msg["result"]
        raise HarnessError(f"ACP agent exited during {method}")

    def drain_notifications(self) -> list[dict]:
        out = []
        try:
            while True:
                out.append(self._notify_queue.get_nowait())
        except queue.Empty:
            pass
        return out

    def close(self) -> None:
        with self._lock:
            self._closed = True
            pending = list(self._pending.values())
            self._pending.clear()
        for q in pending:
            try:
                q.put({"error": {"code": -32000, "message": "agent exited"}})
            except Exception:
                pass
        self._kill()

    def _kill(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None:
            return
        try:
            if proc.poll() is not None:
                return
        except Exception:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    proc.kill()
                except Exception:
                    pass


class _AgyOptionMixin:
    """Shared agy `session/new` payload parser (used by AgyAcpHarness)."""

    def parse_options(self, session_new_result: dict) -> HarnessOptions:
        opts = HarnessOptions()
        result = session_new_result or {}
        models = (result.get("models") or {}) if isinstance(result, dict) else {}
        avail = models.get("availableModels") or []
        current = str(models.get("currentModelId") or "")
        cfg = result.get("configOptions") or []
        model_opt = next((o for o in cfg if o.get("configId") == "model"), None)
        if model_opt:
            for o in model_opt.get("options") or []:
                opts.models.append({"id": str(o.get("value", "")),
                                    "name": str(o.get("name", o.get("value", "")))})
            opts.current_model = str(model_opt.get("currentValue") or current or "")
        elif avail:
            for m in avail:
                opts.models.append({"id": str(m.get("modelId", "")),
                                    "name": str(m.get("name", m.get("modelId", "")))})
            opts.current_model = current
        effort_opt = next((o for o in cfg if o.get("configId") == "thought_level"), None)
        if effort_opt:
            for o in effort_opt.get("options") or []:
                opts.efforts.append({"id": str(o.get("value", "")),
                                     "name": str(o.get("name", o.get("value", "")))})
            opts.current_effort = str(effort_opt.get("currentValue") or "")
        return opts


class _OmpOptionMixin:
    """Shared omp `session/new` payload parser (used by OmpAcpHarness)."""

    def parse_options(self, session_new_result: dict) -> HarnessOptions:
        opts = HarnessOptions()
        cfg = (session_new_result or {}).get("configOptions") or []
        model_opt = next((o for o in cfg if o.get("configId") == "model"), None)
        if model_opt:
            for o in model_opt.get("options") or []:
                opts.models.append({"id": str(o.get("value", "")),
                                    "name": str(o.get("name", o.get("value", "")))})
            opts.current_model = str(model_opt.get("currentValue") or "")
        effort_opt = next((o for o in cfg if o.get("configId") == "thinking"), None)
        if effort_opt:
            for o in effort_opt.get("options") or []:
                opts.efforts.append({"id": str(o.get("value", "")),
                                     "name": str(o.get("name", o.get("value", "")))})
            opts.current_effort = str(effort_opt.get("currentValue") or "")
        return opts


class AcpHarness(Harness, abc.ABC):
    """Shared ACP lifecycle for ACP-capable harnesses."""

    supports_acp: bool = True

    # ── abstract ACP hooks ──
    @abc.abstractmethod
    def acp_argv(self) -> list[str]: ...
    @abc.abstractmethod
    def authenticate_method(self) -> str | None: ...
    @abc.abstractmethod
    def parse_options(self, session_new_result: dict) -> HarnessOptions: ...
    @abc.abstractmethod
    def extract_question(self, update: dict) -> Question | None: ...
    @abc.abstractmethod
    def model_config_id(self) -> str: ...
    @abc.abstractmethod
    def effort_config_id(self) -> str: ...

    # ── ACP lifecycle ──
    def _open_client(self, env_file: str | None, workdir: str,
                     timeout_s: int | None) -> AcpClient:
        return AcpClient(self.acp_argv(), workdir=workdir,
                         env_file=env_file, timeout_s=timeout_s)

    def discover_options(self, env_file: str | None = None) -> HarnessOptions:
        """Probe `<bin> acp` (initialize + session/new in a temp cwd)."""
        import tempfile
        check_binary(self.acp_argv()[0])
        with tempfile.TemporaryDirectory(prefix="workagent-acp-probe-") as directory:
            client = self._open_client(env_file, directory, timeout_s=5)
            try:
                client.request("initialize", {"protocolVersion": 1}, timeout_s=5)
                auth = self.authenticate_method()
                if auth:
                    client.request("authenticate", {"methodId": auth}, timeout_s=5)
                result = client.request("session/new", {"cwd": directory}, timeout_s=5)
                return self.parse_options(result if isinstance(result, dict) else {})
            finally:
                client.close()

    def _apply_model_effort(self, client: AcpClient, session_id: str,
                            model: str | None, effort: str | None,
                            known: HarnessOptions | None) -> None:
        mid, eid = self.model_config_id(), self.effort_config_id()
        known_models = {m["id"] for m in (known.models if known else [])}
        known_efforts = {e["id"] for e in (known.efforts if known else [])}
        if model:
            if known is not None and known_models and model not in known_models:
                print(f"warning: unknown --model {model!r} for {self.name} "
                      f"(using harness default)", file=sys.stderr)
            else:
                client.request("session/set_config_option",
                               {"sessionId": session_id, "configId": mid, "value": model})
        if effort:
            if known is not None and known_efforts and effort not in known_efforts:
                print(f"warning: unknown --effort {effort!r} for {self.name} "
                      f"(using harness default)", file=sys.stderr)
            else:
                client.request("session/set_config_option",
                               {"sessionId": session_id, "configId": eid, "value": effort})

    def _update_to_events(self, session_id: str, update: dict) -> list[dict]:
        """session/update notification → structured ACP: events."""
        inner = update.get("update") or {}
        kind = inner.get("sessionUpdate")
        events: list[dict] = []
        if kind == "agent_message_chunk":
            content = inner.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else ""
            if text:
                events.append({"t": "message", "id": session_id, "text": text})
        elif kind == "agent_thought_chunk":
            content = inner.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else ""
            if text:
                events.append({"t": "thought", "id": session_id, "text": text})
        elif kind in ("tool_call", "tool_call_update"):
            events.append({"t": "tool_call",
                           "id": str(inner.get("toolCallId") or session_id),
                           "name": str(inner.get("title") or inner.get("kind") or ""),
                           "kind": str(inner.get("kind") or ""),
                           "status": str(inner.get("status") or ""),
                           "args": inner.get("rawInput") or inner.get("args") or {},
                           "output": inner.get("content") or ""})
        elif kind == "usage_update":
            events.append({"t": "usage", "id": session_id,
                           "usage": inner.get("tokens") or inner})
        elif kind == "end_turn":
            events.append({"t": "turn_end", "id": session_id})
        elif kind in ("available_commands_update", "session_info_update"):
            pass
        else:
            text = inner.get("content") if isinstance(inner.get("content"), str) else ""
            if text:
                events.append({"t": "message", "id": session_id, "text": text})
        question = self.extract_question(update)
        if question is not None:
            events.append({"t": "question", "id": question.qid,
                           "qid": question.qid, "text": question.text,
                           "options": question.options, "multi": question.multi})
        return events

    def launch_acp(self, prompt: str, workdir: str,
                   model: str | None = None, effort: str | None = None,
                   env_file: str | None = None,
                   timeout_s: int | None = None,
                   session_file: str | None = None,
                   input_stream=None,
                   no_tty: bool = False,
                   on_event: Callable[[dict], None] | None = None) -> int:
        """Run one ACP session to end_turn; stream `ACP:` events to stdout.

        Input channel: `WORKAGENT_RUN_DIR/input.jsonl` tail when set, else
        *input_stream* (TTY stdin), else unattended non-blocking mode
        (question → treat as WAITING_USER_RESPONSE, exit 2).
        """
        check_binary(self.acp_argv()[0])
        received: list[dict] = []

        def _handle(update: dict) -> None:
            for ev in self._update_to_events(str(update.get("sessionId") or ""), update):
                received.append(ev)
                _emit(ev)
                _mirror(session_file, ev)
                if on_event is not None:
                    try:
                        on_event(ev)
                    except Exception:
                        pass

        client = self._open_client(env_file, workdir, timeout_s)
        client.set_notification_handler(_handle)
        try:
            try:
                client.request("initialize", {"protocolVersion": 1})
            except HarnessError as e:
                raise HarnessError(f"{self.name} ACP init failed: {e}\n"
                                   f"{self._stderr_tail()}")
            auth = self.authenticate_method()
            if auth:
                try:
                    client.request("authenticate", {"methodId": auth})
                except HarnessError as e:
                    raise HarnessError(f"{self.name} ACP auth failed: {e}")
            try:
                new = client.request("session/new", {"cwd": workdir or None})
            except HarnessError as e:
                raise HarnessError(f"{self.name} ACP session/new failed: {e}")
            new = new if isinstance(new, dict) else {}
            sid = str(new.get("sessionId") or "")
            if not sid:
                raise HarnessError(f"{self.name} ACP session/new returned no sessionId")
            known: HarnessOptions | None = None
            try:
                known = self.parse_options(new)
            except Exception:
                known = None
            self._apply_model_effort(client, sid, model, effort, known)
            client.request("session/prompt",
                           {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]})
            idx = 0
            while True:
                pending = None
                for pos in range(idx, len(received)):
                    if received[pos].get("t") == "question":
                        pending = received[pos]
                        idx = pos + 1
                        break
                if pending is None:
                    break
                code = self._answer_question(client, sid, pending, session_file,
                                             input_stream, no_tty)
                if code != 0:
                    return code
                # `session/prompt` (answer) only replies once the
                # follow-up turn completes, so its notifications are
                # already in `received`. Loop: a second question in
                # that turn is answered next; otherwise exit.
            return 0
        finally:
            client.close()

    def _answer_question(self, client: AcpClient, session_id: str, ev: dict,
                         session_file: str | None,
                         input_stream, no_tty: bool) -> int:
        """Wait for one answer (input.jsonl tail / stdin / non-block); prompt next turn."""
        qid = str(ev.get("qid") or ev.get("id") or "")
        answer = self._read_answer(ev, input_stream, no_tty)
        if answer is None:
            _emit({"t": "turn_end", "id": session_id, "waiting": True})
            _mirror(session_file, {"t": "turn_end", "id": session_id, "waiting": True})
            print("WAITING_USER_RESPONSE")
            return 2
        _emit({"t": "answer", "id": session_id, "qid": qid, "text": str(answer)})
        _mirror(session_file, {"t": "answer", "id": session_id, "qid": qid,
                               "text": str(answer)})
        client.request("session/prompt",
                       {"sessionId": session_id,
                        "prompt": [{"type": "text", "text": str(answer)}]})
        return 0

    def _read_answer(self, ev: dict, input_stream, no_tty: bool):
        qid = str(ev.get("qid") or ev.get("id") or "")
        run_dir = os.environ.get("WORKAGENT_RUN_DIR", "")
        if run_dir:
            queue_path = Path(run_dir) / "input.jsonl"
            seen = getattr(self, "_consumed_answers", None)
            if seen is None:
                seen = self._consumed_answers = set()
            import time
            interval = 0.2
            while True:
                if getattr(self, "_answer_stop", False):
                    return None
                try:
                    if queue_path.is_file():
                        with queue_path.open("r", encoding="utf-8") as fh:
                            for lineno, line in enumerate(fh):
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    item = json.loads(line)
                                except ValueError:
                                    continue
                                if isinstance(item, dict) and (
                                        not qid or str(item.get("qid") or "") in ("", qid)):
                                    key = (str(queue_path), lineno, line)
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    return item.get("text", item.get("answer", item.get("choice", "")))
                except OSError:
                    pass
                time.sleep(interval)
        stream = input_stream
        if stream is not None:
            try:
                line = stream.readline() if hasattr(stream, "readline") else None
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
        if not no_tty:
            return None
        raise HarnessError("no answer channel: set WORKAGENT_RUN_DIR or pipe an answer line on stdin")

    # Harness base defaults for ACP harnesses (concrete subclasses own argv).
    def model_effort_cli_args(self, model: str | None, effort: str | None) -> list[str]:
        return []


class OmpAcpHarness(_OmpOptionMixin, AcpHarness):
    """OMP harness: all 4 modes + ACP hooks (`omp`, `--approval-mode=yolo`)."""

    name = "omp"
    label = "OMP (omp)"
    binary = "omp"
    judge_argv: list[str] = ["--smol", "--no-skills", "--no-tools",
                             "--no-rules", "--no-context-files",
                             "--no-lsp", "--no-extensions", "--no-pty"]

    def model_config_id(self) -> str:
        return "model"

    def effort_config_id(self) -> str:
        return "thinking"

    def model_effort_cli_args(self, model: str | None, effort: str | None) -> list[str]:
        args: list[str] = []
        if model:
            args.append(f"--model={model}")
        if effort:
            args.append(f"--thinking={effort}")
        return args

    def acp_argv(self) -> list[str]:
        return ["omp", "acp", "--approval-mode=yolo"]

    def authenticate_method(self) -> str | None:
        return "agent"

    def extract_question(self, update: dict) -> Question | None:
        return None  # omp asks in plain text; turn ends end_turn

    def command_argv(self, prompt: str, no_tty: bool,
                     extra_args: list[str] | None = None,
                     model: str | None = None,
                     effort: str | None = None) -> list[str]:
        extra = list(extra_args or []) + self.model_effort_cli_args(model, effort)
        if no_tty:
            return ["omp", "-p", "--approval-mode=yolo", "--auto-approve",
                    *extra, prompt]
        args = ["omp", "--approval-mode=yolo", *extra]
        if prompt:
            args.append(prompt)
        return args

    def launch(self, prompt: str, workdir: str, no_tty: bool,
               extra_args: list[str] | None = None,
               env_file: str | None = None,
               timeout_s: int | None = None,
               session_file: str | None = None,
               on_conversation_id: Callable[[str], None] | None = None,
               model: str | None = None,
               effort: str | None = None) -> int:
        check_binary(self.binary)
        argv = self.command_argv(prompt, no_tty, extra_args, model, effort)
        if no_tty:
            return run_blocking_cmd(argv, workdir, env_file=env_file,
                                    timeout_s=timeout_s)
        return exec_tty(argv, workdir, env_file=env_file)

    def session_file_flag(self, path: str, metadata: dict | None = None) -> list[str]:
        if not path:
            return []
        try:
            p = Path(path)
            if p.is_file() and p.stat().st_size > 0:
                return ["--resume", path]
        except OSError:
            pass
        return ["--session-dir", str(Path(path).parent)]

    def resume_argv(self, session_file: str,
                    metadata: dict | None = None,
                    model: str | None = None,
                    effort: str | None = None) -> list[str]:
        return ["omp", "--approval-mode=yolo",
                *self.model_effort_cli_args(model, effort),
                *self.session_file_flag(session_file, metadata)]

    def bare_terminal_argv(self, scope_dir: str = "",
                           model: str | None = None,
                           effort: str | None = None) -> list[str]:
        args = ["omp", "--approval-mode=yolo",
                *self.model_effort_cli_args(model, effort)]
        if scope_dir:
            args += ["--session-dir", scope_dir]
        return args

    def watch_transcript(self, workdir: str, scope_dir: Path | None,
                         before: Path | None, run_id: int | None,
                         db: Path, result: dict | None = None,
                         session_file: str = "", launched_at: float = 0.0,
                         stop_event=None) -> None:
        if not scope_dir:
            return
        import time
        from . import store_sqlite as _sq
        time.sleep(1.0)
        while stop_event is None or not stop_event.is_set():
            try:
                minted = _sq.newest_transcript(scope_dir)
            except Exception:
                return
            if minted is not None and minted != before:
                if result is not None:
                    result["session_file"] = str(minted)
                if run_id is not None and db is not None:
                    try:
                        _sq.set_run_file_path(db, run_id, str(minted))
                    except Exception:
                        pass
                return
            if stop_event is not None:
                if stop_event.wait(2.0):
                    return
            else:
                time.sleep(2.0)

    def extract_last_assistant_text(self, transcript_path: str,
                                    max_chars: int | None = 4000) -> str:
        from . import harness_summary as _hs
        return _hs.last_assistant_text(transcript_path, max_chars=max_chars)

    def tool_error_rate(self, transcript_path: str,
                        since_line: int = 0) -> tuple[int, int] | None:
        from . import harness_summary as _hs
        return _hs.tool_error_rate(transcript_path, since_line=since_line)


from .backend import HARNESSES as _HARNESSES  # noqa: E402

_HARNESSES[OmpAcpHarness.name] = OmpAcpHarness()
