"""Run registry + spawn/cancel/mirror (from webapp.py).

Import cost mirrors the parent: only stdlib + dataclasses here. FastAPI
imports stay in webapp (create_app).
"""
from __future__ import annotations

import itertools
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .web_args import VAL_FLAGS, ApiError, _first_positional

ANSI_RE = None  # set lazily in _strip_ansi (keeps import cost tiny)
MAX_LINES = 10_000
MAX_RUNS = 100
CANCEL_GRACE = 10.0


def _strip_ansi(text: str) -> str:
    import re

    global ANSI_RE
    if ANSI_RE is None:
        ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    return ANSI_RE.sub("", text)


def _transcript_ready(session_file: str) -> bool:
    """True when the path is a non-empty transcript resumable via --resume.

    omp v18+ rejects --resume on missing AND empty files, so both count as
    missing (the resume endpoint 404s on them; the copy button is gated on
    this flag — a preview row's path is only a ``--session-dir`` hint).
    """
    if not session_file:
        return False
    try:
        p = Path(session_file)
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _db_row_target(command: str, args: list[str]) -> str:
    """Link ref owning a web run's DB row ("" when no worktree known yet).

    Worktree-attached commands link the row; everything else persists with
    a NULL ref so the run is still queryable (runs page shows the command).
    """
    if command in ("start", "review", "sync", "action", "cleanup",
                   "prodtest", "adhoc"):
        try:
            return _worktree_for_target(f"{command}:{_first_positional(args, VAL_FLAGS.get(command, ()))}") or ""
        except Exception:
            return ""
    return ""

def _precreate_run_row(run: Run) -> None:
    """Persist a web run at spawn as one state='running' row, adopted on finish.

    The row id is stashed on the Run (`db_run_id`); the tailer streams
    output into it every ~2s so the log persists even for long/failed runs.
    The child CLI records its own rows for the work it performs (preview,
    launch) — the precreated row is the web invocation itself, finalized by
    `_mirror_run`, never duplicated there. Never raises.
    """
    try:
        from . import store as _store
        from . import store_sqlite as _sq
        db = _sq.db_path()
        if not db.exists():
            return
        try:
            ref = _db_row_target(run.command, list(run.args or []))
            if ref and ref not in _store.load_links(include_inactive=True,
                                                   include_deleted=True):
                ref = ""
        except Exception:
            ref = ""
        args = list(run.args or [])
        # Bare command first so review --fix-comments types as fix_comments
        # (a 'review' row would masquerade as the fix-continue target);
        # two-word subcommands (prodtest run/plan) need the joined argv.
        stype = (_sq.derive_session_type(run.command, "--fix-comments" in args)
                 or _sq.derive_session_type(f"{run.command} {' '.join(args)}".strip()))
        meta = {"origin": "web-run", "web_run_id": run.id,
                "target": run.target or ""}
        try:
            hname = run.harness_name or _harness_arg(args) or _store.default_harness()
            rid = _sq.insert_run(db, run.command, args, None,
                                 worktree_ref=ref or None,
                                 harness_name=hname, initiator_command=run.command,
                                 prompt="", file_path=run.session_file or "",
                                 session_type=stype, metadata=meta,
                                 state="running",
                                 output=[f"$ workagent {' '.join([run.command, *args])}".strip()],
                                 validation={"web_run_id": run.id,
                                             "target": run.target or ""})
        except Exception:
            return
        with run.lock:
            run.db_run_id = rid
            run.db_worktree_ref = ref or ""
            run.session_type = stype
    except Exception as e:
        print(f"[web] run precreate failed: {e}", file=sys.stderr, flush=True)

def _push_run_row(run: Run, *, exit_code: int | None = None) -> None:
    """Stream the live buffer into the precreated row (throttled by caller).

    Never raises; no row (pre-cutover or insert failure) is a silent skip.
    """
    try:
        from . import store_sqlite as _sq
        rid = getattr(run, "db_run_id", 0) or 0
        if not rid:
            return
        db = _sq.db_path()
        if not db.exists():
            return
        with run.lock:
            output = [text for _, text in run.lines]
            truncated = run.truncated
        _sq.update_run(db, rid, exit_code=exit_code, output=output,
                       truncated=truncated)
    except Exception as e:
        print(f"[web] run update failed: {e}", file=sys.stderr, flush=True)

def _review_run_row(db, session_file: str) -> dict | None:
    """The review row owning *session_file* (fix-continue metadata target).

    The fix-continue child inserts its own row for the same transcript, so
    the stored path resolves to that newest row first; its chain head (or
    the row itself) is the review run the fix stamped.
    """
    from . import store_sqlite as _sq
    row = _sq.run_by_file_path(db, session_file, include_deleted_worktrees=True)
    if row is None:
        row = _sq.run_by_file_stem(db, Path(session_file).stem,
                                   include_deleted_worktrees=True)
    if row is None:
        return None
    try:
        root = _sq.transcript_root_run(
            db, str(row.get("file_path") or session_file),
            include_deleted_worktrees=True)
    except Exception:
        root = None
    for cand in (root, row):
        if cand is not None and cand.get("session_type") == "review":
            return cand
    return None


def _stamp_fixed_review(db, run: Run) -> None:
    """Stamp the review run a successful fix-continue execution updated.

    Targets the review row (the transcript's chain head), never the fix
    run's own row. Best effort: a missing review row or DB error is a
    silent skip.
    """
    argv = run.argv or []
    if not (run.command == "review" and run.exit_code == 0
            and "--fix-comments" in argv and "--use-review-session" in argv):
        return
    if not run.session_file:
        return
    try:
        from . import store_sqlite as _sq
        review = _review_run_row(db, run.session_file)
        if review is None:
            return
        from datetime import datetime, timezone
        _sq.set_run_metadata(db, int(review["id"]), {
            "review_comments_fixed_at": datetime.now(timezone.utc).isoformat(),
            "fixed_by_run_id": run.id})
    except Exception:
        pass


def _mirror_run(run: Run) -> None:
    """Best-effort mirror of a web run into the runs table.

    A run precreated at spawn (`_precreate_run_row`) is adopted: its row is
    finalized with the live log, exit code and terminal state, so the web
    invocation persists exactly one row. A headless child launch owns the
    row it recorded itself — matched by the transcript it names (`file_path`
    first, then filename stem for a row never updated to the minted path),
    never by a separate id — so the mirror only closes a row the child left
    'running' and stamps fix-continue metadata on the review run. It never
    adds a row for a transcript that already has one. Anything missing (no
    db, no row) is a silent skip — the live registry remains the source of
    truth.
    """
    precreated = bool(getattr(run, "db_run_id", 0))
    if precreated:
        try:
            from . import store_sqlite as _sq
            db = _sq.db_path()
            with run.lock:
                output = [text for _, text in run.lines]
                truncated = run.truncated
            rid = int(getattr(run, "db_run_id"))
            _sq.update_run(db, rid, exit_code=run.exit_code, output=output,
                           truncated=truncated)
            _sq.finish_run(db, rid, "finished" if run.exit_code == 0 else "failed")
            _stamp_fixed_review(db, run)
        except Exception as e:
            print(f"[web] run mirror failed: {e}", file=sys.stderr, flush=True)
        return
    if not run.session_file:
        return
    argv = run.argv or []
    if "--launch" not in argv:
        return
    try:
        from . import store_sqlite as _sq
        db = _sq.db_path()
        if not db.exists():
            return
        row = _sq.run_by_file_path(db, run.session_file,
                                   include_deleted_worktrees=True)
        if row is None:
            row = _sq.run_by_file_stem(db, Path(run.session_file).stem,
                                       include_deleted_worktrees=True)
        if row is None:
            return
        rid = int(row.get("id") or 0)
        db_file = str(row.get("file_path") or "")
        if db_file and db_file != run.session_file:
            # The child rescoped the path (branch-less → branch-scoped, or
            # pinned → minted): point the live row at the real transcript.
            with run.lock:
                run.session_file = db_file
        if (_sq.get_run(db, rid, include_deleted_worktrees=True)
                or {}).get("state") == "running":
            # The child died before closing its own row: finalize it here.
            with run.lock:
                output = [text for _, text in run.lines]
                truncated = run.truncated
            _sq.update_run(db, rid, exit_code=run.exit_code, output=output,
                           truncated=truncated)
            _sq.finish_run(db, rid, "finished" if run.exit_code == 0 else "failed")
        _stamp_fixed_review(db, run)
    except Exception as e:
        print(f"[web] run mirror failed: {e}", file=sys.stderr, flush=True)


def _worktree_for_target(target: str) -> str:
    """Best-effort worktree path for a run target (review:/sync:/start: keys
    strip to the recorded worktree ref)."""
    from . import store as _store
    try:
        links = _store.load_links()
    except Exception:
        return ""
    if target in links and isinstance(links[target], dict):
        wt = links[target].get("worktree", "")
        if wt:
            return wt
    for prefix in ("start:", "review:", "sync:", "cleanup:", "open:"):
        if target.startswith(prefix):
            ref = target[len(prefix):]
            if ref in links and isinstance(links[ref], dict):
                wt = links[ref].get("worktree", "")
                if wt:
                    return wt
            for key, entry in links.items():
                if not isinstance(entry, dict):
                    continue
                if key == ref or entry.get("branch") == ref \
                        or entry.get("worktree") == ref:
                    wt = entry.get("worktree", "")
                    if wt:
                        return wt
    return ""


def _harness_arg(args: list[str]) -> str:
    for i, a in enumerate(args):
        if a == "--harness" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--harness="):
            return a.split("=", 1)[1]
    return ""


@dataclass
class Run:
    id: str
    command: str
    args: list[str]
    target: str
    argv: list[str] = field(default_factory=list)
    state: str = "running"
    exit_code: int | None = None
    truncated: bool = False
    lines: list[tuple[int, str]] = field(default_factory=list)
    _seq: itertools.count = field(default_factory=itertools.count)
    proc: subprocess.Popen | None = None
    created: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    session_file: str = ""
    session_type: str | None = None
    worktree: str = ""
    run_dir: str = ""
    db_run_id: int = 0
    db_worktree_ref: str = ""
    harness_name: str = ""
    resume_command: str = ""

    def append(self, text: str) -> None:
        with self.lock:
            for ln in _strip_ansi(text).splitlines():
                n = next(self._seq)
                if len(self.lines) >= MAX_LINES:
                    self.lines.pop(0)
                    self.truncated = True
                self.lines.append((n, ln))

    @property
    def last_seq(self) -> int:
        with self.lock:
            return self.lines[-1][0] if self.lines else 0

class Registry:
    def __init__(self) -> None:
        self.runs: dict[str, Run] = {}
        self.targets: dict[str, str] = {}
        self.lock = threading.Lock()

    def create(self, command: str, args: list[str], target: str) -> Run:
        with self.lock:
            finished = [r for r in self.runs.values() if r.state != "running"]
            while len(self.runs) >= MAX_RUNS and finished:
                old = min(finished, key=lambda r: r.created)
                del self.runs[old.id]
                finished.remove(old)
            if target in self.targets:
                raise ApiError(
                    "conflict",
                    f"a run for target {target!r} is already in progress "
                    f"({self.targets[target]})", 409)
            hname = _harness_arg(args)
            run = Run(id=uuid.uuid4().hex[:12], command=command, args=args,
                      target=target, harness_name=hname)
            self.runs[run.id] = run
            self.targets[target] = run.id
            return run

    def get(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise ApiError("not_found", f"no such run: {run_id}", 404)
        return run

    def release_target(self, run: Run) -> None:
        with self.lock:
            if self.targets.get(run.target) == run.id:
                del self.targets[run.target]

    def running(self) -> list[Run]:
        with self.lock:
            return [r for r in self.runs.values() if r.state == "running"]


def _pending_question_from_texts(texts: list[str]) -> dict | None:
    """Newest unanswered ACP question event in log lines (None when none).

    ACP agents stream `ACP: {"t": "question", ...}` lines; an `answer`
    event (or a further `session/prompt` round-trip turn) resolves the
    pending question. Newest question wins. A non-waiting `turn_end`
    from the same unanswered turn (e.g. agy co-emits question +
    end_turn) does not clear it — only the explicit `waiting: true`
    marker emitted when the run blocks on input closes the wait.
    """
    import json as _json
    pending: dict | None = None
    answered_qids: set[str] = set()
    parked = False
    for ln in reversed(texts):
        text = ln[4:] if ln.startswith("ACP:") else ln
        try:
            ev = _json.loads(text)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("t")
        if t == "answer":
            qid = str(ev.get("qid") or ev.get("id") or "")
            if qid:
                answered_qids.add(qid)
            parked = False
            pending = None
        elif t == "turn_end":
            if ev.get("waiting", False):
                parked = True
            elif not parked:
                pending = None
        elif t == "question":
            qid = str(ev.get("qid") or ev.get("id") or "")
            if qid and qid in answered_qids:
                answered_qids.discard(qid)
                parked = False
                continue
            if pending is None:
                raw_opts = ev.get("options") or []
                opts = [str(o.get("label", "") if isinstance(o, dict)
                            else o) for o in raw_opts]
                pending = {"t": "question",
                           "qid": qid,
                           "text": str(ev.get("text") or ""),
                           "options": opts,
                           "multi": bool(ev.get("multi", False))}
    return pending


def _summary(run: Run, lines: list[tuple[int, str]] | None = None) -> dict:
    from . import store as _store
    worktree = run.worktree
    if not worktree and run.state != "running" and run.command in ("start", "review"):
        # Replays after the child recorded its link: the global `start` key
        # can't know the worktree at spawn time.
        worktree = _worktree_for_target(f"{run.command}:{_first_positional(run.args, VAL_FLAGS[run.command])}") or ""
    hname = run.harness_name or _harness_arg(run.args) or _store.default_harness()
    t_ready = _transcript_ready(run.session_file)
    resume_cmd = ""
    if t_ready and worktree and run.session_file:
        try:
            resume_cmd = _resume_shell_command(worktree, run.session_file, harness_name=hname)
        except Exception:
            resume_cmd = ""
    out: dict[str, Any] = {"id": run.id, "command": run.command,
                           "args": run.args, "state": run.state,
                           "exit_code": run.exit_code,
                           "truncated": run.truncated,
                           "created": run.created, "target": run.target,
                           "session_file": run.session_file,
                           "transcript_ready": t_ready,
                           "session_type": run.session_type,
                           "worktree": worktree,
                           "harness_name": hname,
                           "resume_command": resume_cmd,
                           "last_seq": run.last_seq}
    if lines is not None:
        out["lines"] = [{"seq": s, "text": t} for s, t in lines]
        src = [txt for _, txt in lines]
    else:
        with run.lock:
            src = [txt for _, txt in run.lines]
    out["pending_question"] = _pending_question_from_texts(src) if run.state == "running" else None
    return out

def _persisted_lines(row: dict) -> list[dict]:
    """Log lines for a runs-table row (seqs rebuilt in stored order)."""
    text = str(row.get("output") or "")
    if not text:
        return []
    return [{"seq": i, "text": ln} for i, ln in enumerate(text.split("\n"))]



def _persisted_summary(row: dict) -> dict:
    """Map a runs-table row to the live Run summary shape (log included)."""
    import json
    from datetime import datetime
    args = row.get("args", [])
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = []
    exit_code = row.get("exit_code")
    # Same derivation as the live summary (`_finish_state`): exit 2 is
    # "needs input" (a flow step paused on a question), never "failed".
    state = "running" if exit_code is None else _finish_state(exit_code)
    # The stored state wins only where the exit code is silent: a row whose
    # exit code never landed but finished (or failed) stays terminal. A real
    # exit code always wins — exit 2 is needs_input even though _mirror_run
    # finished the run as 'failed', matching the live summary.
    stored = str(row.get("state") or "")
    if exit_code is None:
        if stored == "failed":
            state = "failed"
        elif stored in ("finished", "preview"):
            state = "succeeded"
    created = 0.0
    try:
        created = datetime.fromisoformat(str(row.get("created_at", ""))).timestamp()
    except Exception:
        created = 0.0
    session_file = str(row.get("file_path") or "")
    if session_file:
        # Legacy rows may name the pre-rename sessions/ tree; resolve it so
        # the surfaced path is the one a resume command can actually use.
        try:
            from . import store_sqlite as _sq
            session_file = str(_sq.resolve_transcript_path(session_file))
        except Exception:
            session_file = str(row.get("file_path") or "")
    worktree = ""
    try:
        from . import store as _store
        links = _store.load_links()
        ref = str(row.get("worktree_ref") or "")
        if ref in links and isinstance(links[ref], dict):
            worktree = str(links[ref].get("worktree") or "")
        if not worktree:
            cmd = str(row.get("command") or "")
            if cmd in ("start", "review") and isinstance(args, list):
                worktree = _worktree_for_target(
                    f"{cmd}:{_first_positional(list(args), VAL_FLAGS[cmd])}") or ""
    except Exception:
        worktree = ""
    cmd = str(row.get("command") or "")
    argv = list(args) if isinstance(args, list) else []
    # Step rows store `flow step <kind>` as the command: not a real
    # subcommand (issue #91 showed `workagent flow step review …`
    # in the header). Map back to the step's real command + ref so the
    # header shows (and re-runs) a valid argv and the target resolves.
    # New rows store args=[kind, ref]; legacy rows stored args=[ref].
    if cmd.startswith("flow step "):
        from .store_flows import STEP_KINDS as _STEP_KINDS
        kind = cmd[len("flow step "):]
        if len(argv) >= 2 and argv[0] in _STEP_KINDS:
            kind, ref = argv[0], argv[1]
        else:
            ref = argv[0] if argv else ""
        mapped = {"start": "start", "review": "review",
                    "fix_comments": "review", "sync": "sync",
                    "cleanup": "cleanup"}.get(kind)
        if mapped is not None:
            cmd, argv = mapped, [ref] if ref else []
    target = ""
    try:
        from .web_args import _target_for
        target = _target_for(cmd, argv)
    except Exception:
        target = ""
    from . import store as _store
    hname = str(row.get("harness_name") or "") or _harness_arg(argv) or _store.default_harness()
    t_ready = _transcript_ready(session_file)
    resume_cmd = ""
    if t_ready and worktree and session_file:
        try:
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
            resume_cmd = _resume_shell_command(worktree, session_file, harness_name=hname, metadata=meta)
        except Exception:
            resume_cmd = ""
    persisted = _persisted_lines(row)
    return {"id": f"db-{row.get('id')}", "command": cmd,
            "args": argv, "state": state,
            "exit_code": exit_code, "truncated": bool(row.get("truncated")),
            "created": created, "target": target,
            "session_file": session_file, "worktree": worktree,
            "harness_name": hname,
            "resume_command": resume_cmd,
            "transcript_ready": t_ready,
            "session_type": row.get("session_type"),
            "last_seq": max((ln["seq"] for ln in persisted), default=-1) + 1,
            "validation": row.get("validation") if isinstance(row.get("validation"), dict) else {},
            "pending_question": (_pending_question_from_texts([ln["text"] for ln in persisted]) if state == "running" else None)}
def _persisted_row(run_id: str) -> dict | None:
    """Fetch a runs-table row for a `db-<id>` run id (None otherwise)."""
    if not run_id.startswith("db-"):
        return None
    try:
        from . import store_sqlite as _sq
        db = _sq.db_path()
        for row in _sq.list_runs(db, limit=1000, include_deleted_worktrees=True):
            if f"db-{row.get('id')}" == run_id:
                return row
    except Exception:
        return None
    return None




def _run_dir(run: Run) -> Path:
    """State dir for a run (cached on the Run; derived when missing)."""
    if run.run_dir:
        return Path(run.run_dir)
    from . import run_store as _rs
    return _rs.runs_root() / run.id


def _finish_state(code: int | None) -> str:
    if code == 0:
        return "succeeded"
    if code == 2:
        return "needs_input"
    if code is not None and code < 0:
        return "cancelled"
    return "failed"


def _start_tailer(run: Run, registry: Registry, backfill: bool = False,
                  initial_offset: int | None = None) -> None:
    """Follow output.log into run.lines; close out when exit_code lands.

    Precreated rows (`_precreate_run_row`) stream the log every ~10 polls
    (~2s) so long/failed runs persist progressively — not just at finish.
    `initial_offset` sets the tailer start when the caller already
    backfilled synchronously (skips the stat/read race).
    """
    from . import run_store as _rs
    d = _run_dir(run)

    def tailer() -> None:
        offset = 0
        if not backfill and initial_offset is not None:
            offset = initial_offset
        elif backfill:
            # Post-restart adopt: load the tail (≤10k lines) without OOM.
            try:
                data = (d / "output.log").read_bytes()
                lines = data.decode(errors="replace").splitlines()[-MAX_LINES:]
                for ln in lines:
                    run.append(ln + "\n")
                offset = len(data)
            except OSError:
                offset = 0
        else:
            try:
                offset = (d / "output.log").stat().st_size
            except OSError:
                offset = 0
        polls = 0
        while True:
            try:
                with open(d / "output.log", "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
                    offset = fh.tell()
            except OSError:
                chunk = b""
            if chunk:
                run.append(chunk.decode(errors="replace"))
                if (d / "truncated").exists():
                    run.truncated = True
            polls += 1
            if chunk and polls % 10 == 0:
                _push_run_row(run)
            code = _rs.read_exit(d)
            if code is not None:
                with run.lock:
                    run.exit_code = code
                    if run.state == "running":
                        run.state = _finish_state(code)
                registry.release_target(run)
                _mirror_run(run)
                return
            time.sleep(0.2)

    threading.Thread(target=tailer, daemon=True).start()


def _wait_log(run: Run, needle: str, timeout: float = 5.0) -> bool:
    """Poll run.lines for *needle* (integration-test helper)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with run.lock:
            if any(needle in t for _, t in run.lines):
                return True
        time.sleep(0.05)
    return False


def _spawn(run: Run, registry: Registry) -> None:
    from . import run_store as _rs
    from .run_supervised import supervisor_argv
    d = _rs.new_run_dir(run.id)
    run.run_dir = str(d)
    with run.lock:
        created = run.created
    _rs.write_meta(d, {"command": run.command, "args": run.args,
                       "target": run.target, "created": created,
                       "session_file": run.session_file,
                       "worktree": run.worktree,
                       "harness_name": run.harness_name})
    _precreate_run_row(run)
    try:
        from .env import apply_env_file as _apply_env
        _apply_env()
    except Exception:
        pass
    try:
        subprocess.Popen(
            supervisor_argv(d, run.argv), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, start_new_session=True, close_fds=True)
    except OSError as e:  # spawn failure — surface in the buffer
        run.append(f"[web] spawn failed: {e}\n")
        with run.lock:
            run.exit_code = 1
            if run.state == "running":
                run.state = "failed"
        registry.release_target(run)
        _mirror_run(run)
        return
    _start_tailer(run, registry)


def rehydrate(registry: Registry) -> dict:
    """Re-adopt live runs from disk after a serve restart."""
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
            run = Run(id=d.name, command=str(meta.get("command", "")),
                      args=list(meta.get("args", [])),
                      target=str(meta.get("target", f"run:{d.name}")))
            run.run_dir = str(d)
            run.session_file = str(meta.get("session_file", ""))
            run.worktree = str(meta.get("worktree", ""))
            run.harness_name = str(meta.get("harness_name", ""))
            with registry.lock:
                registry.runs[run.id] = run
                registry.targets.setdefault(run.target, run.id)
            try:
                raw = (d / "output.log").read_bytes()
            except OSError:
                raw = b""
            last_nl = raw.rfind(b"\n")
            complete_raw = raw[: last_nl + 1] if last_nl != -1 else b""
            for ln in complete_raw.decode(errors="replace").splitlines()[-MAX_LINES:]:
                run.append(ln + "\n")
            _start_tailer(run, registry, backfill=False, initial_offset=len(complete_raw))
            adopted.append(d.name)
    _rs.prune_finished()
    return {"adopted": adopted, "closed": closed}


def _cancel(run: Run) -> None:
    from . import run_store as _rs
    from . import store as _store
    # Fake-spawned runs (tests): keep the old in-memory branch.
    proc = run.proc
    if proc is not None:
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

        def escalate() -> None:
            try:
                proc.wait(timeout=CANCEL_GRACE)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            with run.lock:
                if run.state == "running":
                    run.state = "cancelled"

        threading.Thread(target=escalate, daemon=True).start()
        with run.lock:
            if run.state == "running":
                run.state = "cancelled"
        return
    # Detached path: pid group persisted in the run dir (works post-restart).
    d = _run_dir(run)
    pid, ticks = _rs.read_pid(d)
    if pid is None:
        return
    if ticks is not None and _store._proc_start_ticks(pid) not in (None, ticks):
        # Pid reused by an unrelated process: never kill it; close the run.
        with run.lock:
            run.exit_code = -1
            if run.state == "running":
                run.state = "failed"
        _rs.write_exit(d, -1)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    def escalate_detached() -> None:
        deadline = time.time() + CANCEL_GRACE
        while time.time() < deadline:
            if not _store._pid_alive(pid):
                break
            time.sleep(0.2)
        else:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        with run.lock:
            if run.state == "running":
                run.state = "cancelled"

    threading.Thread(target=escalate_detached, daemon=True).start()
    with run.lock:
        if run.state == "running":
            run.state = "cancelled"




def _resume_shell_command(worktree: str, session_file: str,
                          env_file: str | None = None,
                          harness_name: str = "",
                          metadata: dict | None = None) -> str:
    """Shell command that cds to the worktree then continues the session.

    Routes through the configured harness (Harness.resume_argv).
    The env file (explicit or configured default) is sourced first so
    terminal launches see the same env as login shells.
    """
    from . import backend as _backend
    from . import env as _env
    from . import store as _store

    hname = harness_name or _store.default_harness()
    harness = _backend.get_harness(hname)
    argv = harness.resume_argv(session_file, metadata=metadata)
    harness_cmd = " ".join(shlex.quote(a) for a in argv)
    cmd = f"cd {shlex.quote(worktree)} && {harness_cmd}"
    resolved = _env.ensure_env_file(env_file)
    return _env.wrap_command(cmd, resolved)


def _extract_harness_command(texts: list[str]) -> str:
    """Last logged `harness command:` entry, rejoining quote-continued lines.

    Mirrors the RunDetail parser: the CLI preview embeds the prompt as one
    shlex-quoted argv element, so prompts containing newlines split the
    logged command across lines. Scan from the end — the last harness
    command is the runnable one.
    """
    import re

    start = -1
    for i in range(len(texts) - 1, -1, -1):
        if re.search(r"harness command:\s*\S", texts[i] or ""):
            start = i
            break
    if start < 0:
        return ""
    cmd = re.sub(r"^.*harness command:\s*", "", texts[start] or "").rstrip()
    for i in range(start + 1, len(texts)):
        if cmd.count("'") % 2 == 0:
            break
        cmd += "\n" + (texts[i] or "")
    return cmd.strip()


def _open_terminal_command(cmd: str, cwd: str = "") -> int | None:
    """Detached-spawn the OS default terminal running an arbitrary shell
    command (core moved to `cli_harness.spawn_in_terminal`; kept here as
    the web-side alias). `cwd` seeds the terminal's directory — parsed
    from a leading `cd` when omitted. Returns the spawned pid."""
    from .cli_harness import spawn_in_terminal
    return spawn_in_terminal(cmd, cwd)


def _open_terminal(worktree: str, session_file: str,
                   env_file: str | None = None,
                   harness_name: str = "",
                   metadata: dict | None = None) -> int | None:
    """Detached-spawn the OS default terminal resumed on the session
    (mirrors `workagent open` detachment). Returns the spawned pid."""
    return _open_terminal_command(
        _resume_shell_command(worktree, session_file, env_file,
                              harness_name=harness_name, metadata=metadata),
        worktree)
