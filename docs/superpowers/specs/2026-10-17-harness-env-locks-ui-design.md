# Harness env + locks, Candidates, StatusTable, Launch prefs — design (2026-10-17)

## Intent
Six workstreams: env.sh on every harness path (WS1), terminal lock+dots everywhere (WS5),
Candidates Unlinked MR/PRs fixes (WS2), StatusTable filters + deactivated check (WS3),
Launch prefs + layout (WS4). Phase 1 (WS1+WS5) first, then Phase 2 (WS2→WS4).

## Decisions (user-approved)
- Candidates Review → navigate to Launch `mode=review&ref=<url>` (not in-place modal).
- Preview harness command stored/displayed wrapped with `source env.sh &&`.
- Zero-count repo filter in shared `useRepoTabs` (Runs/Sessions inherit).
- Live check: deactivated row `jira:IPG-1043@projectx` persists with `active=0` — storage OK.

## WS1 — env.sh everywhere
- `cli_harness._run_harness` preview branch: always resolve `env_path`, store/display
  wrapped command. Copy-paste + `start-terminal` replay correct for free.
- `POST /api/sessions/{sid}/resume`: look up originating run's `--env-file` (like
  run-resume does); today default-only.
- Files: `cli_harness.py`, `web_runs_routes.py`, `web_runs.py`.
- Tests: update `harness command:` shape assertions; new case for session-resume env-file.

## WS5 — lock + dots for all terminal paths
- `resume`, `start-terminal`, session-`resume` endpoints: `record_harness_run(...,
  origin="terminal", pid)` after spawn; 409 on blocker; sweep reaps on PID death.
- Dots/Stop render from `_harness_cell` already — no frontend change.
- Files: `web_runs_routes.py`, `web_runs.py`. Tests: lock claimed per endpoint, 409 on conflict.

## WS2 — Candidates Unlinked MR/PRs
- Review button → Launch prefill (`mode=review&ref=`).
- Backend: normalize PR URLs (slash/`.git`) + consult worktree paths in `_candidate_prs`.
- Fetch target branch in `fetch_open_prs`; drop default→release rows.
- Attach registered repo `name`; frontend badge/pill.
- Files: `CandidatesCard.tsx`, `cli_candidates.py`, `refs.py`, `API_CONTRACT.md`.

## WS3 — StatusTable
- `useRepoTabs`: only repos owning ≥1 item render; `?repo=` fallback to All.
- Deactivated: diagnostic-first (restart repro → trace serve/`useStatusInactive`); no schema change.

## WS4 — Launch
- `launchPrefs.ts` (mirror `releasePrefs.ts`): per-action run mode, base branch,
  then-fix-comments, new-fix-session, merge-locally. URL params win.
- Header tablist pinned (`flex-nowrap`, `shrink-0`); checkbox row min-height reserve.
- Files: `Launch.tsx`, `StartForm.tsx`, new `lib/launchPrefs.ts`.

## Verification
- `uv run pytest -v` (cwd `/home/bs/projects/personal/harness` per AGENTS.md), `npx tsc --noEmit`,
  live MR/restart repros, CHANGELOG entry (≤150 chars + [no-issue]).
