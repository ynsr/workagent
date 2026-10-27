# Detached harness runs — design (2026-10-27)

## 1. Understanding

Web/terminal harness runs (`POST /api/runs` children: start/review/fix/sync/cleanup)
are subprocesses of `workagent serve`. Any stop/restart/re-install of the
service kills them mid-work (systemd `KillMode=control-group` kills the whole
cgroup regardless of `start_new_session=True`), and even a survivor would be
forgotten (PID, log buffer, per-target lock all live in RAM).

Goal: harness runs survive `serve` stop/restart/re-install, keep streaming
logs, stay cancellable, and keep their blinking-dot state in the worktree
table after `serve` comes back.

Scope decisions (confirmed): all `/api/runs` children detach (uniform);
per-run log file that `serve` tails (live SSE before and after restart);
cancel works after restart via persisted supervisor PID; detachment via
double-fork + pidfile shim (`workagent _run-supervised`), no systemd
dependency; unit gains `KillMode=process` via reinstall + doctor nudge.

## 2. Lifetime & architecture

Two mandatory halves:

1. **Survival half** — unit gets `KillMode=process` (only the main `serve`
   PID receives the stop signal; children are reparented and keep running).
2. **Memory half** — every run gets a state dir on disk so a fresh `serve`
   can re-adopt survivors (PID + log + target lock).

`serve` no longer spawns `python -m workagent …` directly. It spawns a tiny
supervisor — `workagent _run-supervised --run-dir <dir> -- <same argv…>` —
as the session/group leader, with stdout/stderr redirected to a log file.
The supervisor spawns the real child in its own process group, waits, and
writes an `exit_code` file. After a restart, `serve` scans run dirs: no
`exit_code` + PID alive (+ existing `start_ticks` pid-reuse guard) ⇒
re-create the registry entry as `running`, tail the log file for SSE, kill
the persisted process group for cancel.

Reused untouched: blinking dots need zero changes — harness locks already
live in `harnesses.json` keyed by live PID, and the recorded PIDs all live
inside the surviving group, so `load_harnesses()` still sees them alive
after restart. Headless children already write their own session-finish +
runs-table row from inside the child; the serve-side mirror stays as
fallback.

## 3. Run-dir layout

`~/.config/workagent/runs/<run_id>/`:

```
meta.json    {command, args, target, created, session_file, worktree, pid?, pgid?}
output.log   appended by supervisor (cap ~1 MiB, head-truncated, truncated flag set)
pid          supervisor-written child PID (+ start_ticks, same guard as harnesses.json)
exit_code    supervisor-written on child exit (absent ⇒ still running)
```

- Created at `POST /api/runs` time (before spawn).
- `Run` keeps its in-memory shape; only the byte source changes:
  pipe-reader ⇒ file tailer. Post-restart the tailer backfills `run.lines`
  from `output.log` (last ~10k lines max) and keeps watching.
- `_mirror_run` reads from the same tail source; `MAX_RUN_OUTPUT_LINES`
  bound unchanged.
- No migration, no schema change; old `serve` + new unit degrades
  gracefully.
- Retention: newest 100 dirs with `exit_code` kept; running ones never
  pruned. Dirs with `meta.json` but no `pid`/`exit_code` (spawn failure)
  are marked `failed` and released at startup sweep.

## 4. Spawn / rehydrate / cancel

- **Spawn** (`web_runs._spawn`): `serve` creates run dir + `meta.json`,
  spawns the supervisor with stdout/stderr → `output.log`. Supervisor
  (~60 lines, in `cli_harness.py` or `web_runs.py`): `setsid`, spawn real
  child in its own pgid, write `pid` (+ `start_ticks`), wait, write
  `exit_code`. `serve` waits only for the pre-exec handshake, not
  completion.
- **Rehydrate** (at `create_app` startup): scan `runs/*/`. `exit_code`
  present → skip. Else apply `_pid_alive` + ticks guard: alive ⇒ re-create
  `Registry` entry (`running`, target lock re-claimed, tailer at end of
  log); dead/stale ⇒ write `exit_code = -1`, mirror once, release target.
- **Cancel** (`POST /api/runs/{id}/cancel`): read pgid from file (ticks
  guard — never kill a reused PID), `SIGTERM` → 10s `CANCEL_GRACE` →
  `SIGKILL`; supervisor writes `exit_code`; registry → `cancelled`.
  Identical pre/post restart.
- **Concurrency:** per-run `flock` (`runs/<run_id>.lock`, same pattern as
  `harnesses.json.lock`) serializes adopt/cancel/mirror across two `serve`
  instances during re-install; per-target 409 conflict preserved via
  rehydrated locks.

## 5. KillMode migration

- `render_unit()` gains `KillMode=process` under `[Service]`; nothing else
  changes.
- `service install` / re-install rewrites the unit + `daemon-reload`.
- Existing installs: `doctor` (or `serve` startup log) warns when the
  on-disk unit lacks `KillMode=process`, pointing at
  `workagent service install --yes`. No silent rewrite.
- Degradation is safe both ways: new unit + old code ⇒ today's behavior
  minus group-kill; new code + old unit ⇒ supervisor works but stops kill
  the tree (same as today, no corruption).

## 6. Logging

- Supervisor appends raw child stdout/stderr to `output.log`
  (line-buffered); ANSI stripped at tail time by existing `_strip_ansi`,
  not at write.
- Live SSE unchanged (tailer feeds `run.append()`; 10k-line RAM cap +
  `truncated` flag preserved).
- Disk bound ~1 MiB per file (head truncation, `truncated=true` in UI);
  only the supervisor writes, `serve` only reads.

## 7. Failure modes

- Supervisor dies pre-`pid` ⇒ startup sweep marks `failed`, releases
  target (today's `OSError` path).
- Child outlives harness lock ⇒ existing `_sweep` reclaims dead-PID locks;
  live-PID locks keep the dot (correct — the process runs).
- PID reuse across reboot ⇒ `start_ticks` guard: never adopt, never kill.
- Disk full ⇒ supervisor exits non-zero, `exit_code` records it, run shows
  `failed` with whatever tail fit — never a silent hang.
- Double `serve` ⇒ per-run `flock` + rehydrated target locks.

## 8. Testing

- Unit: rehydrate matrix (alive → running; dead → mirrored failed; reused
  PID → dropped), log-cap truncation, supervisor `meta/pid/exit_code`
  lifecycle with fake child.
- Integration: real `serve` → launch run → SIGTERM serve → restart →
  assert `running` + SSE tail continues + post-restart cancel (via
  `setsid` semantics even without systemd in test).
- Existing suites: `test_webapp.py` (incl. `test_specs_mirror_cli_flags`),
  `test_cli.py` run/harness tests; hidden `_run-supervised` command
  covered like `watch-and-exec`.
- Manual: `service install`, long harness run,
  `systemctl --user restart workagent`, confirm dot + log + cancel.
