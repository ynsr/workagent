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
from datetime import datetime
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
                        if not cid and isinstance(obj.get("result"), dict):
                            cid = str(obj["result"].get("conversation_id") or "").strip()
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


def is_meta_stub(path: str) -> bool:
    """True when *path* holds only agy_meta lines and zero real transcript events."""
    if not path:
        return False
    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return False
        saw_meta = False
        with p.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    return False
                if isinstance(obj, dict) and obj.get("type") == "agy_meta":
                    saw_meta = True
                else:
                    return False
        return saw_meta
    except OSError:
        return False


def refresh_if_meta_stub(path: str, metadata: dict | None = None) -> bool:
    """If *path* is only an agy_meta stub, mirror brain/<cid> into it."""
    if not is_meta_stub(path):
        return False
    cid = resolve_conversation_id(path, metadata)
    if not cid:
        return False
    return mirror_brain_transcript(cid, path)


def _parse_iso_ts(raw: str) -> float:
    try:
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
    if not target_uri:
        return ""
    db_ok = False
    sum_db = home / "conversation_summaries.db"
    if sum_db.is_file():
        try:
            uri = f"file:{sum_db}?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
                rows = conn.execute(
                    "SELECT conversation_id, workspace_uris, last_modified_time "
                    "FROM conversation_summaries ORDER BY last_modified_time DESC LIMIT 50"
                ).fetchall()
            db_ok = True
            for cid, uris_raw, mod_raw in rows:
                if not cid or cid in excluded:
                    continue
                if _parse_iso_ts(str(mod_raw or "")) + 1.0 < since_ts:
                    continue
                try:
                    uris = json.loads(uris_raw or "[]")
                except ValueError:
                    uris = []
                norm_uris = [u.rstrip("/") for u in uris if isinstance(u, str)]
                if target_uri.rstrip("/") in norm_uris:
                    return str(cid)
        except Exception:
            pass
    if db_ok:
        return ""
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


def resolve_worktree_for_run(row: dict, *extra_candidates: str) -> str:
    """Resolve a run row's worktree path across active, inactive, and deleted links."""
    from . import store as _store, worktrees as _wt
    try:
        links = _store.load_links(include_inactive=True, include_deleted=True)
    except Exception:
        return ""
    cmd_name = str(row.get("command") or "").strip()
    candidates: list[str] = []
    ref = str(row.get("worktree_ref") or "").strip()
    if ref:
        candidates.append(ref)
    for cand in extra_candidates:
        c = str(cand or "").strip()
        if c and c not in candidates:
            candidates.append(c)
    args = row.get("args") if isinstance(row.get("args"), list) else []
    for idx in (1, 0):
        if len(args) > idx and isinstance(args[idx], str):
            a = args[idx].strip()
            if a and not a.startswith("-") and a != cmd_name and a not in candidates:
                candidates.append(a)
    for cand in candidates:
        if cand in links and isinstance(links[cand], dict):
            wt = str(links[cand].get("worktree") or "")
            if wt:
                return wt
    for cand in candidates:
        resolved = _wt.resolve_worktree(cand, links)
        if (isinstance(resolved, str) and resolved in links
                and isinstance(links[resolved], dict)):
            wt = str(links[resolved].get("worktree") or "")
            if wt:
                return wt
    return ""


class AgyHarness(Harness):
    name = "agy"
    label = "Antigravity (agy-guard)"
    binary = "agy-guard"
    watches_headless = False
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
        done = threading.Event()
        if timeout_s is not None:
            def _kill() -> None:
                nonlocal timed_out
                if done.is_set():
                    return
                timed_out = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                if done.wait(2.0):
                    return
                poll = getattr(proc, "poll", None)
                if not callable(poll) or poll() is None:
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
                        print(line, file=sys.stderr, flush=True)
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
                        if su.get("step_type") == "agent_response":
                            delta = su.get("text_delta")
                            if isinstance(delta, str) and delta:
                                saw_delta = True
                                sys.stderr.write(delta)
                                sys.stderr.flush()
                    elif etype == "result":
                        res = ev.get("result") or {}
                        result_status = str(res.get("status") or "SUCCESS")
                        resp = res.get("response")
                        if not saw_delta and isinstance(resp, str) and resp:
                            if not resp.endswith("\n"):
                                resp += "\n"
                            sys.stderr.write(resp)
                            sys.stderr.flush()
                    if fh is not None:
                        fh.write(line + "\n")
                        fh.flush()
            finally:
                if fh is not None:
                    fh.close()
            rc = proc.wait()
        finally:
            done.set()
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
        target_path = session_file or (str(scope_dir / f"{_sq.gen_session_id()}.jsonl") if scope_dir else "")
        run_meta: dict | None = None
        if run_id and db and db.exists():
            try:
                row = _sq.get_run(db, run_id, include_deleted_worktrees=True)
                if isinstance((row or {}).get("metadata"), dict):
                    run_meta = row["metadata"]
            except Exception:
                run_meta = None
        resumed_cid = resolve_conversation_id(target_path, run_meta)
        brain = antigravity_home() / "brain"
        existing = {d.name for d in brain.iterdir() if d.is_dir()} if brain.is_dir() else set()
        if resumed_cid:
            existing.discard(resumed_cid)
        if stop_event is not None and stop_event.wait(1.0):
            return
        elif stop_event is None:
            time.sleep(1.0)
        known_cid = ""
        recorded_meta = False
        while stop_event is None or not stop_event.is_set():
            cid = known_cid or find_conversation_for_workspace(workdir, since, exclude_ids=existing)
            if cid:
                known_cid = cid
                if not recorded_meta and run_id and db and db.exists():
                    try:
                        _sq.set_run_metadata(db, run_id, {"conversation_id": cid})
                        recorded_meta = True
                    except Exception:
                        pass
                if not target_path:
                    return
                if mirror_brain_transcript(cid, target_path):
                    if result is not None:
                        result["session_file"] = target_path
                    if run_id and db and db.exists():
                        _sq.set_run_file_path(db, run_id, target_path)
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


from .backend import HARNESSES  # noqa: E402
HARNESSES["agy"] = AgyHarness()

