# workagent — Web feature inventory

Ground truth for `workagent serve`: every command/subcommand/option, its
class (read-only vs mutating), the in-process core function a read-only
API endpoint reuses, the concurrency target key for mutating runs,
destructiveness, and where the CLI can exit with code 2 or prompt on
stdin. Byte-exact help snapshots were captured before any change at
`/tmp/workagent-help-before/`.

Global options (on every subcommand invocation): `--version`, `-v/--verbose`,
`-q/--quiet`. The web server passes `-v` (Settings toggle) **before** the
subcommand. `-q` is deliberately not exposed in the UI (prompt exclusion).

| Command | Class | Core function (read-only reuse) | Target key | Destructive | Exit-2 sites | stdin prompts |
|---|---|---|---|---|---|---|
| `start REF` | mutating | — | `start` (per server) | no (spawns agent) | `refs.parse_ref` failure; no linked repo + non-TTY; tracker linked to 2+ repos without `--repo` (#29, exit 2 naming candidates); missing git-wt | repo picker (TTY); tracker y/N |
| `start --repo NAME` | mutating | — | `start` | no | as above | tracker y/N when repo unlinked (TTY) |
| `start --depth N` | mutating | — | `start` | no | | |
| `start --base BR` | mutating | — | `start` | no | unknown branch → git-wt error (exit 1) | repo-on-X note (TTY, no `--yes`) |
| `start --harness NAME` | mutating | `<HarnessPicker>` (Launch, StartForm) | `start` | no | unsupported harness name | default `agy` (`agy-guard`); supports `agy`, `omp` |
| `start/review/sync/action/prodtest/flow --acp/--model/--effort` + `GET /api/harnesses/{name}/options` + `POST /api/runs/{id}/input` (issue #112) | mutating | `<RunModeSelect>` (4 modes) + `<HarnessPicker>` (ACP discovery, 10-min TTL) + `<AcpTranscript>`/`<QuestionCard>` (RunDetail) | per-command | no (spawns agent) | unknown harness (404 options); probe failure → `warning` + disabled model/effort rows | `--acp` runs via ACP stdio (`AcpHarness.launch_acp`); questions surface as `pending_question`, answered via `input.jsonl` |
| `start --no-tty` | mutating | — | `start` | no | | none — headless mode (`agy-guard --dangerously-skip-permissions --output-format stream-json -p=<prompt>` or `omp -p --auto-approve`, `backend.command_argv(no_tty=True)`); the web UI sends it only with `--launch` (Run agent now), so preview `harness_command` stays interactive (`agy-guard -i=<prompt>` or `omp`) |
| `start --launch` (default: preview) | mutating | — | `start` | no | | without `--launch` lands in interactive shell with the printed command — **UI must not use `--launch`** (needs a TTY shell) |
| `start --terminal` (XOR `--launch`) | mutating | — | `start` | no (detached terminal) | no graphical session (no `$DISPLAY`/`$WAYLAND_DISPLAY`); no terminal emulator found (set `$TERMINAL`) | persists a preview run row with its transcript path, claims the harness lock with `origin=terminal` (recorded pid = spawned terminal emulator), prints `terminal_pid` in `--json`; stop from the Dashboard via `POST /api/terminal/stop` (Task 5) |
| `start --related-repo NAME` (repeatable) | mutating | Related repos multi-select | `start` | no (child worktrees) | same-repo-as-main (2); PR/MR mode (2) | one child worktree per repo on the same branch, grouped under the parent in StatusTable; cleanup cascades |
| `start --dry-run` | read-only-ish | — | `start` | no | | none |
| `start --yes` | mutating | — | `start` | no | | skips confirmations |
| `start --json` | mutating | — | `start` | no | | |
| `start --plan` | mutating | Implement/Plan segmented toggle (Launch Start form + candidate Start dialog; persisted in `launch.prefs.v1`) | `start` | no | | plan-mode prompt (no implementation, brainstorm via the `brainstorming` skill, write spec+plan MDs, push + open PR/MR, stop); without `--plan` the prompt names existing spec/plan files; flow `start` steps take `plan: true` (ChainEditor checkbox, headless `_pick` + terminal/manual `START_FLAGS`) |
| `review REF` | mutating | — | `review` | no | parse failure; not a PR/MR ref and no discoverable PR; tracker linked to 2+ repos without `--repo` (#29, exit 2 naming candidates) | repo picker (TTY) |
| `review --repo/--depth/--harness/--no-tty/--launch/--terminal/--dry-run/--yes/--json` | mutating | `<HarnessPicker>` (Launch Review tab, Dashboard Review-all/Fix-all confirms) | `review` | no | as above | as above (`--terminal` XOR `--launch`, same spawn/lock semantics as `start --terminal`) |
| `review --then-fix-comments` | mutating | — | `review` | no | with `--fix-comments`/`--all` (2) | chains a fix run after review exit 0 (headless auto; preview/terminal print follow-up); **deprecated: use flows; ignored inside flow templates** |
| `cleanup --merged [--force/--dry-run/--json]` | mutating | — | `cleanup:all` | **yes** (per merged/closed link) | `--merged` w/o `--yes` non-interactive (2); ref given with `--merged` (2) | none — requires `--yes`; skips live-harness/invalid worktrees |
| `cleanup --released [--yes/--dry-run/--json]` (+ optional ref) | mutating | — | `cleanup:all` (no ref) else `cleanup:<ref>` | **yes** (closes tracker issue only; worktrees/branches/PRs untouched) | `--released` w/o `--yes` non-interactive (2) | closes `done` for links whose branch tip is ancestor of `origin/<release_branch>`; skips deactivated/no-release-branch/unreleased |
| `open REF` | read-only-ish (OS opener) | — | `open:<ref>` | no | no linked state (2), invalid/missing worktree (1), no opener (1) | none — prints the worktree path |
| `cleanup REF` | mutating | — | `cleanup:<ref>` | **yes** (closes/merges PR then soft-deletes: worktree/branch/remote survive, 7-day `link undelete` window; `--hard-delete` tears down immediately; modal shows live outcome table + hard-delete checkbox) | no linked state; conflicted/404 PR keeps remote + link (1) | none (non-TTY) / y-N (TTY) |
| `cleanup --force` | mutating | — | `cleanup:<ref>` | **yes** | | skips state validation |
| `cleanup --yes` | mutating | — | `cleanup:<ref>` | **yes** | | skips confirmation |
| `cleanup --dry-run` | read-only-ish | — | `cleanup:<ref>` | no | no linked state | none |
| `cleanup --json` | mutating | — | `cleanup:<ref>` | **yes** | | |
| `cleanup --hard-delete` | mutating | — | `cleanup:<ref>` | **yes** (immediate full teardown, no recovery) | | skips the 7-day soft-delete window |
| `status [REF]` | read-only | `_resolve_session_key` + `_enrich_entry` / `_session_detail` (`cli.py`) | — | no | unknown ref without linked state | none |
| `status --worktree` | read-only | same (`_session_rows(show_worktree=True)`) | — | no | | |
| `status --refresh-pr` | read-only (network) | `_enrich_entry(entry, refresh=True)` | — | no | | |
| `status --json/--csv` | read-only | same | — | no | | |
| `cd REF` | read-only | `_resolve_session_key` + links | — | no | unknown ref (2), missing worktree (1) | none |
| `sync [REF] [--no-auto-register]` | mutating | `<HarnessPicker>` (Dashboard Sync confirm) | `sync:<ref>` | yes-ish (rebase/merge + push) | ref resolution; dirty worktree (1); `--no-auto-register` w/o link (2) | conflict: TTY confirm; `--harness`/`--yes` run selected/default harness; unlinked PR/MR auto-registers unless `--no-auto-register` |
| `sync -m/--merge` / `--rebase` | mutating | `<SyncActionSelect>` (Launch Sync tab), Sync method select (`ChainEditor`), `<BulkConfirmExtras>` | `sync:<ref>` | yes-ish | | `--rebase` is default; mutually exclusive with `--merge` |
| `sync --harness NAME` | mutating | `<HarnessPicker>` (Dashboard Sync / Sync-all confirms, Launch Sync tab) | `sync:<ref>` | yes-ish | unsupported harness name | launches selected harness on conflicts (default: configured `default_harness`) |
| `sync --all` | mutating | — | `sync:all` | yes-ish | | implies `--yes` |
| `sync --yes/--force/-y` | mutating | — | `sync:<ref>` | yes-ish | | runs configured/selected harness headless on conflicts |
| `sync --dry-run` | read-only-ish | — | `sync:<ref>` | no | | none |
| `sync --json` | mutating | — | `sync:<ref>` | yes-ish | | |
| `prodtest plan REF [--update] [--harness/--launch/--terminal/--no-tty/--dry-run/--yes/--json] [--session-file/--env-file/--extra-prompt]` | mutating | `<HarnessPicker>` (ProdtestLaunch) | `prodtest:<ref>` | no (spawns agent) | no linked state (2), launch+terminal (2) | TTY confirm; non-TTY needs `--yes`; issue-key first, worktree ref as accelerator; `running` status on lock acquire; `worktree_ref` persisted |
| `prodtest run REF [--fix-mode report-only\|issue\|issue+mr] [--skip-version-gate] [--run-dir] [--launch/--terminal/--no-tty/--dry-run/--yes/--json] [--harness/--session-file/--env-file/--extra-prompt]` / `prodtest run --all` | mutating | `<HarnessPicker>` (ProdtestLaunch) | `prodtest:<ref>` / `prodtest:run-all` | yes (ephemeral checkout run) | no release recorded (2), bad fix-mode (2), held lock (1), ref with --all (2), --run-dir/--session-file with --all (2) | TTY confirm; non-TTY needs `--yes`; per-issue lock + timeout kill; `--all` runs eligible keys sequentially in-process with `skipped:<reason>` rows, continuing past failures; verdict persisted to `run_results` JSON |
| `prodtest delete REF [--json]` | mutating | — | `config` | yes (row delete) | unknown issue key (2) | Prodtests page per-row Delete |
| `prodtest cleanup [--yes] [--dry-run] [--json]` | mutating | — | `prodtest:cleanup` | yes (row deletes) | | Prodtests page Cleanup-passed button; drops every `passed` row; non-TTY needs `--yes` (2) |
| `adhoc REF… --prompt TEXT [--launch/--terminal/--acp] [--harness/--model/--effort] [--extra-prompt] [--env-file] [--session-file] [--continue-run ID] [--follow-up] [--dry-run] [--yes] [--json] [--no-auto-register]` | mutating | `<HarnessPicker>` + `<RunModeSelect>` (Launch Adhoc tab, MessageRowActions adhoc, WorktreeWorkspace Follow-up Composer) | `adhoc:<first-ref>` | no (spawns agent) | no linked state (2), blank prompt (2), launch+terminal+acp XOR (2) | TTY confirm (single prompt when auto-registering PR/MR); non-TTY needs `--yes`; `--continue-run`/`--follow-up` resumes ACP session (`session/load` or transcript-context fallback) and links `continued_from_run_id` |
| `orchestrate [--repo R…] --prompt TEXT [--acp/--launch/--terminal] [--harness/--run-mode/--model/--effort] [--extra-prompt] [--env-file] [--session-file] [--continue-run ID] [--dry-run] [--yes] [--json]` | mutating | `SpawnedWorkersCard` inside `AcpTranscript` / `WorktreeWorkspace` / `RunDetail` | `orchestrate:<repos>:<prompt-hash>` | no (spawns coordinator agent) | blank prompt (2), launch+terminal+acp XOR (2) | Defaults to ACP mode; sets `WORKAGENT_PARENT_RUN_ID` so spawned worker runs link back to the orchestrator run and appear in `spawned_runs` |
| `repo add --name --path [--tracker] [--fetch/--no-fetch] [--release-branch] [--pre-release] [--json]` | mutating | — | `repo:add` | no (config write) | path missing | none |
| `repo list [--json/--csv]` | read-only | `store.load_config()["repos"]` | — | no | | none |
| `repo pull NAME [--json]` | mutating | — | `repo:pull:<name>` | no (`fetch --prune` + `merge --ff-only origin/<branch>`; `switched_from` set when auto-checked-out to default) | unknown repo (2), dirty checkout (1), detached HEAD (1), `remote branch origin/<branch> not found` (1), diverged ff-only failure (1) | ReleaseRow Pull button (hidden when `has_remote_diff` is false; disabled with `(!)` tooltip when local repo has uncommitted changes; toast names the old branch on auto-switch; non-origin tracking remote kept with a stderr warning) |
| `repo remove NAME [--json]` | mutating | — | `repo:remove` | yes-ish (registry write) | unknown repo (2) | none |
| `repo update NAME [--default-branch] [--release-branch] [--pre-release] [--overlay-buttons ids] [--ci-ignore globs] [--json]` | mutating | — | — | no (config write) | unknown repo (2), unknown overlay id (2) | none — `--overlay-buttons "a,-b,flow:x"` merges visibility (empty resets); `--ci-ignore "trivy:*,security/*"` sets side-CI ignore globs (empty clears); Repos page per-row Overlay and CI ignore editors; flow/action renames retarget `flow:/action:` keys (explicit target wins) and deletes prune them |
| `release REPO [--checks ci\|local\|both\|none] [--diff remote\|local] [--pre-release-timeout 10m] [--ci-timeout 15m] [--env-file script] [--harness NAME] [--yes] [--dry-run] [--json]` | mutating | — | `release:<repo>` | yes (pinned-SHA MR merge) | bad checks/diff (2), diverged behind>0 (1), CI fail/timeout (1) | TTY confirm; non-TTY needs `--yes` (2); `--env-file` sourced before the pre-release gate (default: configured `env.sh`) |
| `release fix-default REPO [--action rebase\|ci\|both] [--run-mode preview\|headless\|terminal] [--harness NAME] [--extra-prompt text] [--dry-run] [--json] [--yes]` | mutating | `plan_fix_default` + `<HarnessPicker>` (ReleaseCard `FixDefaultDialog`) | `release:<repo>` | yes-ish (force-push rewrite of default on rebase) | bad action/run-mode (2), unknown/missing branches (2) | no-op exit 0 when behind=0 and neither branch CI failing; non-TTY needs `--yes` for headless/terminal; ReleaseCard "Fix default" button (visible when `behind > 0 \|\| ci === "failure"`) → dialog with action/run-mode/`<HarnessPicker>`/extra-prompt → `createRun` |
| `link set TRACKER REPO [--json]` | mutating | — | `link:set` | no (config write) | resolve failure | none |
| `link remove REF [--repo] [--json] [--hard-delete]` | mutating | — | `link:remove` | yes-ish (soft-deletes link, 7-day `link undelete` window; `--hard-delete` drops immediately) | repo not linked (2) | none |
| `link undelete REF [--json]` | mutating | — | `link:undelete` | no (clears deleted_at) | no worktree link (2) | none |
| `link gc [--dry-run] [--json]` | mutating | — | `link:gc` | no (purges rows soft-deleted >7d: worktree dir + local branch + row) | | none |
| `link destroy-deleted [REF] [--all] [--dry-run] [--json] [--yes]` | mutating | `destroy_deleted` (git-wt cleanup + branch -D + row drop) | `link:destroy-deleted` | **yes** (worktree dir + local branch + row and its runs; remote branches untouched; irreversible) | no link/no ref (2) | none — Deleted table per-row trash icon + Destroy (N) selection button, irreversible custom confirm dialog |
| `link purge-orphans [--dry-run] [--json]` | mutating | `purge_orphans` (dangling runs/flows/messages/parents + harness transcripts) | `link:purge-orphans` | no (removes records whose worktree row is gone; prodtest ledger rows keep their ref) | | none — also runs in serve startup maintenance |
| `link list [--worktree] [--refresh-pr] [--json/--csv] [--include-deleted]` | read-only | `store.load_config()["trackers"]` + `_enrich_entry` | — | no | | none |
| `tracker add KEY [--vendor] [--remote-url] [--fetch/--no-fetch] [--json]` | mutating | — | `config` | no (trackers table write) | `--vendor` outside the jira/github enum (2) | none — vendor/URL derive from the key when blank |
| `tracker list [--json/--csv]` | read-only | `store_sqlite.load_tracker_rows` | — | no | | none |
| `tracker remove KEY [--json]` | mutating | — | `config` | yes-ish (row delete, links cascade) | unknown tracker (2) | none |
| `action add --name NAME [--label] [--icon] [--prompt] [--pre-execution] [--post-execution] [--default-options JSON] [--protected/--no-protected] [--sort-order N] [--dedicated-top-bar/--no-...] [--dedicated-overlay/--no-...] [--category worktree\|message] [--yes] [--json]` | mutating | — | `config` | no (custom_actions row write; `--yes` accepted for API/frontend parity, no-op) | blank name (2), blank prompt+pre (2), bad label/JSON (2), label collision (2), bad category (2) | none |
| `action list [--json/--csv]` | read-only | `store.list_actions` | — | no | | none |
| `action show LABEL [--json]` | read-only | `store.get_action` | — | no | unknown label (2) | none |
| `action update LABEL [--name] [--icon] [--prompt] [--pre-execution] [--post-execution] [--default-options JSON] [--protected/--no-protected] [--sort-order N] [--dedicated-top-bar/--no-...] [--dedicated-overlay/--no-...] [--new-label] [--yes] [--json]` | mutating | — | `config` | no (custom_actions row write; `--yes` accepted for API/frontend parity, no-op) | unknown label (2), no fields (2), blank name / blank prompt+pre (2), bad new-label / new-label collision (2) | none — `--new-label` renames (`^[a-z0-9_]+$`, collisions rejected); flow-template steps referencing it are rewritten |
| `action run LABEL REF [--launch/--terminal] [--harness] [--extra-prompt] [--env-file] [--session-file] [--dry-run] [--yes] [--json] [--no-auto-register]` | mutating | `<HarnessPicker>` (ImmediateRunForm, ActionForm default) | `action:<label>:<ref>` | no (spawns agent) | unknown label (2), launch+terminal (2), blank prompt+pre (2), live harness (1) | TTY confirm (single prompt when auto-registering PR/MR); non-TTY needs `--yes` (2) |
| `action delete LABEL [--yes] [--json]` | mutating | — | `config` | yes-ish (row delete) | unknown label (2) | TTY confirm; non-TTY needs `--yes` (2) |
| `action suggest-icon [--name] [--label] [--prompt] [--pre-execution] [--post-execution] [--used-icons] [--harness] [--yes] [--json]` | read-only-ish | ONE bare judge-style harness session (temp dir, no checkout) | `suggest-icon:action` | no | | none — never confirmed; prints `{"icon": "Name"}` validated against full Lucide `SUGGEST_ICON_CHOICES` (1611); prompt lists all choices + already-used icons (explicit `--used-icons` unioned with the store-collected fallback) |
| `flow template add --name NAME [--label] [--icon] --steps JSON [--pre-execution] [--post-execution] [--harness] [--model] [--effort] [--protected/--no-protected] [--sort-order N] [--dedicated-top-bar/--no-...] [--dedicated-overlay/--no-...] [--category worktree\|message] [--yes] [--json]` | mutating | — | `config` | no (flow_templates row write; `--yes` accepted for API/frontend parity, no-op) | missing steps (2), bad steps JSON / unknown kind (2), label collision (2) | steps: `start\|review\|fix_comments\|sync\|action(action_label)\|prodtest_plan\|cleanup(merge_only)\|release` + `execution_mode` bulk buttons/summary/badges (editor, wizard fan-out line, templates Mode column + detail badges, run detail badges); `--icon` defaults `Zap`; form offers the AI Suggest-icon button; none — validation mirrors backend |
| `flow template list [--json/--csv]` | read-only | `store.list_templates` | — | no | | none |
| `flow template show LABEL [--json]` | read-only | `store.get_template` | — | no | unknown label (2) | none |
| `flow template update LABEL [--name] [--icon] [--steps JSON] [--pre-execution] [--post-execution] [--harness] [--model] [--effort] [--protected/--no-protected] [--sort-order N] [--dedicated-top-bar/--no-...] [--dedicated-overlay/--no-...] [--new-label] [--yes] [--json]` | mutating | — | `config` | no (flow_templates row write; `--yes` accepted for API/frontend parity, no-op) | unknown label (2), no fields (2), bad steps JSON (2), bad new-label / new-label collision (2) | none — `--new-label` renames (`^[a-z0-9_]+$`, collisions rejected); `flows.template_label` history rows retargeted |
| `flow template suggest-icon [--name] [--label] [--steps JSON] [--pre-execution] [--post-execution] [--used-icons] [--harness] [--yes] [--json]` | read-only-ish | ONE bare judge-style harness session (temp dir, no checkout) | `suggest-icon:flow` | no | | none — never confirmed; prints `{"icon": "Name"}` validated against full Lucide `SUGGEST_ICON_CHOICES` (1611); prompt lists all choices + already-used icons (explicit `--used-icons` unioned with the store-collected fallback) |
| `flow template clone SRC [--name] [--label] [--yes] [--json]` | mutating | — | `config` | no (flow_templates row write; `--yes` accepted for API/frontend parity, no-op) | unknown label (2), label collision (2) | none — lineage in `parent_labels` |
| `flow template compose LABEL --from A,B [--name] [--yes] [--json]` | mutating | — | `config` | no (flow_templates row write; `--yes` accepted for API/frontend parity, no-op) | unknown label (2), label collision (2), bad steps (2) | none — starts preserved, lineage in `parent_labels` |
| `flow run TEMPLATE REF [--steps JSON] [--step-config JSON] [--skip-steps 0[,N]] [--harness NAME] [--dry-run] [--fresh] [--yes] [--json] [--no-auto-register]` | mutating | `<HarnessPicker>` (FlowWizard, ChainEditor step config, ImmediateRunForm) | `flow:<template>:<ref>` | no (fans out to step runners) | unknown template (2), bad steps/step-config JSON (2), bad/out-of-range/future --skip-steps (2), cleanup overrides (2), resume with different --steps chain (2: --fresh or drop --steps; config-only diffs: --step-config) | TTY confirm; non-TTY needs `--yes` (2); unlinked PR/MR auto-registers unless `--no-auto-register`; wizard omits --steps when unedited, offers Fresh toggle |
| `flow list [--worktree REF] [--json/--csv]` | read-only | `store.list_flows` | — | no | | none |
| `flow show ID [--json]` | read-only | `store.get_flow` | — | no | unknown flow id (2) | none |
| `message add --message-id ID\|PERMALINK [--fetch] [--thread-id T] [--app APP] [--sender S] [--content C] [--ts TS] --repo R... [--related-repo R...] [--worktree W...] [--json]` | mutating | — | `config` | no (messages row write) | zero/unknown repos (2), duplicate message_id+app (2), bad app (2), unknown repos/related/worktrees (2), --fetch miss (2) | none |
| `message list [--thread T] [--json/--csv]` | read-only | `store_messages.list_messages` | — | no | | none |
| `message show REF [--json]` | read-only | `store_messages.resolve_message` | — | no | unknown/ambiguous ref (2) | none |
| `message update REF [--thread-id] [--sender] [--content] [--ts] [--repo R...] [--related-repo R...] [--worktree W...] [--json]` | mutating | — | `config` | no (messages row write) | unknown/ambiguous ref (2), unknown field (2), unknown repos/related/worktrees (2) | none |
| `message remove REF [--yes] [--json]` | mutating | — | `message:<ref>` | yes (row delete) | unknown/ambiguous ref (2) | TTY confirm; non-TTY needs `--yes` (2) |
| `message adhoc MSG --prompt P [--ref] [--repo] [--launch/--terminal/--acp] [--harness/--model/--effort] [--extra-prompt] [--env-file] [--session-file] [--dry-run] [--yes] [--json]` | mutating | `<HarnessPicker>` + `<RunModeSelect>` (MessageLaunch, MessageRowActions adhoc) | `message:<ref>` | no (spawns agent) | unfetchable/unknown msg (2), blank prompt (2), multi-repo auto-register without --repo and without a resolving --ref (2) | TTY confirm; non-TTY needs `--yes` (2); single --repo pin; `--acp` streams structured ACP events to the Run page |
| `message validate MSG [...]` | mutating | `<HarnessPicker>` (MessageLaunch) | `message:<ref>` | no (spawns agent; seeded prompt confirms issues + files Jira) | same as adhoc | TTY confirm; non-TTY needs `--yes` (2) |
| `message draft-reply MSG [--ref] [--repo] [--extra-prompt] [--launch/--terminal] [--harness] [--env-file] [--session-file] [--dry-run] [--yes] [--json]` | mutating | `<HarnessPicker>` (MessageLaunch) | `message:<ref>` | no (spawns agent; skill prompt mm-reply-drafter + persian-chat-generator — facts-only, never sends) | unfetchable/unknown msg (2), multi-repo without --repo/--ref (2) | Message Launch Draft-reply tab; ZWNJ-free ≤160-char output-only draft |
| `message action run LABEL MSG [--ref] [--repo] [--harness] [...] [--dry-run] [--yes] [--json]` | mutating | `<HarnessPicker>` (MessageLaunch) | `message:action:<ref>` | yes (spawns agent) | worktree-category action (2), repo path without linked worktree (2) | TTY confirm; non-TTY needs `--yes` (2); single --repo pin |
| `message flow run LABEL MSG --ref WT [--repo] [--harness NAME] [--dry-run] [--yes] [--json]` | mutating | `<HarnessPicker>` (MessageLaunch) | `message:flow:<ref>` | yes (fans out to step runners) | worktree-category template (2), missing --ref (2) | TTY confirm; non-TTY needs `--yes` (2) |
| `config show [--json]` | read-only | `GET /api/config` (`store.default_harness()`, `default_model()`, `default_effort()` + `backend.HARNESSES`) | `config` | no | | Settings "Default AI Harness" card reads `GET /api/config` |
| `config set KEY VALUE [--json]` | mutating | Settings "Default AI Harness" card (`<HarnessPicker allowDefault={false}>`) | `config` | no (config.json write) | unknown key or unsupported harness (2) | persists `default_harness` (`agy` \| `omp`), `default_model`, or `default_effort` in `config.json` |
| `register PATH [--key] [--issue] [--repo] [--yes/--force] [--json]` | mutating | — | `register` | no (links.json write) | bad path/main checkout (2), key conflict (1) | none |
| `doctor [--json]` | read-only | `doctor.check(json_output=True)` returns dict | — | no | exit 1 when stale | none |
| `completions show/install` | mutating (writes rc) | — | — | — | — | **excluded from UI per prompt** |
| `workagent serve` | — | this feature | — | — | port busy (1), missing web/dist (1) | none |

## Transcripts on runs

Every harness launch is a run that optionally carries its AI transcript: the
Runs page owns the per-run kind label (read from the run's stored
`session_type`: start / review / fix comments / sync / custom action / flow /
prodtest), the transcript path, and the resume/copy buttons (`RunResumeActions`
in `RunActions.tsx`, using `run.resume_command` or a `agy-guard --conversation` /
`omp --resume` fallback). Resume is offered whenever a path is stored (for
`agy`, adopts `conversation_id` and mirrors `brain/<cid>/.../transcript.jsonl`
into `runs/agy/….jsonl`; for `omp`, resolves the minted `.jsonl`); copy only
when `transcript_ready` is true (a preview row's path is the scope hint). There
is no separate Sessions page or surface — old `/sessions` links land on Runs.

## AI harnesses (`agy-guard` & `omp`)

- `DEFAULT_HARNESS = "agy"` (`AgyHarness` via `agy-guard` binary; `OmpHarness` via `omp`).
- `GET /api/config` supplies `default_harness`, `supported_harnesses`, and per-harness availability to `<HarnessPicker>`.
- `<HarnessPicker>` appears across Settings (Default AI Harness card, `allowDefault={false}` → `config set default-harness`), Launch (`StartForm` / Review / Sync / Adhoc tabs), MessageLaunch, ProdtestLaunch, ActionForm (default options), ImmediateRunForm, ChainEditor (per-step config), FlowWizard (flow override), Dashboard bulk confirms (Sync / Sync-all / Review-all / Fix-all), ReleaseCard `FixDefaultDialog`, and WorktreeDetail terminal picker (`POST /api/worktrees/{ref}/terminal` optional `harness` body param).

## Headless agent mechanism (reused, not reimplemented)

- File: `src/workagent/backend.py` (`Harness` ABC + `OmpHarness`) and `src/workagent/agy_harness.py` (`AgyHarness`) → `command_argv(harness, prompt, no_tty=True)` builds `agy-guard --dangerously-skip-permissions --output-format stream-json [extra] -p=<prompt>` for `agy` or `omp -p --auto-approve [extra] "<prompt>"` for `omp`.
- CLI surface: `workagent start <ref> --no-tty` (and `review ... --no-tty`)
  launch the agent headless with auto-approve; `--no-tty` also appends the
  "commit, push and create an MR/PR" suffix for `start`.
- The web server therefore runs `workagent start <ref> [--repo ...] --no-tty`
  as a child process of the same code version. It never re-derives the argv.

## Exit-code → run state mapping

| Exit | State |
|---|---|
| 0 | `succeeded` |
| 1 | `failed` |
| 2 | `needs_input` |
| SIGTERM/cancel | `cancelled` |
| any other non-zero (incl. signals) | `failed` |

## Exit-2 → UI flow

`needs_input` runs show the captured stderr message and offer **Re-run with
prefilled arguments** (same command/args, new run). Typical exit-2 causes:
unparseable ref, no linked state, unknown repo, non-TTY without required
flags, `--force`-required conflicts (register), repo-picker aborts.

## Concurrency target keys (409 on collision)

- `start` — one agent launch at a time (also protects git-wt index writes).
- `review:<ref>` — one review per PR ref; `review:all` for bulk.
- `prodtest:<ref>` — one prodtest plan/run per issue ref; `prodtest:run-all` for `run --all` (single sequential bulk run in-process).
- `adhoc:<first-ref>` — one adhoc run per first ref.
- `cleanup:<normalized ref>` — one cleanup per worktree ref; `--merged`
  keys as `cleanup:all` (one bulk sweep at a time).
- Dashboard toolbar acts on the `?sel=` selection when non-empty (N sequential
  per-key runs under one confirm), else `--all`/`--merged`; `review --all`
- `open:<ref>` — one opener spawn per ref (harmless, but keeps the
  registry consistent).
- `register`, `repo:add`, `repo:remove`, `link:set`, `link:remove` — config
  writes; single global key `config` to avoid lost updates.

## Destructive commands requiring `confirm: true` at the API

- `prodtest` (`plan`/`run`) — require `confirm: true` (the Prodtests page + Prodtest Launch page confirm before spawning runs; `list`/`status` are read-only GETs, no confirm).
- `adhoc` — spawns an agent; requires `confirm: true`.
- `repo remove` (registry write)
- `link remove` (drops tracker relation or worktree link)
- `sync` (without `--dry-run`) — mutates branches (server rebase rewrites
  remote history / local merge + push)
- `start`/`review` launch auto-approving agents — require `confirm: true`
  (the Launch dialog states the agent runs headless with auto-approve).

`--yes` semantics verified from source: `cleanup --yes` skips its
confirmation prompts; `sync --yes` auto-launches the selected/default harness on conflicts
(non-TTY `agy-guard` or `omp -p --auto-approve`); `register --yes/--force` overwrites an
existing link; `start/review --yes` skip repo-picker confirmations. The
API passes `--yes` only when the request carries `confirm: true`
(`force: true` additionally passes `--force` for cleanup/register, whose
`--force` skips state validation / link overwrite — verified in
`cleanup_cmd` and `link_wt`).

### Background PR & CI Auto-Watchers (T11 / #154)
- CLI: `workagent repo update <name> --auto-fix-ci/--no-auto-fix-ci --auto-fix-reviews/--no-auto-fix-reviews`; `workagent watch [--once] [--dry-run] [--interval N] [--repo R] [--json]`.
- Web: `Repos.tsx` (`Auto-fix CI` / `Auto-fix reviews` toggles); `WorktreeDetail.tsx` & `WorktreeWorkspace.tsx` (`WorktreeWatcherOverrides` per-worktree Inherit/On/Off overrides); opt-in `webapp.py` lifespan background watcher (`WORKAGENT_WATCH=1`).

## Recurring Schedules (T13)
- `schedule` (`add|list|update|remove|run-due`) — `run-due` requires `confirm: true` (`schedule:run-due` target key); CRUD endpoints live under `/api/schedules` and the `Schedules` tab of Automation Library (`/library`).

- Cache controls (#171): `GET /api/candidates/prs?force=true` and `GET /api/release/status?name=<repo>&force=true` (manual Refresh buttons in `CandidatesCard` and `ReleaseCard`); `workagent candidates --reset-cache` clears both `issue_cache` and `pr_candidates_cache`; expanded CDC SSE triggers (`pr_candidates_cache`, `issue_cache`, `release_diff_cache`, `repos`, `trackers`, `pr_checks`, `pr_review_threads`).
