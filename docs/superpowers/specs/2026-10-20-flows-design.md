# Flow chaining: Start → … → Cleanup → Release — design

Date: 2026-10-20. Approach: **A — orchestrator fans out to existing
entry points** (in-process step dispatch, zero logic duplication).
Status: brainstorming approved §§1–3; spec review gate pending.

## 1. Intent

Chain today's independent actions (Start, Review, Fix-comments, Sync,
custom `action:*`, Cleanup, Release) into named, reusable **flow
templates** executed as **flows** (one per worktree), fully automating
issue handling from start to release. `--then-fix-comments` stays as a
shortcut but is deprecated in docs and rejected inside flows.

## 2. Naming

- `flow_templates` (definitions) + `flows` (runs). `flow_chain`
  rejected: chain describes the steps JSON, not the entity.
- **Steps** (`steps: [{kind, action_label?, config?}]`),
  `kind ∈ start|review|fix_comments|sync|action|cleanup|release`.
  Only `action` steps carry `action_label` (live ref into
  `custom_actions`, validated at template-save + flow-start).
- Example template: `label: e2e`, `name: "End to End"`.

## 3. Schema (`store_flows.py` + `store.py` facade + `store_sqlite.py` DDL)

- `flow_templates(id, label UNIQUE, name, steps JSON, pre_execution,
  post_execution, created_at, modified_at)`. Label immutable,
  `normalize_label(name)` default — same semantics as custom actions.
- `flows(id, template_label, worktree_ref FK→worktrees.ref_key
  ON DELETE CASCADE, repo_snapshot JSON {repo, default_branch,
  release_branch}, status ∈ pending|running|paused|failed|done,
  current_step INT, step_results JSON [{kind, status, detail}],
  created_at, modified_at)`.
- Step `config` holds per-step default flags, overridable per run
  wholesale (template default + flow-run override, like
  `custom_action.default_options`).
- List queries filter soft-deleted worktrees exactly like sessions;
  7-day GC cascades via the FK. `link undelete` restores visibility.

## 4. Validation (`validate_template(steps)`, pure; save-time + run-time)

Non-empty; Start iff first; Release iff last AND requires a prior
Cleanup; Review→…→Cleanup requires Fix-comments between Review and
Cleanup; `action` steps only between Start and Cleanup; `action_label`
must exist; `--then-fix-comments` rejected; cleanup config fixed to
squash + soft-delete (`--hard-delete`/`--no-squash` rejected in
templates).

## 5. Execution (`cli_flow.py` owns logic; `flow run <template> <ref>`)

1. Resolve ref: issue → Start path (creates worktree); worktree →
   adopt (skip Start if live). Deactivated/invalid → exit 2.
2. After step 0, persist `worktree_ref` + `repo_snapshot`; Release
   reads the snapshot, never the (possibly cleaned) link.
3. Loop from `current_step`, dispatching each step to its existing
   runner in-process with merged flags. Each step mints normal
   session + run rows tagged `flow_id` in session metadata.
4. Harness lock per-step (acquire/release), never held end-to-end.
5. Failure → `status=failed`, persist cursor; re-run resumes after
   last green step. Preview/terminal step → `status=paused` + manual
   command; re-run continues.
6. Release dedup: per-repo lock; losers record
   `skipped:release-locked` (not a failure) — one ship per repo.
7. Template pre/post scripts via `run_repo_command` in the worktree;
   pre-fail aborts before step 0, post-fail marks flow failed.
8. Dashboard fans out one `flow run` per selected key (no `--all`
   flag); bulk ops skip deactivated (existing invariant).

## 6. CLI + web

- `flow template add/list/show/update/delete` (label immutable) +
  `flow run <template> <ref> [--step-config JSON] [--dry-run] [--yes]
  [--json]` + `flow list [--json]` + `flow show <id>`.
  `--dry-run` prints the resolved chain without executing.
- Web mirror: `web_args.py` SPECS/BOOL_FLAGS/VAL_FLAGS +
  `SUBCOMMANDS["flow"]`; `GET /api/flow-templates`,
  `GET /api/flows?worktree=`; contract + inventory docs; parity test
  green in the same PR. Mutations run as `python -m workagent` children.

## 7. UI (single `ChainEditor`, two modes)

- Run-launch wizard (template picker → resolved chain → per-step
  override editors → confirm) shared by the Dashboard `Flows` menu
  (scoped to N selected keys, fans out on confirm) and the Launch
  page (scoped to an issue ref). Neither fires with defaults
  directly; CLI `--yes` covers headless fire.
- Template-edit mode adds step add/remove/drag-reorder + per-step
  config forms (used by the `FlowTemplates` CRUD page).
- `Flows` page: runs table (relative timestamps + tooltip, status
  badge) + detail with step timeline and session/run link-outs.
  Worktree filter shortcuts (`?worktree=`) in Flow detail +
  WorktreeDetails card; nav entries for both pages.

## 8. Edge cases

- Start on existing worktree adopts (idempotent, matches direct
  re-runs); Start-mid-flow updates the flow's `worktree_ref`.
- Preview steps pause; terminal steps pause with the OS-terminal
  command printed. `paused ≠ failed`.
- Release-after-cleanup works via snapshot even post-GC of the link
  row for the repo path (snapshot is in the flow row).
- Undelete restores flow visibility; GC purges flows with sessions.
- Multi-worktree same-repo Release: first wins, rest skip.
- Custom action edited/deleted after flow start: running flows
  continue on already-dispatched steps; `flow run` validates all
  labels up front, so a deleted label fails fast before step 0.

## 9. Testing

- `validate_template` unit matrix (each ordering rule + flag
  rejection); store CRUD round-trip; `flow run --dry-run` chain
  resolution; resume cursor (fail step 2, re-run starts at 2);
  release-lock skip; soft-delete hide + GC cascade; mirror test;
  `tsc` + `npm run build`; CLI smokes on a scratch template.
