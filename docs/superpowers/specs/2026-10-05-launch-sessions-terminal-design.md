# Launch sessions + terminal run-mode — design

Date: 2026-10-05. Status: approved (all four points confirmed).
Follows the session-type (`session_type`/`metadata`) and harness-tracking
(one-live-harness lock) precedents.

## 1. Intent

- Every Start/Review/Fix-Comments/Sync run persists a `sessions` row +
  a `runs` row, including previews (Run-agent-now OFF). Previews store
  the full prompt for future analysis; no transcript file is touched.
- Pasting an MR/PR link into Launch → Start switches to the Review tab,
  carries the ref text, and prefills Review → Repository from the
  PR/worktree-linked repo.
- Launch gains a run-mode selector (Preview / Run headless / Run in
  terminal, XOR). Terminal mode spawns the harness command detached in
  the OS terminal, claims the harness lock with `origin='terminal'` +
  PID; the Dashboard shows a blue blinking dot, track PID liveness, and
  offers Stop (SIGTERM→SIGKILL).

## 2. Agreed rules (user-confirmed)

1. **Preview persistence = DB row + full prompt, no transcript.**
   `state='preview'`, empty `file_path`; plus a `runs` row (exit 0,
   summary output). `--dry-run` records nothing. Matches sync's
   `_record_no_harness_session` pattern, extended to start/review.
2. **PR switch = URL-shaped only, toast + keep text.** Client-side
   regex mirroring `refs.py` (`/pull/`, `/merge_requests/`,
   `OWNER/REPO#N`); issue URLs never trigger. User can tab back; text
   preserved.
3. **Review repo prefill reuses `GET /api/default-repo?ref=`.** A raw PR
   URL needs PR → head-branch → linked-worktree resolution before Rule 0
   applies: extend `trackers.default_repo_for_ref` (or a PR-aware wrapper
   used only by `/api/default-repo`) to resolve `kind in (pr, mr)` refs
   via `refs.fetch_pr_info` head_ref → worktree lookup, then fall through
   to the existing rules. Never blanks a manual pick. Best-effort: fetch
   failure → existing rules (tracker single repo or `""`).
4. **Terminal mode reuses the harness lock with `origin='terminal'`.**
   One agent per worktree still holds: headless launches block while a
   terminal agent owns the worktree. PID-reuse guarded by start-time
   check; stale locks reaped on serve boot.

## 3. Data model & migrations

- `sessions` rows for previews: `session_type` in
  `(start, review, sync, fix_comments)`, `initiator_command` = subcommand,
  `prompt` = full harness prompt, `worktree_ref` = link key (or ref when
  no link yet), `harness_name` = configured default, `state='preview'`,
  `file_path=''`. Runs row mirrors sync's preview-run shape.
- Harness lock record gains `origin: 'headless' | 'terminal'`
  (default `'headless'` for existing rows), plus `terminal_pid` when
  terminal. `store.record_harness_run(..., origin=...)` param; sweep
  keeps unknown origins intact. `started_at` participates in the
  PID-reuse guard (`/proc/<pid>` start time vs recorded).
- No SQLite migration: harness lock is JSON (`harness.json`); sessions
  schema unchanged. Serve boot reaps terminal locks whose PID is dead.

## 4. CLI

- New `start`/`review` flag `--terminal` (XOR with `--launch`; both →
  usage error exit 2). The CLI owns the flag per the parity checklist
  (mirrored in web `SPECS`/`BOOL_FLAGS` + contract + frontend; Launch
  sends it as a CLI arg, not a run-create flag). Preview (neither)
  prints the harness command (unchanged TTY handover). `--terminal`
  persists the preview row first (so the session exists even if the
  terminal spawn fails), then detached-spawns the harness command in
  the OS terminal (reuse `web_runs._open_terminal_command` core, moved
  to a shared helper), claims the lock with `origin='terminal'`, prints
  the PID. Terminal spawn never `exec`s the shell; CLI returns.
- `sync --harness` conflict path unchanged (headless). `--dry-run`
  writes nothing (unchanged).
- Web `BOOL_FLAGS` mirror + `_validate_args` + `SPECS` passthrough;
  inventory test `test_specs_mirror_cli_flags` covers drift.

## 5. Web backend

- `POST /api/runs` accepts the terminal mode (Launch sends it as an
  arg/passthrough per parity checklist); spawn path branches:
  headless = current child; terminal = detached terminal spawn + lock
  claim + PID recorded on the run summary (spawn failure → session stays
  `preview`, run row records the error). Preview runs keep minting
- New endpoints: `POST /api/terminal/stop` (`{worktree_ref}` →
  SIGTERM→SIGKILL `terminal_pid`, clear lock; 404 when no live
  terminal lock) and terminal liveness surfaced through the existing
  status `harness` cell (`"terminal <pid>"` vs `"omp <pid>"`).
- `API_CONTRACT.md` entry for the new flag/endpoints.

## 6. Frontend

- `launchConfig.ts`: `LaunchForm` gains `runMode: 'preview' |
  'headless' | 'terminal'` (replaces boolean `launch`; `--launch`
  derived for headless, `--terminal` for terminal). Start/Review/
  Fix-Comments tabs share the selector; Sync tab untouched.
- `Launch.tsx` + `StartFormFields`: PR-shaped Start ref →
  `setMode('review')`, keep ref, toast; Review repo effect already
  polls `/api/default-repo` (linked-worktree wins, else single repo).
- `StatusTableMain.tsx`: harness cell with `origin=terminal` + live
  PID → blue blinking dot (`bg-sky-500`), tooltip `terminal <pid>`;
  headless stays emerald. Row offers Stop (calls the new endpoint).
- Sessions page copy: "one row per run, incl. previews".

## 7. Testing & rollout

- Preview-row persistence (start/review/sync incl. prompt stored,
  `--dry-run` writes nothing); PR-switch unit (URLs switch, issue URLs
  don't, manual repo never blanked); terminal lock/stop (claim blocks
  headless, stale reap, PID-reuse guard, stop kills + clears).
- `test_specs_mirror_cli_flags` + `tsc` + `vite build` + full `pytest`.
- Rollout: `./install.sh`, restart serve, verify previews list, PR
  paste switches, terminal run shows blue dot + Stop works.
