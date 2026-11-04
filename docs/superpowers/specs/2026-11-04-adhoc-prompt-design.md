# Adhoc Prompt Design

Intent: run a user-supplied prompt on one or more linked worktrees,
sequentially, reusing the `action run` harness path and the Launch
run-mode prefs pattern. No custom-action record is created; the prompt
is passed on the command line.

Decisions (approved): command name `adhoc`; session type reuses
`custom_action` (no DB migration); multi-worktree failures continue
past failure collecting per-row results.

## CLI

`workagent adhoc REF… --prompt TEXT [--harness N] [--extra-prompt T]
[--env-file F] [--session-file F] [--launch | --terminal]
[--dry-run] [--yes] [--json]`

- `REF…`: one or more worktree refs (same `worktrees.resolve_any`
  resolver as `action run`). Zero refs = usage error (exit 2).
- `--prompt` (required): the adhoc prompt body. Empty = usage error.
- Mode: run-mode segmented control defaults to Headless (persisted per
  `launch.prefs.v1` like other tabs); CLI runs headless when neither
  `--launch` nor `--terminal` is given (differs from `action run`,
  whose default is preview); `--launch`+`--terminal` together = usage error.
- Per ref, in order: resolve → skip deactivated/invalid worktrees
  with a stderr note + `skipped` row (mirrors `prodtest run --all`
  continue-past-failure) → `_guard_harness` → `_run_harness` with
  `{"command": "adhoc"}` → `--extra-prompt` appended last.
- Exit code: 0 when all rows ran, 1 when any row failed/skipped.
  `--json` prints `{"results": [...]}`; human table otherwise.
  TTY confirmation per ref unless `--yes`/non-TTY (same phrasing
  as `action run`).
- Session: recorded via `_run_harness` as `custom_action` with
  metadata `{"action": "adhoc", "mode": …}`.

New file: `src/workagent/cli_adhoc.py` (~120 lines). Register in
`src/workagent/cli.py` next to `cli_action`. Share
`_guard_harness` from `cli_action` (import, no copy).

## Web backend

- `SPECS["adhoc"] = {"confirm": True, "force": False, "key": …}` keyed
  on first positional ref; `BOOL_FLAGS["adhoc"] = ("--launch",
  "--terminal", "--dry-run", "--yes", "--json")`;
  `VAL_FLAGS["adhoc"] = ("--prompt", "--harness", "--extra-prompt",
  "--env-file", "--session-file")`.
- `_session_file_for` maps adhoc refs → per-worktree subdirs using the
  existing normalized branch-name rule.
- `API_CONTRACT.md`: `adhoc REF… --prompt …` row (mutating, confirm,
  target key `adhoc:<first-ref>`); `FEATURE_INVENTORY.md`: CLI row.

## Frontend

- `launchConfig.ts`: `Mode` += `"adhoc"`; `MODES` += `"adhoc"`;
  `LaunchForm` += `{ adhocPrompt: string; adhocRefs: string }`;
  `COPY.adhoc` title/hint; `buildAdhocArgs(form)` →
  `["adhoc", ...refs, "--prompt", text, ...runModeArgs(mode),
  ...extraPromptArgs(form), ...harness/session/env args]`.
- `launchPrefs.ts`: `adhoc: { runMode }` slice in
  `launch.prefs.v1`, default `"headless"` (differs from other tabs'
  `"preview"` default — adhoc has no meaningful preview-per-row).
- `Launch.tsx`: `Adhoc` tab + `AdhocTabFields` (multi-ref textarea
  + prompt textarea); deep-link `?mode=adhoc&ref=` supported.
- `Dashboard.tsx` top bar: `DashboardBulkMenu` += "Adhoc prompt…"
  item (opens Launch adhoc tab with selected refs prefilled; all
  worktrees when none selected — same scope rule as Sync All).
- `WorktreeDetail.tsx` footer: "Adhoc prompt" button → Launch adhoc
  tab with this worktree's key prefilled.

## Tests

- `tests/test_cli_adhoc.py`: usage errors (no refs, empty prompt,
  launch+terminal), dry-run rows, continue-past-failure (one invalid
  + one valid ref), TTY-confirm skip with `--yes`, JSON shape.
- `tests/test_webapp.py`: `test_specs_mirror_cli_flags` covers
  SPECS/BOOL/VAL parity automatically; add adhoc confirm-key case.
- Frontend: no new harness; Launch tab render covered by existing
  Launch tests pattern (add adhoc args-builder unit test in
  `web/src/lib/__tests__/launchConfig.test.ts` if that file exists,
  else skip per throwaway-script rule).

## Non-goals

- No persistence of adhoc prompts (no new table; history stays in
  runs/sessions like other actions).
- No parallel execution (sequential only; harness lock per worktree).
- No `--all` flag in v1 (explicit refs only; bulk scope handled by
  the Dashboard callers).
