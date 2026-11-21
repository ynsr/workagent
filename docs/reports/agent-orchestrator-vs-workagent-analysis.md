# Agent Orchestrator vs. Workagent: Architecture, Data Modeling & UI/UX Analysis

**Issue:** [ynsr/workagent#126](https://github.com/ynsr/workagent/issues/126)  
**Date:** 2026-11-21  
**Repositories Compared:**
- **`OrchestratorInc/agent-orchestrator`** (`/home/bs/projects/github/agent-orchestrator`): Go daemon (`ao`) + Electron/React 19 desktop supervisor + Expo/React Native mobile companion.
- **`ynsr/workagent`** (`/home/bs/dev/worktrees/workagent/feat/126--analyze-agent-orchestrator-vs-workagent-uiux-comparison-impr`): Python Typer/Rich CLI + FastAPI (`workagent serve`) + React/TypeScript web application (reconciled against `main` at `bdd1809`, including the ACP harness architecture from [PR #123 / Issue #112](https://github.com/ynsr/workagent/pull/123), ACP fixes & transcript/UI alignment from [PR #129 / Issue #128](https://github.com/ynsr/workagent/pull/129), OMP ACP discovery & cross-harness cascade fixes from [PR #131 / Issue #130](https://github.com/ynsr/workagent/pull/131), and fresh flow retry on differing `--steps` from [PR #133 / Issue #132](https://github.com/ynsr/workagent/pull/133)).

---

## 1. Executive Summary & Core Philosophy Comparison

Both **Agent Orchestrator (AO)** and **Workagent** solve the problem of coordinating parallel AI coding agents across isolated Git worktrees and linking their output to Pull/Merge Requests and CI pipelines. However, they approach agent orchestration from fundamentally different paradigms:

| Dimension | `OrchestratorInc/agent-orchestrator` (AO) | `ynsr/workagent` |
|---|---|---|
| **Primary Mental Model** | **Session- & Conversation-Centric Supervision:** Each task is a persistent *Session* (worker or project-level *Orchestrator* meta-agent) with a live Chat timeline and/or interactive Terminal (PTY/tmux), plus a continuous background SCM feedback loop. | **Worktree- & Pipeline-Centric Operations:** Each unit of work is a *Linked Worktree* (`issue ↔ worktree ↔ PR`) driven through discrete, composable CLI/Web *Runs* and deterministic *Flow Templates* (`Start → Review → Fix → Sync → Prodtest → Cleanup → Release`). |
| **Orchestration Paradigm** | **Conversational Meta-Agent (`kind=orchestrator`):** A project-level AI agent chats with the user, explores the repo, decomposes goals into parallel subtasks, spawns child worker sessions (`ao spawn`) in separate worktrees/harnesses, and receives batched worker reports (`<ao-worker-reports>`). | **Declarative Step Chains (`flow_templates` & `flows`):** The human operator defines repeatable multi-step pipelines (sequential or parallel across worktrees) with pre/post shell hooks, custom actions, and automated headless/ACP/terminal execution modes. |
| **Feedback Loop Automation** | **Continuous Daemon Observer Loop:** Background `SCMObserver` (polls GitHub/GitLab every 30s with ETags + semantic hashes) and `RuntimeReaper` (every 5s) automatically inject failing CI checks, review comments, and merge conflicts back into the live worker session (`AutoInjectCI`, `AutoInjectReview`, `AutoReview`). | **Explicit / Flow-Driven Invocations:** Status, CI, and review counts are fetched with stale-while-revalidate caching (`pr_cache`) on demand or via 15s dashboard polling; review/fix/sync cycles are triggered explicitly by the operator, bulk actions, or a running Flow (`review_fix`, `workagent_start_to_cleanup`). |
| **Enterprise / Post-Merge Scope** | Focused on **First-Commit-to-Merge** delivery (`Building → Validating → In review → Ready to merge`), plus recurring RRULE **Automations**, versioned **Artifacts**, **Browser CDP Preview**, and **Usage/Cost Telemetry**. | Extends **Beyond Merge into Production & Chat Ops**: native **Jira** status state-machine transitions, **Mattermost** chat message ingestion/validation/reply drafting (`messages`), **Release Branch** gates (`release`, `fix-default`), and post-release **Production Verification** (`prodtest_items`). |
| **Supported Harnesses** | 23+ worker/chat/reviewer adapters (Claude Code, Codex, Cursor, Gemini/Agy, OMP, OpenCode, Qwen, Copilot, Pi, Aider, Cline, Devin, etc.) with capability registries and TUI↔Chat live controller handoff. | Focused 2-harness architecture (`agy` via `agy-guard` and `omp`), unified under `AcpHarness` (PR #123 + PR #129) supporting 4 execution modes (`preview`, `headless`, `acp`, `terminal`) with dynamic model/effort discovery, dual-mode validation, and structured Markdown ACP transcripts. |

---

## 2. Backend Comparison

### 2.1 Data Modeling and Structure in DB

Both systems use **SQLite in WAL mode** as their single local source of truth (`~/.ao/...` in AO; `~/.config/workagent/state.db` in Workagent).

#### A. `agent-orchestrator` SQLite Schema (`backend/internal/storage/sqlite/gen/models.go`)
AO uses **goose migrations** + **sqlc-generated type-safe Go queries** (~55 tables) organized around **Durable Facts vs. Derived Display Status**:

1. **Projects & Multi-Repo Workspaces**:
   - `projects` (`id`, `path`, `repo_origin_url`, `display_name`, `config` JSON, `kind`, `archived_at`)
   - `workspace_repos` (`project_id`, `name`, `relative_path`, `repo_origin_url`, `default_branch`, `git_status`)
   - `session_worktrees` (`session_id`, `repo_name`, `branch`, `base_sha`, `worktree_path`, `preserved_ref`, `state`, `base_ref`, `creation_sha`)
2. **Sessions & Interface Handoffs**:
   - `sessions` (`id`, `project_id`, `num`, `issue_id`, `kind` [`worker`|`orchestrator`], `harness`, `session_mode` [`chat`|`tui`], `activity_state` [`active`|`idle`|`waiting_input`|`blocked`|`exited`], `is_terminated`, `branch`, `workspace_path`, `runtime_handle_id`, `provider_conversation_id`, `controller_generation`, `auto_inject_review`, `auto_inject_ci`, `auto_review_enabled`, `hibernated_at`, `provision_state`, `branch_naming`, `temporary_branch`, etc.)
   - `session_interface_transitions` & `session_interface_transition_messages` (atomic CAS state machine + durable outbox for draining/interrupting a session when switching live between Terminal TUI and Native Chat)
   - `session_forks` (`fork_session_id`, `source_session_id`, `root_session_id`, `source_turn_id`, `context_mode`)
   - `shell_terminals` (`handle_id`, `project_id`, `session_id`, `working_dir`, `title`, `last_agent_harness`)
3. **Structured Conversations, Turns & Activities**:
   - `conversations` (`id`, `scope` [`project`|`session`|`review`], `project_id`, `session_id`, `review_id`, `model`, `reasoning_effort`, `approval_mode`, `context_used`, `context_window`, `usage_*_tokens`, `usage_cost`, `account_json`, `mcp_servers_json`, `active_branch_id`)
   - `conversation_branches` (supports non-destructive turn rollback/edit/forking with `fork_after_turn_id`, `replay_cutoff_sequence`)
   - `conversation_turns` (`id`, `conversation_id`, `handled_by_session_id`, `provider_turn_id`, `controller_generation`, `state`, `diff_json`, `plan_json`, `artifacts_json`, `batched_input`)
   - `conversation_messages` (`id`, `conversation_id`, `turn_id`, `sequence`, `role`, `origin`, `text`, `streaming`, `sender_session_id`, `delivery_content_json`)
   - `conversation_activities` (`id`, `conversation_id`, `turn_id`, `sequence`, `kind` [`tool`|`command`|`file_change`|`approval`|`user_input`|`steer`], `status`, `summary`, `detail_json`, `command_output`, `streamed_text`)
   - `conversation_provider_events` (raw JSON-RPC/ACP archive), `conversation_steer_deliveries`, `conversation_edit_deliveries`
4. **Pull Requests, Granular Checks, Reviews & Automated Review Runs**:
   - `prs` (`url` PK, `session_id` FK, `number`, `pr_state`, `review_decision`, `ci_state`, `mergeability`, `additions`, `deletions`, `changed_files`, `head_sha`, `base_sha`, `metadata_hash`, `ci_hash`, `review_hash`, `last_nudge_signature`, `auto_inject_ci`)
   - `pr_details`, `pr_checks` (per-check `name`, `commit_hash`, `status`, `conclusion`, `url`, `log_tail`), `pr_reviews`, `pr_review_threads` (`thread_id`, `path`, `line`, `resolved`, `semantic_hash`), `pr_comments` (`comment_id`, `author`, `file`, `line`, `body`, `resolved`, `auto_inject_review`)
   - `reviews` & `review_runs` (AO's built-in interactive reviewer agent runs per PR `target_sha`: `status`, `verdict`, `body`, `worker_notice`, `trigger_source`)
5. **Orchestrator Reports, Artifacts, Automations, Notifications & Telemetry**:
   - `reports` & `report_outputs` (structured worker-to-orchestrator reports with `state` [`needs_input`|`stuck`|`done`], claim tokens, delivery batching, and PR/artifact references)
   - `session_artifacts`, `session_artifact_versions` (SHA-256 content-addressed versioning), `kept_artifacts` (survive session teardown)
   - `automations` & `automation_runs` (RRULE/timezone recurring scheduled agent spawns with lease-based execution)
   - `cues` (reusable per-project prompt snippets or shell commands)
   - `notifications` (`id`, `session_id`, `project_id`, `pr_url`, `type` [`needs_input`|`ready_to_merge`|`pr_merged`|`pr_closed_unmerged`], `status`)
   - `agent_accounts`, `agent_limit_readings`, `model_usage_events`, `usage_bindings`, `usage_sources` (token/cost accounting & quota windows)
   - `change_log` (`seq` PK, `project_id`, `session_id`, `event_type`, `payload`, `created_at` — populated automatically by **SQLite AFTER INSERT/UPDATE/DELETE triggers**).

#### B. `Workagent` SQLite Schema (`src/workagent/store_sqlite.py`)
Workagent uses Python's `sqlite3` stdlib directly (no ORM, idempotent schema migrations v1–v26 in `store_sqlite.py`) with 14 focused tables:

1. **Repositories, Trackers & Worktrees**:
   - `trackers` (`key_ref` PK e.g. `jira:IPG` or `github:ynsr/workagent`, `remote_url`, `vendor` [`jira`|`github`], `fetch_enabled`, `is_enabled`)
   - `repos` (`key_ref` PK, `path` UNIQUE, `name`, `remote`, `tool` [`gh`|`glab`], `fetch_enabled`, `default_branch`, `release_branch`, `commands` JSON e.g. `{"pre-release": "..."}`, `overlay_buttons` JSON)
   - `tracker_repos` (`tracker_key` FK, `repo_key` FK, `ON DELETE CASCADE`)
   - `worktrees` (`ref_key` PK, `path` UNIQUE, `branch`, `repo_key` FK, `issue_url`, `pr_url`, `added_at`, `payload` JSON, `active` [1|0 for deactivated], `deleted_at` [7-day soft-delete recovery window], `related_repos` JSON, `parent_key_ref` for multi-repo child worktrees)
2. **Execution Runs, Custom Actions & Flows**:
   - `runs` (`id` PK, `command`, `args` JSON, `exit_code`, `created_at`, `output`, `truncated`, `validation` JSON, `flow_id` FK, `worktree_ref` nullable FK `ON DELETE CASCADE`, `harness_name`, `initiator_command`, `prompt`, `file_path`, `session_type`, `metadata` JSON, `state` [`running`|`succeeded`|`failed`|`needs_input`|`cancelled`], `continued_from_run_id` FK) + on-disk run directories (`~/.config/workagent/runs/<id>/output.log`, `meta.json`, and `input.jsonl` from PR #123)
   - `custom_actions` (`id`, `label` UNIQUE, `name`, `icon`, `prompt`, `pre_execution`, `post_execution`, `default_options` JSON, `protected`, `sort_order`, `dedicated_top_bar`, `dedicated_overlay`, `run_immediately`, `category` [`worktree`|`message`])
   - `flow_templates` (`id`, `label` UNIQUE, `name`, `icon`, `steps` JSON, `pre_execution`, `post_execution`, `parent_labels` JSON for clone/compose lineage, display flags, `category` [`worktree`|`message`])
   - `flows` (`id` PK, `template_label`, `worktree_ref`, `repo_snapshot` JSON, `status` [`pending`|`running`|`paused`|`done`|`failed`], `current_step`, `step_results` JSON, `effective_steps` JSON)
3. **Caches, Chat Messages & Production Testing**:
   - `pr_cache` (`branch` PK, `payload` JSON storing PR metadata, CI state, behind/ahead counts, review counts, `checked_at`)
   - `issue_cache`, `pr_candidates_cache`, `release_diff_cache` (server-side SWR caches for candidates and release branch diffs)
   - `messages` (`id`, `message_id` UNIQUE, `thread_id`, `app_name` [`mattermost`], `sender_username`, `content`, `ts`, `repos` JSON, `related_repos` JSON, `worktrees` JSON, `added_at`)
   - `prodtest_items` (`issue_key` PK, `plan_path`, `plan_revision`, `plan_risk`, `plan_diff_hash`, `release_sha`, `released_at`, `status` [`pending`|`pending_prodtest`|`running`|`passed`|`failed`|`blocked`], `last_run_id`, `worktree_ref` [deliberately not cascade-deleted so post-release ledger survives worktree teardown], `run_results` JSON, `updated_at`).

#### Key Data Modeling Takeaways
- **Normalized SCM & Conversation Entities (AO) vs. Coarse JSON Blobs & Log Files (Workagent):** AO normalizes `prs`, `pr_checks`, `pr_review_threads`, `pr_comments`, `conversations`, `conversation_turns`, `conversation_messages`, and `conversation_activities` into relational tables with SQLite FTS (`conversation_messages_fts`). Workagent stores PR/CI/review state as a single JSON payload inside `pr_cache(branch)` and stores agent execution transcripts as flat files (`output.log` / `.jsonl`) referenced by `runs.file_path`.
- **Trigger-Based Change Data Capture (AO) vs. Per-Run SSE + Endpoint Polling (Workagent):** AO uses SQLite triggers to append every mutation into `change_log`, which a single `CDC Poller` tails and broadcasts over `/api/v1/events` to invalidate exact React Query keys in real time. Workagent streams per-run stdout/stderr over `/api/runs/{id}/events` and polls `/api/status` every 15s (and `/api/runs` every 1.5–4s).
- **Derived Status Contract (AO):** AO never persists display statuses (`Working`, `Fixing CI failures`, `Changes requested`, `Mergeable`); `contract.DeriveKanbanPresentation` computes both the Kanban column and status phrase deterministically at read time from durable facts.

---

### 2.2 Functionality and Features Comparison

| Capability | `agent-orchestrator` (AO) | `Workagent` |
|---|---|---|
| **Multi-Agent Hierarchy** | **2-Tier Hierarchy:** Project-scoped **Orchestrator** agent spawns and supervises N **Worker** agents (`ao spawn`, `ao send`, `reports` batching) + **Subagents** panel + independent **Reviewer** agent runs. | **Flat Peer Worktrees + Multi-Repo Children:** Parent worktree (`worktrees.ref_key`) can spawn synchronized child worktrees in `--related-repo` checkouts (`parent_key_ref`), and `flow run` orchestrates sequential/parallel steps across 1..N worktrees. |
| **Closed-Loop CI & Review Auto-Fix** | **Autonomous Continuous Nudge Engine:** `SCMObserver` detects new failing CI checks (extracting `log_tail`), unresolved PR review threads, or merge conflicts and automatically queues a turn into the worker session (`AutoInjectCI`, `AutoInjectReview`). | **Command / Flow-Triggered:** `workagent review` (with `--fix` or `--then-fix-comments` or `review_fix` flow template) and `workagent release fix-default` run headless/ACP agents when invoked by the user or a flow step. |
| **Interactive Terminal vs. Structured Chat** | **Dual-Mode with Live Handoff:** Every session runs in either `chat` mode (detached ACP/Codex host) or `tui` mode (native PTY/ConPTY/tmux streamed over `/mux` WebSocket to `xterm.js`), with lossless live `TUI ↔ Chat` switching (`session_interface_transitions`). | **4 Execution Modes (PR #123 + PR #129):** `preview` (prints command), `headless` (`-p` one-shot subprocess), `acp` (JSON-RPC stdio streaming with `Structured / Raw` toggle, coalesced Markdown/thought/tool-call `<details>` in `AcpTranscript`, and `QuestionCard` via `WORKAGENT_RUN_DIR/input.jsonl`), and `terminal` (spawns local OS terminal emulator window). No in-browser `xterm.js` WebSocket mux. |
| **Issue Tracker & Chat Ops Integration** | Basic read-only GitHub/GitLab issue intake (`AO_TRACKER_INTAKE`); no Jira state machine or team chat integration. | **Deep Jira + GitHub + GitLab + Mattermost Integration:** Automatic Jira workflow transitions (`To Do → In Progress → Review → Product Test → Done`), Mattermost permalink ingestion (`messages`), claim validation, Persian reply drafting, and issue/PR candidate discovery. |
| **Release & Post-Release Verification** | Stops at PR merge (`POST /prs/{id}/merge`). | **Full Release & Prodtest Lifecycle:** `Ready to release` dashboard card (`default → release` branch diff, pre-release test gate, `fix-default` rebase/CI fixer, `Close released` issue closer) + `prodtest plan/run` YAML post-release verification ledger. |
| **Scheduling & Automations** | Built-in **Automations** engine (`automations` table, RRULE/cron + IANA timezone scheduler, starter templates for daily PR review, issue triage, weekly release notes). | No built-in cron/RRULE scheduler inside `workagent serve` (relies on external OS timers or manual flow launches). |
| **Artifacts, Previews & Usage Analytics** | Versioned **Artifacts Gallery** (live HTML/PDF/Markdown/Image preview, cloud sharing), integrated **Browser Preview** (`WebContentsView` + CDP bridge), and **Usage/Spend/Quota** analytics dashboard. | No artifact gallery, embedded browser preview, or token/cost usage ledger. |

---

### 2.3 Architecture and Design Patterns

1. **Daemon & Process Supervision**:
   - **AO (`backend/internal/`)**: Hexagonal **Ports & Adapters** architecture in Go. The CLI (`ao`) and Electron UI are strictly thin HTTP/SSE/WebSocket clients over the loopback daemon (`127.0.0.1:3001`), plus an opt-in bearer-authenticated LAN listener (`0.0.0.0:3011`) for mobile pairing. Agent Chat providers (Codex, ACP harnesses) run in **detached per-session host processes** so restarting the daemon or desktop app never interrupts an in-flight agent turn. Idle Chat sessions automatically **hibernate** after a 30s view-lease expiry and wake transparently on the next message.
   - **Workagent (`src/workagent/`)**: Unified Python package where the CLI (`cli.py`) calls core domain modules directly, and the FastAPI web server (`webapp.py`) reuses the exact same Python functions for read endpoints (`GET /api/*`) while spawning detached `sys.executable -m workagent.run_supervised` child processes for mutating commands (`POST /api/runs`). Strict parity is enforced by `tests/test_webapp.py::test_specs_mirror_cli_flags`, which introspects the real Typer app.
2. **Contract & Type Safety**:
   - **AO**: Code-first Go DTOs (`controllers/dto.go`) generate `openapi.yaml` → `openapi-typescript` generates `frontend/src/api/schema.ts`, enforced by CI drift gates.
   - **Workagent**: Hand-maintained `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + TypeScript interfaces in `web/src/lib/api.ts`, verified by pytest + vitest suites.

---

## 3. Frontend (UI/UX) Deep-Dive & Visual Screenshot Comparison

### 3.1 Visual Thesis & Layout Architecture

#### `agent-orchestrator` Screenshots & Code Analysis
- **`orchestrator.jpg` (`Sidebar.tsx` + `SessionView.tsx` + `ChatWorkspace.tsx` + `WorkerUpdates.tsx`)**:
  - **Visual Thesis**: "Dark-first precision instrument" (`DESIGN.md`) — layered charcoal surfaces (`--color-bg-primary`, `--color-bg-secondary`), sentence-case typography (`SF Pro`/`Geist` + `SF Mono`/`Geist Mono`), minimal 1px structural borders, and semantic status colors (`#60a5fa` Working blue, `#f06445` Needs-you coral/red, `#f59e0b` In-review amber, `#4ade80` Ready green, `#c084fc` Merged purple).
  - **Left Sidebar (`240px`, collapsible to `48px` icon rail)**: Shows brand header, `Search (⌘K)` trigger, 2 global utility items (`Artifacts`, `Automations`), and a single `Projects` tree (`billing-service`, `web-app`, `marketing-site`). Expanding `billing-service` reveals its pinned `Orchestrator` row and indented live child worker sessions, each with a single blue spinning activity indicator.
  - **Center Pane (Orchestrator Chat)**: Topbar has only the project context (`Orchestrator billing-service`) and 3 high-leverage controls: `+ Task`, `Kanban` toggle, and the notification bell. Inside the chat, the user's prompt bubble is followed by a collapsed 1-line tool summary (`Explored 14 files, 5 searches >` via `ActivityRun.tsx`) and a composed **`Started 3 workers — Each in its own worktree`** card (`SpawnedAgents` in `WorkerUpdates.tsx`) listing each worker's harness icon (Claude Code, Codex, Cursor), task title, mono branch name (`metering-api`, `usage-chart`, `invoice-email`), harness label, and live `Working` spinner.
- **`board.jpg` (`SessionsBoard.tsx` + `packages/product-ui/src/SessionsBoardView.tsx`)**:
  - **4-Lane Delivery Kanban**: `Working (2)`, `Needs you (2)`, `In review (2)`, `Ready to merge (1)` separated by full-height 1px vertical dividers.
  - **High-Signal Session Cards (`SessionCardView`)**: Each card displays in strict priority order:
    1. Provider avatar icon (Claude Code, Codex, Cursor, Gemini) + bold 2-line task title.
    2. Muted monospace branch slug (`metering-api`, `checkout-flake`).
    3. Compact PR badge (`#479 open`, `#481 open`, `#476 open`) with lifecycle color.
    4. Bottom status strip with a single derived live activity or blocker phrase + elapsed time:
       - `Working`: `Editing metering.go (1m)`, `Running tests (3m)` with blue spinner.
       - `Needs you`: Coral-outlined cards (`border-status-needs-you`) with `Asking which mail provider (2m)` and `CI failed · e2e (chrome) (9m)`.
       - `In review`: Amber text `Review pending (14m)`, `Checks running (6m)`.
       - `Ready to merge`: Green text `Approved (22m)`.
- **`feedback.jpg` (Closed-Loop Feedback Timeline)**:
  - Visualizes the automated PR feedback loop on `#481 Rate-limit the export endpoint (export-rate-limit)`:
    1. `9:41` (`github`): Red `✕` — `e2e (chrome) failed on export.spec.ts (ci)` → `+ sent to Claude Code with the failing check and a link to its log`.
    2. `9:44` (`nishant`): Amber comment icon — `Return Retry-After so clients can back off (review)` → `+ routed to the same session, with the thread to resolve`.
    3. `9:52` (`claude code`): Agent icon — `Pushed 2 commits to export-rate-limit (fix)` → `+ export.ts +14 -3, export.spec.ts +22, thread resolved`.
    4. `9:58` (`nishant`): Green `✓` — `Checks passing, approved (ready to merge)` → `+ merge it yourself, or let the orchestrator do it`.

#### `Workagent` Screenshots (`/home/bs/Pictures/Screenshots/workagent/*.png`) & Code Analysis
- **Sidebar (`AppLayout.tsx`)**: Fixed `256px` (`w-64`, collapsible to `w-16`) left navigation containing **15 top-level links** split across 7 divider groups:
  1. `Dashboard`, `Launch`
  2. `Messages`, `Message Launch`
  3. `Flows`, `Runs`
  4. `Prodtests`, `Prodtest Launch`
  5. `Flow Templates`, `Actions`
  6. `Repos`, `Trackers`, `Links`
  7. `Doctor`, `Settings`
- **Screenshots 1 & 2 (`23-49-11.png`, `23-49-20.png` — `Dashboard.tsx`)**:
  - **Top Action Bar**: `Actions`, `Workagent: Start to Cleanup` (dedicated top-bar flow button), `Flows` dropdown, `Refresh PR`, `Bulk actions` dropdown, `Close released`, and `Worktree paths` switch.
  - **Filter & Repo Pills**: Search input (`Filter by key, branch, PR, path...`), `PR: all`, `Issue: all`, `Author: all` dropdowns, and repo filter pills (`All repos (3)`, `projectx (1)`, `workagent (2)`).
  - **Worktrees Table (`StatusTable.tsx`)**: 10 columns (`Checkbox`, `Worktree`, `Issue`, `Branch`, `Behind|Ahead`, `PR / MR`, `CI`, `Reviews`, `Prodtest`, `Added`) plus hover overlay action buttons and collapsible `Show deactivated worktrees (4)` / `Show deleted worktrees (13)` accordions.
  - **Candidates Card (`CandidatesCard.tsx`)**: Tabbed card (`Unlinked PR/MRs (2)`, `Recent issues (4)`, `Worktrees (10)`) with warning banner (`skipped disabled issue source(s): github`) and one-click `▷ Review` / `Start` buttons.
  - **Ready to Release Card (`ReleaseCard.tsx`)**: Lists 5 repos (`ap-ipg`, `behpardakht-ipg`, `ipg-commons`, `projectx`, `saman-ipg`) with default→release commit counts (`develop → main · 8 commits ahead`), CI status badge, `Fix default` button (when behind or CI failing), and `Release` button.
- **Screenshots 3 & 5 (`23-49-56.png`, `23-50-52.png` — `Messages.tsx` & `MessageLaunch.tsx`)**:
  - `Messages` page shows a 5-field filter card (`Search`, `Sender`, `App`, `Repo`, `Thread`, `Group by thread`) over a 10-column table (`GET /api/messages`) with RTL Persian Mattermost text (`malekshah`, `projectx`) and expandable row details.
  - `Message Launch` is a separate full page with a right-aligned card (`Adhoc`, `Validate claimed issues`, `Draft reply`, `Run action`, `Run flow` tabs) and large empty whitespace on the left.
- **Screenshot 4 (`23-50-47.png` — `Launch.tsx`)**:
  - Separate full page with a right-aligned form card (`Start`, `Review`, `Sync`, `Adhoc Prompt`, `Flow` tabs), `Issue ref` selector, `Repository` selector, `Related repos` pill toggles (9 repos), `Base branch`, `Action` (`Implement`|`Plan`), `Run mode` (`Preview`|`Run headless`|`Run in terminal`), `Harness` (`Default (agy)`|`agy`|`omp`), `Extra prompt`, and an amber warning box for `--terminal`.
- **Screenshots 6 & 8 (`23-51-02.png`, `23-51-25.png` — `Flows.tsx` & `FlowTemplates.tsx`)**:
  - `Flows` renders a 6-column table (`ID`, `Template`, `Worktree`, `Status`, `Step`, `Updated`) where clicking `#38` or `#104` opens a modal listing step results (`step 0`, `step 5`).
  - `Flow Templates` renders a 13-column management table (with horizontal scrollbar) alongside two right-hand cards (`Run a message flow` and `Add a template` with AI `Suggest icon`).
- **Screenshot 7 (`23-51-18.png` — `Runs.tsx`)**:
  - State filter pills (`All`, `Running`, `Succeeded`, `Failed`, `Needs input`, `Cancelled`) over an 8-column table (`Run`, `Command`, `Kind`, `Target`, `State`, `Exit`, `Started`, actions for terminal/copy/cancel). Notice that a single user flow action spawns multiple stacked rows (`db-1997 start`, `db-1996 start`, `db-1995 flow run`, `13bf871a80 flow run`) that appear as flat siblings rather than a collapsible tree.
- **Screenshots 9 & 10 (`23-51-33.png`, `23-51-46.png` — `Prodtests.tsx` & `ProdtestLaunch.tsx`)**:
  - `Prodtests` renders a 7-column table (`Issue`, `Status` [`pending_prodtest`], `Plan` [full filesystem path causing horizontal overflow], `Release` [12-char SHA], `Last run`, `Updated`, `Actions`).
  - `Prodtest Launch` is a third separate launch page (`Plan` | `Run` toggle) with the same right-aligned form layout.

---

### 3.2 Page-by-Page Breakdown of `agent-orchestrator` vs. `Workagent`

#### Page 1: App Shell, Navigation Sidebar & Command Palette
- **`agent-orchestrator` (`Sidebar.tsx`, `CenterPanelShell.tsx`, `PageTopbar.tsx`, `CommandPalette.tsx`)**:
  - **Components**: 3-pane desktop shell (Resizable Left Sidebar `240px` / `48px` rail + Center Route Surface + Collapsible/Resizable Right `SessionInspectorRail`). Global `⌘K` `CommandPalette` searches across actions, projects, sessions, chat transcripts (FTS), workspace files, and archived sessions.
  - **Entity Representation**: Projects and their active sessions live directly in the sidebar tree. Each session row has a single semantic status slot (blue working spinner + title shimmer sweep, red attention alert, orange review alert, green PR ready icon, or grey unread dot).
  - **Orchestration Role**: Lets an engineer supervise 10+ parallel agents across multiple repos and switch context in one click or `⌘K` keystroke without leaving the working surface.
- **`Workagent` (`AppLayout.tsx`, `RunWatcher.tsx`)**:
  - **Components**: Fixed left sidebar (`w-64` / `w-16`) with 15 static page links, mobile top header + 6-item bottom navigation bar, main outlet container, and background `RunWatcher` toast notifier.
  - **Comparison & Gap**: Workagent's sidebar lists *feature pages* rather than *active worktrees/projects*, forcing users to return to the Dashboard table or Runs table to find a task. Splitting `Messages` / `Message Launch` and `Prodtests` / `Prodtest Launch` into separate sidebar routes inflates navigation to 15 items and leaves 50% empty whitespace on the three Launch pages. There is no global `⌘K` command palette.

#### Page 2: Home & Project / Repo Onboarding
- **`agent-orchestrator` (`HomePage.tsx`, `CreateProjectFlow.tsx`, `CloneRepositoryDialog.tsx`)**:
  - **Components**: Minimal centered `640px` column with a 2×2 action tile grid (`Clone a repo`, `Open a local folder`, `Open a workspace`, `Start a standalone agent` / `Clone to the cloud`) + top 3 recent project rows using `NavRowHighlight`.
  - **Orchestration Role**: Frictionless zero-to-one onboarding that immediately provisions a project and its default orchestrator/worker agent.
- **`Workagent` (`Repos.tsx`, `Trackers.tsx`, `Links.tsx`)**:
  - **Components**: Three separate CRUD admin pages (`Repos`, `Trackers`, `Links`) with tables and add/edit forms.
  - **Comparison & Gap**: Powerful explicit control over `tracker ↔ repo` many-to-many mappings, default/release branches, pre-release commands, and dashboard overlay button visibility, but fragmented across 3 pages with raw API subtitles (`GET /api/repos`) exposed in card headers.

#### Page 3: Operational Overview — Kanban Board vs. Worktree Dashboard
- **`agent-orchestrator` (`SessionsBoard.tsx`, `SessionsBoardView.tsx`, `ArchiveModalHost.tsx`)**:
  - **Components**: 4-column delivery Kanban (`Building/Working`, `Validating`, `In review/Needs you`, `Ready to merge`) + toolbar search (`⌘F`) + `+ Task` & `Orchestrator` header buttons + bottom `Archived sessions` modal trigger (opens a keyboard-navigable 3-column card grid inside the `⌘K` glass surface).
  - **Entity Representation**: Cards group multi-PR stacks, show provider avatars, branch topics, reviewer avatars with unresolved comment counts, resource/memory pressure badges (in developer mode), and a single derived status phrase (`Fixing CI failures`, `Addressing comments`, `Waiting on base PR`). Cards requiring human action (`Needs input`, `Blocked`, `CI failing`, `Merge conflicts`, `Changes requested`) sort to the top of their lane and pulse subtly with `--color-status-needs-you`.
- **`Workagent` (`Dashboard.tsx`, `StatusTable.tsx`, `WorktreeDetail.tsx`, `CandidatesCard.tsx`, `ReleaseCard.tsx`)**:
  - **Components**: Dense tabular control center combining:
    1. `WorktreeFilterForm` + repo tabs (`All repos`, per-repo counts).
    2. `StatusTable` (active worktrees + indented multi-repo child worktrees + hover quick-action overlay + row click `WorktreeDetail` drawer/modal).
    3. `DeactivatedTable` and `DeletedTable` (7-day soft-delete trash with bulk Undelete/Destroy).
    4. `CandidatesCard` (`Unlinked PR/MRs`, `Recent issues`, unregistered `Worktrees`).
    5. `ReleaseCard` (`Ready to release` default→release branch diff & release runner).
  - **Comparison & Gap**: Workagent's Dashboard packs impressive operational breadth (especially `Candidates` and `Ready to release`, which AO lacks). However, `StatusTable` requires horizontal scanning across 10 columns and does not group worktrees by *who owns the next turn* (`Working` vs. `Needs human input` vs. `In review` vs. `Ready to merge`). When a flow is paused or an ACP run asks a question, or when CI fails on a PR, the row does not visually elevate into an attention lane.

#### Page 4: Task Creation & Agent Launching (`NewChatPage` vs. `Launch` / `MessageLaunch` / `ProdtestLaunch`)
- **`agent-orchestrator` (`NewChatPage.tsx`, `TurnSettingsBar.tsx`, `TaskComposer.tsx`)**:
  - **Components**: "New Task is a page, not a modal: the session's own chat with no session yet." Centered prompt composer with sample prompt chips, `@` workspace file autocomplete (backed by background pre-warmed worktree preparation), image/file attachment staging, voice dictation, and a compact bottom `TurnSettingsBar` (Agent/Model picker, Reasoning Effort picker, Approval Mode picker, Account picker, and Project tray).
  - **Orchestration Role**: Sending the first message transitions seamlessly in-place into the live session chat without page jumps or form ceremony.
- **`Workagent` (`Launch.tsx`, `MessageLaunch.tsx`, `ProdtestLaunch.tsx`, `StartForm.tsx`)**:
  - **Components**: Three separate form pages using right-aligned `<Card>` layouts. With PR #123 and PR #129, all launch forms (plus `ChainEditor` step configs and `MessageRowActions`) support `<HarnessSelect>` (`agy`|`omp`), `<RunModeSelect>` (`Preview`|`Headless`|`ACP`|`Terminal`), `<ModelSelect>`, and `<EffortSelect>` populated via `GET /api/harnesses/{name}/options` (with `?refresh=1` cache bypass and persisted launch-pref hydration).
  - **Comparison & Gap**: Workagent's forms expose rich domain flags (`--related-repo`, `--plan` mode, `--skip-status-updates`, review `--fix-comments`, message `validate`/`draft-reply`), but having three separate Launch pages in the sidebar creates navigation clutter and forces a "fill form → confirm modal → redirect to `/runs/:id`" workflow instead of an interactive task composer.

#### Page 5: Live Session Workspace & Orchestrator View (`SessionView` & `ChatWorkspace` vs. `Runs` & `RunDetail`)
- **`agent-orchestrator` (`SessionView.tsx`, `CenterPane.tsx`, `ChatWorkspace.tsx`, `ActivityRun.tsx`, `WorkerUpdates.tsx`, `ElicitationDock.tsx`, `QueuedMessageDock.tsx`, `TerminalPane.tsx`, `SplitView.tsx`)**:
  - **Components**:
    - **Draggable Split-Pane Tab Strip (`CenterPane.tsx`, `SplitView.tsx`)**: Primary agent tab (Chat or Terminal TUI) + auxiliary tabs for Reviewer terminal/chat, worktree Shell terminals (`+` shell button), opened Workspace Files, and PR tabs. Any tab can be dragged out to split the center pane horizontally or vertically.
    - **Structured Chat Timeline (`ChatWorkspace.tsx`)**:
      - Virtualized turn list opening every settled turn with `"Worked for <duration>"` (`TurnWorkSummary.tsx`).
      - Consecutive tool/command calls automatically folded into a single semantic summary line (`"Explored 14 files, 5 searches"` + `+<additions> -<deletions>` diff counter via `ActivityRun.tsx`), while failed commands automatically surface unfolded below the fold.
      - **Orchestrator Worker Cards (`WorkerUpdates.tsx`)**: Renders `Started N workers` cards with live status and collapses incoming worker reports (`needs_input`, `stuck`, `done`) into compact expandable rows.
      - **Interactive Docks**: `ElicitationDock.tsx` / `ApprovalCard` for structured agent questions and tool permission requests; `QueuedMessageDock.tsx` for queuing follow-up messages while a turn is streaming or clicking **Steer** (`⌘↵`) to inject guidance into the active turn; `ContextMeter.tsx` and `QuotaReadout.tsx` showing token context window % and account rate-limit reset timers; non-destructive turn rollback/branching (`ConversationBranchNavigator.tsx`).
    - **WebSocket Terminal (`TerminalPane.tsx`, `XtermTerminal.tsx`)**: Native `xterm.js` connected over `/mux` binary WebSocket with search, font zoom, OSC title tracking, and foreground shell-agent activity detection.
    - **Seamless Mode Switch (`SessionInterfaceSwitch.tsx`)**: Toggle button switching the same underlying Claude Code / Codex conversation between Terminal TUI and Chat UI.
- **`Workagent` (`Runs.tsx`, `RunDetail.tsx`, and merged PR #123 / PR #129 `AcpTranscript` + `QuestionCard`)**:
  - **Components**:
    - `Runs.tsx`: Filterable table of all CLI child executions (`db-1997`, etc.).
    - `RunDetail.tsx`: Header with `Same worktree runs`, `All runs`, `RunResumeActions` (`Resume in terminal`, `Copy resume command`), `RunHarnessCommandAction` (`Start in terminal`, `Copy`), `Re-run`, and `Cancel` (`SIGTERM`), above `QuestionCard`, a **`Structured / Raw`** tab toggle (defaulting to `Structured` whenever ACP events are present), and either `<AcpTranscript />` or `<LogViewer />` (`h-[60vh]` dark monospace container streaming SSE lines with `Follow output` checkbox).
    - **Merged PR #123 (`ff8b1ab`) + PR #129 (`bdd1809`) Additions**: `parseAcpBlocks` coalesces consecutive `message` and `thought` chunks, merges `tool_call`/`tool_call_update` events in place by `toolCallId`, formats `usage` events into readable token summaries (`tokens: in X · out Y · total Z`), and normalizes `question` options. `AcpTranscript` renders `thought` blocks inside collapsed `<details>` (`Thinking`), `tool_call` blocks inside `<details>` (expanded for running/failed, collapsed for completed, with sticky user toggles, tool-kind icons, status badge, and formatted args/output), assistant `message` blocks via `react-markdown` + `remark-gfm`, and sticky-bottom autoscroll (`Follow output`). `QuestionCard` pins above the transcript when `pending_question` is active (cleared automatically when the run exits and backfilled synchronously on `rehydrate()`), posting qid-validated answers to `POST /api/runs/{id}/input` → `WORKAGENT_RUN_DIR/input.jsonl`.
  - **Comparison & Gap**:
    - PR #123 and PR #129 deliver end-to-end ACP transport (`AcpClient` with stderr ring buffer and SIGTERM cleanup), dual-mode `validate_headless` dispatch, dynamic model/effort discovery, structured Markdown/collapsible `AcpTranscript` with a `Structured / Raw` toggle, and supervised inline `QuestionCard` answering.
    - Remaining gaps verified against `main` (`bdd1809`):
      1. **Multi-Turn Follow-up & Steer**: `AcpHarness.launch_acp` still always calls `session/new` (no `session/load` resume yet) and exits as soon as no `question` event is pending (which is always true for `omp`, where `extract_question` returns `None`); `POST /api/runs/{id}/input` strictly enforces an active `pending_question` (`409 no_pending_question` / `409 not_running`). There is no chat composer to send follow-up prompts or mid-turn steers.
      2. **Multi-Tool `ActivityRun` Folding & Turn Summary Headers**: While PR #129 added per-block chunk coalescing, `toolCallId` merging, `<details>` folding for individual thoughts/tool calls, Markdown rendering, and the `Structured / Raw` toggle, `AcpTranscript.tsx` still renders each tool call as its own sequential block. Grouping 3+ consecutive exploratory tool calls into a single AO-style `ActivityRun` summary bar (`"Explored 14 files, 5 searches · +42 −11"` with auto-extracted `FailedCalls`) and adding settled turn headers (`"Worked for <duration> · <model> (<effort>)"`) will further reduce visual noise on long runs.
      3. **Parent-Child Run Hierarchy in `Runs.tsx`**: A flow execution spawns multiple child runs (`flow run` → `start` → `review` → `fix_comments`), which currently render as flat ungrouped rows in `Runs.tsx`.

#### Page 6: Contextual Inspector, Diff Review & Feedback Timeline (`SessionInspector` vs. `WorktreeDetail` & `Flows`)
- **`agent-orchestrator` (`SessionInspectorRail.tsx`, `SessionInspector.tsx`, `WorkspaceDiffView.tsx`, `BrowserPanel.tsx`, `SubagentPanel.tsx`)**:
  - **Components**: Collapsible, spring-animated right rail (`340px–900px`) with 7 contextual tabs:
    1. **Summary**: Branch work summary (`ahead/behind`, dirty files), PR pills & check groups, CI failure links, one-click `Merge` / `Resolve comments` actions, account switcher, and **Activity / Feedback Timeline** (showing chronological PR events, CI failures routed to the agent, review comments routed to the session, and agent commits).
    2. **Files (`WorkspaceDiffView.tsx`)**: Virtualized unified or split (`old | new`) syntax-highlighted diff viewer and image diff viewer (`ImageDiffView.tsx`) with **inline multi-line comment annotations** (`FileAnnotationModel`) that batch and send line-specific feedback directly into the active agent chat.
    3. **Reviews**: Dedicated interactive AI reviewer tab (`ReviewerChatSurface` / `ReviewerPicker`) showing automated review runs per commit SHA, verdicts, and one-click "Send to worker".
    4. **Browser (`BrowserPanel.tsx`)**: Embedded live Chromium preview (`WebContentsView`) with URL bar, logical tabs, element annotation queueing to chat, and detached DevTools.
    5. **Side Chat (`IndependentSideChats.tsx`)**: Ephemeral `/btw` side conversations forked from the current session context without polluting the main agent transcript.
    6. **Subagents (`SubagentPanel.tsx`)**: Live tree and transcripts of child subagents spawned during the turn.
    7. **Artifacts (`SessionArtifactsView.tsx`)**: Deliverables created by the session.
- **`Workagent` (`WorktreeDetail` in `StatusTable.tsx`, `Flows.tsx`, `FlowTemplates.tsx`, `ChainEditor.tsx`)**:
  - **Components**:
    - `WorktreeDetail`: Modal/drawer showing worktree metadata (path, branch, issue link, PR link, CI status, review counts, active harness PID, and quick action buttons).
    - `Flows.tsx` & `FlowTemplates.tsx`: Visualizes and edits multi-step pipelines (`start → review → fix_comments → sync → prodtest_plan → cleanup → release`) with sequential (`→`) and parallel (`∥`) badges and pre/post shell script blocks.
  - **Comparison & Gap**: Workagent has a first-class pipeline builder (`ChainEditor` + `FlowWizard`) that AO lacks, but Workagent has **no in-app Git diff viewer**, **no inline code annotation/review UI**, and **no unified per-worktree feedback timeline** showing how PR review comments, CI runs, flow steps, and agent runs interacted over time.

#### Page 7: Artifacts, Automations, Usage & Settings
- **`agent-orchestrator`**:
  - **Artifacts (`ArtifactsView.tsx`)**: Virtualized 3-column gallery & list view of all files/reports generated by agents across local, remote, and cloud hosts, with live HTML/PDF/Markdown/Mermaid/SVG/Table previews (`ArtifactPreviewModal.tsx`), version history, pinning, and cloud share links.
  - **Automations (`AutomationsView.tsx`)**: Starter template grid (`Daily PR review`, `Daily issue triage`, `Weekly release notes`) + scheduled RRULE automation list with expandable run history.
  - **Usage (`UsageView.tsx`)**: Token spend, cost breakdowns by project/model, and live subscription rate-limit windows (`Spend`, `Limits`, `Windows` tabs).
  - **Settings (`SettingsDialog.tsx`)**: Split modal (quiet left navigation rail + borderless grouped content cards) covering `General`, `Agents` (1-click CLI install & readiness diagnostics), `Accounts` (multi-account CLI profile switcher), `Shortcuts`, `Connect Mobile` (QR pairing), and per-project `Scripts`, `Environment`, and `Cues`.
- **`Workagent` (`Doctor.tsx`, `Settings.tsx`, `Actions.tsx`, `Prodtests.tsx`)**:
  - **Doctor & Settings**: Tool health checks (`git-wt`, `agy-guard`, `omp`, `gh`, `glab`, `jira`), install-sync verification, systemd user service readout, verbose toggle, default AI harness selector, and database backup/restore.
  - **Actions & Prodtests**: Custom prompt/script action library (with AI Lucide icon suggester) and post-release production test plan/run ledger.

---

### 3.3 Evaluation Across the 5 Frontend (UI/UX) Dimensions

| UI/UX Dimension | `agent-orchestrator` (AO) | `Workagent` |
|---|---|---|
| **1. User Interface Design & Usability** | Cohesive dark-first token design system (`tokens.css`); low border noise (shared lists with 1px dividers); strict sentence case; zero API jargon in UI; persistent 3-pane workspace shell. | Clean shadcn/Tailwind foundation with light/dark support, but reads like an internal admin console: 15 sidebar items, raw endpoint subtitles (`GET /api/messages`, `GET /api/flow-templates`) in card headers, wide tables requiring horizontal scrollbars, and 3 separate right-aligned Launch pages. |
| **2. Management of Workflows & Tasks** | State-driven Kanban lanes (`Building`, `Validating`, `In review`, `Ready`) + searchable `Archived sessions` modal + recurring RRULE `Automations` + project `Cues`. | Powerful declarative `Flow Templates` (clone, compose, sequential/parallel fan-out, resumable paused steps) + bulk worktree operations + 7-day soft-delete trash + `Ready to release` & `Prodtests` lifecycle. |
| **3. Integration with AI Agents & Orchestration** | Conversational `Orchestrator` meta-agent spawns parallel workers across 23+ harnesses; live `TUI ↔ Chat` handoff; inline `Steer`, message queueing, and `/btw` side chats; multi-account quota switching. | 2 harnesses (`agy`, `omp`) unified under `AcpHarness` (PR #123 + PR #129: `preview`, `headless`, `acp`, `terminal` across all CLI/web/flow/release/message commands) + dynamic model/effort discovery (`?refresh=1`) + Mattermost message-grounded agent flows + AI icon judge. |
| **4. Visualization of Agent Interactions & Progress** | `ActivityRun` folds noisy tool streams into `"Explored N files, M searches"`; `SpawnedAgents` card tracks child workers live; `Inspector` shows unified diff viewer with inline annotations, PR check logs, and feedback routing timeline. | `RunDetail` provides a `Structured / Raw` toggle with coalesced Markdown `AcpTranscript` blocks, collapsible `<details>` for thoughts and individual tool calls, formatted `usage` summaries, and `QuestionCard`; `Flows` modal shows step index/status. Still lacks multi-tool `ActivityRun` folding (`"Explored N files, M searches"`), settled turn duration headers, a per-worktree unified timeline, parent-child run tree, and in-app diff viewer. |
| **5. Responsiveness & Accessibility** | Full keyboard-first design (`⌘K` palette, `⌘F` contextual find, arrow-key archive grid combobox, focus restoration, `:focus-visible` growing pills), `prefers-reduced-motion` compositor-only animations (`transform`/`opacity`), `.sr-only` metric labels, and dedicated Expo mobile app. | Responsive mobile bottom nav (6-item grid) and collapsible desktop sidebar; standard ARIA labels on table controls and dialogs. However, wide tables (`FlowTemplates` 13 cols, `Prodtests`, `StatusTable`) overflow horizontally on medium viewports, and keyboard shortcuts / `⌘K` navigation are absent. |

---

## 4. Summary of Key Architectural & UX Insights for Workagent

1. **Preserve Workagent's Unique Strengths**: Workagent already excels in areas AO does not cover—specifically **declarative multi-step Flow Templates**, **Jira workflow state transitions**, **Mattermost chat message ingestion & reply drafting**, **multi-repo child worktrees (`--related-repo`)**, **release-branch management (`Ready to release` / `fix-default`)**, and **post-release production verification (`Prodtests`)**.
2. **Adopt AO's High-Leverage Supervision & UX Patterns**:
   - **Consolidate Navigation & Launch Surfaces**: Collapse the 15 sidebar items into 7 task-oriented destinations by embedding `Launch`, `Message Launch`, and `Prodtest Launch` directly into their parent views (or a unified `⌘K` Command Palette / slide-over drawer) and removing raw `GET /api/...` developer subtitles.
   - **Kanban / Attention-First Board View on Dashboard**: Add a **Board View (`Working` | `Needs You` | `In Review` | `Ready to Merge`)** alongside the existing `StatusTable`, powered by a deterministic read-time status reducer over worktree, run, flow, PR, CI, and review facts.
   - **Worktree Workspace View (Split Inspector + In-App Diff + Unified Activity Timeline)**: Upgrade `WorktreeDetail` into a persistent workspace view combining the active ACP transcript/composer, a unified activity/feedback timeline (runs, flow steps, CI checks, PR reviews), and an in-app Git diff viewer.
   - **Richer ACP Run Experience (Building on PR #123 & PR #129)**: Group consecutive exploratory tool calls into AO-style `ActivityRun` summaries (`"Explored N files, M searches"`), add settled turn duration/model headers, support multi-turn follow-up prompts / steering in `RunDetail`, and group child runs under their parent Flow in `Runs.tsx`.
   - **Event-Driven Reactivity & Closed-Loop Watchers**: Replace manual PR refresh and 15s polling with a background observer/webhook loop (opt-in per repo/worktree: `auto_fix_ci`, `auto_fix_reviews`) and a single global `/api/events` SSE invalidation stream.

See the companion document **[Workagent Improvement Roadmap & Issue Catalog](./workagent-improvement-roadmap.md)** for concrete backend and frontend implementation specifications and ready-to-file GitHub issues.
