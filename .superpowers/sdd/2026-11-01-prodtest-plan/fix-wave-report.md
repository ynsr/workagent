# Fix-wave report — 2026-11-01 (blocking findings from final whole-branch review)

Worktree: `.worktrees/prodtest` @ `b8803b8` (parent `7b4fbe8`).

## Fixes
- **P0 `plan_diff_hash`**: `_finish_plan` (`src/workagent/cli_prodtest.py`) now computes
  `current_prodtest_diff` at the dirty-tree tip and persists it in the same upsert
  (`plan_diff_hash`), so byte-identical plans pass the cleanup gate. Also preserves
  `pending_prodtest` when the release columns are already recorded (no clobber to `pending`).
- **P1a Launch tab**: prodtest tab now has a Plan/Run segmented selector
  (`ProdtestActionSelect`; `prodtestAction: plan|run` in `launchConfig`, prefs slice,
  `buildArgs` emits `prodtest plan <ref>` vs `prodtest run <ref>`). `API_CONTRACT.md`
  target-key section + launch-prefs doc updated.
- **P1b Spec §4 fix-comments delta**: `_prodtest_fix_warning` in `cli_review.py` prepends a
  loud WARNING to the fix-comments prompt when the MR touches a prodtest plan and the gate
  reports `blocked:stale` / `blocked:invalid-plan` / `warn:*`. Wired into both
  fix-comments dispatch paths (reuse + fresh worktree).
- **P1c `current_prodtest_diff` CWD fallback**: returns `''` immediately when the repo dir
  is missing/unusable (no `cwd=None` accident). Never raises (unchanged).
- **P1d run binding + fix log-only**: `_plan_release_binding` compares working-tree plan
  bytes against `git show <release_sha>:<plan>` (warn on missing/unverifiable, block on
  mismatch); `_enforce_plan_release_binding` fails by default, `warn` via
  `manifest.fix_policy.plan_binding: warn`. `_create_fix_followup` files a real
  follow-up issue (jira/gh/glab by source-issue URL) for FAIL/BLOCKED when
  `fix_mode` is `issue`/`issue+mr` and the harness reported no `issue_url`;
  `report-only` keeps the old log-only line.

## Tests
- New: `tests/test_prodtest_fixwave.py` (8 tests: hash persist, status preserve,
  missing-dir `''`, warning empty/loud, binding block/missing/no-repo, follow-up
  jira create, report-only no-create).
- `uv run pytest tests/test_prodtest_fixwave.py tests/test_cli_prodtest_plan.py tests/test_cli_prodtest_run.py` → **15 passed**.
- Full non-web suite (`--ignore` 4 fastapi-dependent modules, no `fastapi` in env) →
  **847 passed, 1 failed**: `test_cli_nav.py::test_doctor_missing_and_ok` also fails on
  stashed (pre-change) tree — pre-existing, unrelated.

## Concerns / follow-ups
- `glab issue create` needs a project path/id context; bare-host repos only warn (harness owns follow-up there).
- Frontend `tsc` not run (no node toolchain invoked); Launch/StartForm/launchConfig edits are small and mirror the existing ReviewAction pattern.
- Web parity checklist: `web_args.py` needs no change (no new CLI flags — plan/run are existing subcommands).

---

# Fix-wave round 2 — 2026-11-01 (re-review residuals)

## Findings addressed
- **(1 Important) status preserve over-wide** (`cli_prodtest.py` `_finish_plan`): the
  preserve-when-`release_sha`-set branch now only preserves statuses meaning
  "released, awaiting run" (`pending_prodtest`, `running`). Re-plan after a
  completed run (`passed`/`failed`/`blocked`) resets to `pending` with the new
  revision/diff-hash, so the fresh plan goes through release + run again.
- **(P1d-residual) binding silent pass for worktree-local plans** (`cli_prodtest.py`
  `_plan_release_binding`): when the stored `plan_path` is not under the ephemeral
  checkout/repo (the production worktree-local layout), the binding now resolves the
  canonical repo-relative path `.workagent/prodtest/plans/<KEY>.yaml` (from the plan
  filename) and compares working-tree bytes against
  `git show <release_sha>:<repo-relative-plan>`. Unverifiable-at-release now returns
  `blocked:plan-unverifiable-at-release` by default (fires the gate), downgraded to
  `warn:plan-unverifiable-at-release` only when `manifest.fix_policy.plan_binding: warn`.
  New `_rel_plan_candidates` + `_plan_manifest_allows_warn` helpers; never raises.

## Tests
- New in `tests/test_prodtest_fixwave.py`: `test_finish_plan_resets_status_after_completed_run`
  (passed/failed/blocked → pending), `test_finish_plan_preserves_running`,
  `test_plan_binding_worktree_local_layout_blocks_on_mismatch` (asserts the exact
  `git show deadbeef:.workagent/prodtest/plans/IPG-1.yaml` probe + pass-on-match),
  `test_plan_binding_unverifiable_blocks_by_default` (block default, warn with manifest opt-in).
- `.venv/bin/python -m pytest tests/ -k prodtest` → **44 passed**.
- Targeted: `test_prodtest_fixwave + test_cli_prodtest_plan + test_cli_prodtest_run + test_prodtest_gates` → **33 passed**.

## Concerns
- None new. `git show` byte comparison assumes the plan was committed to the MR/release
  history at the canonical path; a plan renamed out of `plans/` would surface as
  `blocked:plan-unverifiable-at-release` (correct fail-closed direction).

---

# Fix-wave round 3 — 2026-11-01 (review residual: stripped-vs-raw comparison)

## Finding addressed
- **(Important) false `blocked:plan-changed-since-release` on identical plans**
  (`cli_prodtest.py` `_plan_release_binding`, worktree-local path): `run_cmd`
  returns stripped stdout while the working-tree plan was read via raw
  `read_bytes()`, so any normal newline-terminated YAML plan never equaled the
  stripped `git show` bytes and identical plans reported
  `blocked:plan-changed-since-release`. Fix: decode the working-tree bytes as
  UTF-8 and compare symmetrically with `.strip()` on both sides, so
  newline-terminated identical plans pass while genuinely changed plans still
  block.

## Tests
- Extended `test_plan_binding_worktree_local_layout_blocks_on_mismatch` in
  `tests/test_prodtest_fixwave.py` with a newline-terminated fixture
  (`plan_version: 1\n` on disk vs stripped `plan_version: 1` from git-show)
  asserting the binding passes (`None`).
- `.venv/bin/python -m pytest tests/test_prodtest_fixwave.py tests/test_cli_prodtest_plan.py tests/test_cli_prodtest_run.py tests/test_prodtest_gates.py` → **33 passed**.

## Concerns
- `.strip()` on both sides also collapses leading/trailing-whitespace-only
  differences (e.g. extra blank line at EOF); accepted — semantically identical
  plans should not block the gate.
