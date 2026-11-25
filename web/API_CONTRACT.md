# Web API contract — `workagent serve`

Base URL: the server origin itself (`/api/...`). All bodies JSON.
Errors: `{"error": {"code": string, "message": string}}` with status
400 (validation), 403 (host/origin/content-type), 404, 409.

The Vite dev server proxies `/api` to `http://127.0.0.1:3344`
(`changeOrigin: false` — keep the browser's Origin/Host so the
same-origin check passes).

## Read endpoints

### `GET /api/info`
```json
{"version": "0.2.1", "host": "127.0.0.1", "port": 3344,
 "network_exposed": false}
```

### `GET /api/config`
```json
{
  "default_harness": "agy",
  "default_model": "",
  "default_effort": "",
  "supported_harnesses": ["agy", "omp"],
  "harnesses": [
    {"name": "agy", "label": "Antigravity (agy-guard)", "binary": "agy-guard", "available": true},
    {"name": "omp", "label": "OMP (omp)", "binary": "omp", "available": true}
  ]
}
```
Read-only runtime harness configuration (`store.default_harness()` — defaults
to `"agy"` — plus `store.default_model()`, `store.default_effort()`, and every
registered `Harness` in `backend.HARNESSES` with `shutil.which(binary)` availability).
Always 200.

### `GET /api/status` → all worktrees
Map of worktree key → entry (same shape as `workagent status --json`).
Query params: `ref` (single detail), `refresh=true` (re-query PR),
`include_inactive=true` (deactivated rows), `include_deleted=true`
(soft-deleted rows, 7-day recovery window).
Jira rows are keyed `jira:KEY@repo` (e.g. `jira:IPG-932@projectx`); bare
```json
{
  "jira:IPG-932@projectx": {
    "issue": "IPG-932", "worktree": "/home/x/wt", "branch": "feat/…",
    "harness": "omp 4242",
    "repo": "/home/x/projectx", "issue_url": "https://…/browse/IPG-932",
    "commits": "0|1", "pr": "…",
    "ci": "success",
    "ci_url": "https://…/-/jobs/194493",
    "added_at": "2026-09-20T10:00:00+00:00",
    "wt_valid": true,
    "active": 1, "deleted_at": "",
    "commits_detail": {"behind": 0, "ahead": 1},
    "pr_detail": {"number": 1706, "state": "open", "title": "…",
                   "author": "…", "url": "https://…", "tool": "glab"},
    "issue_status": "in progress",
    "kanban_column": "working",
    "display_status": "Working · omp",
    "attention_reason": null,
    "failing_checks": [],
    "unresolved_threads": 0
}
```
`failing_checks` lists names of currently failing normalized PR checks (`pr_checks`); `unresolved_threads` is the count of unresolved review threads.

### `GET /api/worktrees/{ref}/timeline?limit=100&fetch_logs=false` → normalized PR checks, review threads & feedback timeline
Returns `404` when `ref` does not match any linked worktree. When `fetch_logs=true`, refreshes missing CI checks and populates `log_tail` (last 50 lines of terminal output, ANSI-stripped) on failing GitHub/GitLab CI checks before returning.
```json
{
  "ref": "jira:IPG-123",
  "checks": [
    {"id": 1, "worktree_ref": "jira:IPG-123", "name": "pytest", "commit_sha": "abc", "status": "completed", "conclusion": "failure", "url": "https://...", "log_tail": "", "updated_at": "..."}
  ],
  "threads": [
    {"id": 1, "worktree_ref": "jira:IPG-123", "thread_id": "t1", "author": "alice", "file": "a.py", "line": 12, "body": "Fix null check", "resolved": false, "is_bot": false, "updated_at": "..."}
  ],
  "events": [
    {"id": 1, "worktree_ref": "jira:IPG-123", "event_type": "ci_failed", "summary": "CI failed: pytest", "details": {}, "created_at": "..."}
  ],
  "summary": {"failing_checks": ["pytest"], "unresolved_threads": 1}
}
```
`harness` is `"<name> <pid>"` while a harness is live on the worktree,
`""` otherwise. `commits` is the display string `"B|A"`; prefer
`commits_detail`.
`pr_detail` is `null` when no open/known PR. `issue_status` is the
lowercased tracker status of the linked issue (`in progress`, `review`,
`done`, ...) or `null` when the worktree has no linked issue, the tracker
is unsupported (Jira + GitHub only), or the lookup failed (cached within
max(10 min * 10, 1 day); re-queried when `refresh=true`). `ci` is the latest CI
pipeline status for the PR — `success` | `failure` | `running` |
`not_started` — or `null` when there is no PR or the lookup failed
(cached within max(10 min * 10, 1 day) while the branch tip is unchanged). `ci_url` is the
latest CI job page (GitLab `.../-/jobs/<id>`, GitHub check details URL)
`reviews_detail` is `{"reviews", "unresolved", "resolved"}` or
`null` when there is no PR or the lookup failed (same 10-min/tip cache).
`prodtest` is the stored prodtest item status (`pending` | `pending_prodtest` |
`running` | `passed` | `failed` | `blocked`), `-` when no plan exists;
`prodtest_detail` is `{"issue_key", "plan_path", "plan_revision", "plan_risk",
`plan_diff_hash`, "release_sha", "released_at", "status", "last_run",
`last_run_id`, "updated_at"}` or `null`.
`active` is `0` on deactivated rows (`link deactivate`), `1` otherwise;
`deleted_at` is the soft-delete stamp (`link remove`), `""` on live rows —
the Dashboard deactivated/deleted tables filter on these flags.
Plain (non-inline) `# Code Review` bot comments carry
no reliable cross-host resolution state, so one counts as resolved only when its second
non-empty line (below the header) matches `Status:\\s+RESOLVED` (case-insensitive) — on both GitHub and GitLab; non-bot threads keep their native flags.
Optional query:
`?refresh=true` fetches origin (fresh remote tips for Behind/Ahead) and
re-queries PR status (slow, hits the tracker CLI).

`_session_detail` shape: the entry fields plus
`key`, `harness`, `commits`, `pr` (display string), `commits_detail`,
`pr_detail`, `ci`, `reviews`, `reviews_detail`, `base_branch`, `issue_url`,
`wt_valid` (false when the recorded path is missing or not a live git
worktree), and `create_hint`
(only when there is no PR). `added_at` is present on entries stamped

### `GET /api/path?ref=IPG-932`
```json
{"key": "jira:IPG-932", "worktree": "/home/x/wt"}
```
404 `{error:{code:"not_found",…}}` for unknown ref or missing worktree.

### `GET /api/repos`
```json
[{"name": "projectx", "path": "/home/x/projects/projectx",
  "tracker": "jira:IPG", "trackers": ["jira:IPG"], "fetch_enabled": true}]
```
(`tracker` = first linked tracker; `trackers` = all linked tracker ids
from the tracker_repos join; any extra registry keys pass through.
`fetch_enabled` (bool) gates the Candidates PR scan per repo — `repo add
--fetch/--no-fetch`; GitHub (`gh`-tooled) repos default off.)
`commands` (object, e.g. `{"pre-release": "npm test"}`) is the per-repo
command map — currently only the `pre-release` gate, which runs in the
repo path before the release MR opens (failing gate aborts; empty = no
gate). `repo add`/`repo update` accept `--pre-release <cmd>`
(auto-detected from the project type on add unless given; empty clears).
`overlay_buttons` (object, e.g. `{"adhoc": false, "flow:deploy": false}`) is
the per-repo StatusTable overlay visibility map: `{button_id: bool}`,
missing key or `{}` = every button visible. Fixed ids: `sync`, `review`,
`fix-comments`, `prodtest`, `adhoc`, `cleanup`, `stop-terminal`,
`open-runs`, `open-flows`, `resume-session`, `dedicated`
(master toggle hiding all dedicated buttons); per-item dedicated ids are
`flow:<label>` / `action:<label>`. `repo update NAME --overlay-buttons
"sync,review,-adhoc,flow:deploy"` merges (`-id` hides, later entries win;
empty string resets); the Repos page edits it per row. Item
renames/destroys cascade: `flow:<label>`/`action:<label>` keys follow
`--new-label` (explicit target wins on conflict) and are pruned on delete —
no orphan keys accumulate.

### `GET /api/repos/detect?path=<localpath>`
Read-only auto-detection of the pre-release gate for a local path
(used as the add-form autofill; failures leave the field untouched):
```json
{"pre-release": "npm test"}
```
`{"pre-release": null}` when the path has no recognizable project type
or is unreadable. Always 200.

### `GET /api/trackers`
```json
{"trackers": [{"key": "jira:IPG", "vendor": "jira",
  "remote_url": "https://jira/…", "repos": 2, "fetch_enabled": true}]}
```
(CRUD page source: `tracker add/list/remove` run as runs; `repos` is the
linked-repo count. `vendor` is an enum: `jira` | `github`; `remote_url` is mandatory —
`tracker add` runs require `--remote-url`. `fetch_enabled` gates the
Candidates/Launch issue fetch per tracker — `tracker add
--fetch/--no-fetch`; `github:` ids default off.)

### `GET /api/actions` → `{"actions": [...]}`, `GET /api/actions/{label}`
```json
{"actions": [{"id": 1, "label": "generate_post_prd",
  "name": "Generate Post PRD", "icon": "Zap", "prompt": "…",
  "pre_execution": "", "post_execution": "",
  "default_options": {"run_mode": "preview"},
  "protected": false, "sort_order": 0,
  "dedicated_top_bar": false, "dedicated_overlay": false,
  "created_at": "…", "modified_at": "…"}]}
```
(CRUD page source: `action add/list/show/update/delete` run as runs; detail 404s
on unknown label. `action update OLD [--new-label NEW] ...` renames the label (flow steps cascade). Pre/post-execution are multi-line scripts; the details dialog highlights them via shebang detection. `action run <label> <ref> [--no-auto-register]` runs via `POST /api/runs`
with target key `action:<label>:<ref>` — one run per worktree, fanned out
by the Dashboard Actions dropdown; unlinked PR/MR refs auto-register a worktree unless `--no-auto-register` is passed.)

### `GET /api/flow-templates` → `{"templates": [...]}`, `GET /api/flow-templates/{label}`, `GET /api/flows?worktree=`
{"templates": [{"id": 1, "label": "full-review-cycle",
  "name": "Full review cycle",
  "steps": [{"kind": "start"}, {"kind": "review"}, {"kind": "cleanup"}],
  "pre_execution": "", "post_execution": "",
  "parent_labels": [],
  "protected": false, "sort_order": 0,
  "dedicated_top_bar": false, "dedicated_overlay": false,
  "created_at": "…", "modified_at": "…"}]}
```
 (Flow page source: `flow template add/list/show/update/clone/compose/delete` run as runs; detail 404s
 on unknown label. Step kinds: `start | review | fix_comments | sync | action | prodtest_plan | cleanup | release` —
 `action` steps carry `action_label` (a custom action; the Judgement preset ships with the Actions page),
 `prodtest_plan` generates the worktree's prod-test plan (config `update`), and `cleanup` accepts `merge_only`
 to merge the PR/MR squash-style without tearing down the worktree/branch/link.
 Each step carries `execution_mode` (`parallel` default for `start`, `sequential` default for all other steps | `parallel`): multi-worktree runs (Dashboard selection,
 wizard) run one worktree at a time when any step is sequential — waiting for each run to settle before starting
 the next — and fan out concurrently only when every step is parallel; `cleanup`/`release` reject
 `parallel` (teardown/creation ordering). The toggle is hidden for single-worktree runs.
 The Run dialog closes first and queued runs continue in the background with toast progress.
 The ChainEditor adds All-parallel/All-sequential bulk buttons plus a fan-out summary; the FlowWizard shows
 a live fan-out summary for multi-worktree refs; the templates table carries a Mode column and the detail dialog
 renders a per-step badge list; the Flows run detail badges each step from effective_steps.
 `flow template update OLD [--new-label NEW] ...` renames the label (flow history retargeted).
 `flow template clone SRC [--name] [--label]` duplicates one template (lineage in `parent_labels`);
 `flow template compose LABEL --from A,B [--name]` concatenates chains (starts preserved, but a start after the first cleanup is rejected; lineage recorded).
 Pre/post-execution are multi-line scripts; the details dialog highlights them via shebang detection.
 `flow run TEMPLATE REF [--steps JSON] [--step-config JSON] [--skip-steps 0[,N]] [--harness NAME] [--dry-run] [--fresh] [--yes] [--json] [--no-auto-register]` runs via `POST /api/runs`. Resume keeps the stored chain: passing a different `--steps` chain for a resumable run fails (exit 2) — use `--fresh` for the new chain or drop `--steps` to resume. When `--harness` is given, it applies to every step and overrides step-level `config.harness`. Unlinked PR/MR refs auto-register a worktree unless `--no-auto-register` is passed.
### `GET /api/links`
```json
{"trackers": {"jira:IPG": {"repos": ["/home/x/projects/projectx"]}},
 "worktrees": { …same shape as GET /api/status… }}
```
### `GET /api/messages?thread=` → `{"messages": [...]}`, `GET /api/messages/{ref}`
```json
{"messages": [{"id": 1, "message_id": "p1", "thread_id": "t1", "app_name": "mattermost",
  "sender_username": "u", "content": "deploy broke", "ts": "…",
  "repos": ["r1"], "related_repos": ["r2"], "worktrees": ["jira:X"], "added_at": "…"}]}
```
(`message add --worktree W...` links worktree refs alongside `--repo R...`;
`--related-repo R...` (Start-style) records extra linked repos whose paths
are appended to the run prompt body as `Related repos:` context — record-only,
no launch-time override. `--message-id` accepts a Mattermost permalink for
`resolve_message` lookups and is normalized to the bare id on write; `message
add --fetch --message-id <permalink> --repo R` fetches sender/content/ts/
thread via `mm` so the Register button needs only a permalink + repo.
Unregistered `message adhoc/validate/draft-reply` and `message action run` /
`message flow run` refs auto-register via the same `mm` fetch (single repo,
or the `--ref` worktree's repo when several repos exist — otherwise exit 2
naming `--repo` candidates). Runs keep a single `--repo` pin; the web repo
pin is singular (no fan-out). The detail endpoint 404s on unknown ref
(ambiguous substring → 404). `message adhoc/validate/draft-reply` and
`message action run` / `message flow run` (`--harness NAME` supported) run via `POST /api/runs` with target
key `message:<ref>` (draft-reply: `message:<ref>`; skills mm-reply-drafter +
persian-chat-generator: facts-only, never sends, ZWNJ-free ≤160-char draft)
live run per (subcommand, message).)

### `GET /api/default-repo?ref=…`
Issue #26: repo default for Launch (never the serve CWD). Returns the
repo of the worktree already linked to `ref` (rule 1), else the single
linked repo of the ref's tracker (rule 2), else `""` (multi/zero linked
repos — the picker has no default and `--repo` is required headless).
`repos` lists every linked repo of the ref's tracker (plus the
worktree-pinned default when outside it) for the Start repo picker:
```json
{"ref": "IPG-1", "repo": "/home/x/projects/projectx", "repos": ["/home/x/projects/projectx"]}
```

### `GET /api/review-session?ref=…`
Latest non-running `review` run for the ref's worktree key (fix-continue
target). Read-only, always 200 — nulls when the ref is unlinked or no review
run exists (caller falls through to a fresh `fix_comments` run). `run_id` is
the runs-table row id (`file_path` resolved through the `runs/` → legacy
`sessions/` fallback):
```json
{"ref": "jira:IPG-1", "run_id": 42, "file_path": "/home/x/.config/workagent/runs/omp/….jsonl"}
```

### `GET /api/doctor`
```json
{"status": "ok", "live_hash": "…", "recorded_hash": "…",
 "tools": {"git-wt": true, "agy-guard": true, "omp": true, "gh": true,
            "glab": true, "jira": true}, "receipt": "/home/x/.local/share/…"}
```

### `GET /api/issues`
Issues for the (window, scope) selection: `?days=1..365` (default 90),
`?mine=false` for the all-scope (default mine: jira `(reporter =
currentUser() OR assignee = currentUser())` any status + gh
author=@me ∪ assignee=@me deduped; all = unqualified search). The
initial (90d, mine) variant warms on page load; selections outside it
(Last year / All) requery under their own variant server-cache key —
one remote round max per selection. Per-source rows are cached
server-side and served within max(1h * 10, 1 day); `?force=true`
bypasses the cache and re-queries the tracker CLIs live (slow).
Always 200 — a missing/failing host CLI yields `[]` plus a `warning`:

### `GET /api/candidates`
Unlinked open PR/MRs across every registered repo (PR/MR URLs already
present in `links.json` are excluded server-side — normalized: trailing
slash and `.git` stripped — plus linked branch/worktree matches; the
repo's default→release MRs are excluded as release plumbing, never
review work) plus issues for the
(window, scope) selection (same query as `/api/issues`; days/mine
params accepted) plus unregistered on-disk worktrees under the scan
root. `?force=true` re-queries the tracker CLIs live instead of the
served cache window (slow). Read-only:
{"prs": [{"key": "github:o/r#33", "number": 33, "title": "…",
          "url": "https://github.com/o/r/pull/33", "branch": "feat/x",
          "target_branch": "main",
          "updated": "2026-09-20T10:00:00Z", "state": "OPEN",
          "author": "octocat",
          "repo": "o/r", "repo_name": "proj"}],
 "issues": [{"key": "jira:IPG-981", "title": "…", "url": "…",
             "status": "To Do", "created": "2026-09-20T10:00:00.000+0000",
             "reporter": "Younes Rahimi", "description": "…"}],
 "worktrees": [{"path": "/home/x/dev/worktrees/proj/feat/x",
               "repo": "proj", "branch": "feat/x",
               "key_guess": "branch:feat/x", "mtime": "2026-09-20T10:00:00+00:00"}],
 "warnings": ["proj: gh pr list failed: …"]}
 ```
(`repo_name` is the registered repo name for the Launch repo picker;
`target_branch` is the MR/PR base branch.)

Issue rows also carry `reporter` (jira) / `author` (github login) plus
`description` for the Recent-issues client-side filter: scope toggles
(My / All / My-Created, default My-Created on, All wins), Created-within
range dropdown (Last 7/30/90 days, Last year; default Last 30 days),
title/description search, status dropdown (default Not Done = open or in
progress) — all client-side over the loaded rows.

Per-tab slices (each loads independently so the slow live-PR fetch never
blocks the others; the Dashboard Candidates card queries all three in
parallel, never blocking on refetch — stale rows stay visible with an
overlay Updating badge in the top-right corner; skeletons render only on
first load):

### `GET /api/candidates` → `{prs, issues, worktrees, warnings}`
`issues` rows carry `repo_hint`: the Launch default repo for that ref
(linked worktree's repo, else the tracker's single linked repo, else
`""`). Shown in the Link page issue rows.

### `GET /api/trackers` → `{trackers: [{key, vendor, remote_url, fetch_enabled, is_enabled, repos}]}`
`is_enabled` 0 = disabled (skipped by issue/PR fetching); updates run
through `POST /api/runs` with `tracker update KEY [--vendor] [--remote-url] [--fetch/--no-fetch] [--enable/--disable]`.
### `GET /api/tracker-domains` → `{disabled_domains: [...]}`; `POST /api/tracker-domains` `{domains: [...]}` replaces the list (normalized lowercase/sorted; empty = all enabled). Trackers on a listed host (or subdomain) are skipped by fetching.
### `GET /api/release/status?name=` → `{repo, path, default_branch, release_branch, diff_count, behind, releasable, reason, stale, ci, ci_url, fetched_at, releasing, dirty, dirty_files, has_remote_diff}`
Read-only releasability for one repo (1h fresh / 1d SWR diff cache). Always serves the cached row instantly and revalidates the diff in the background -- the ReleaseCard polls every 30 s, so fresh diffs swap in on the next poll without blocking or clearing the rendered rows. `releasable` = release branch set + diff > 0. `behind` = commits the release branch has that default lacks (local rev-list, 0 on unknown refs: drives the Fix-default button, never a gate); `ci`/`ci_url` are the release branch's newest pipeline (`success|failure|running|not_started`, empty when the lookup soft-fails); `fetched_at` is the diff-cache stamp. `releasing` is True while the repo's release lock is held (a release is running). `dirty` is True when the local checkout has uncommitted changes (`dirty_files` lists the `git status --porcelain` lines), disabling the ReleaseRow Pull button with a `(!)` tooltip; `has_remote_diff` is True when local `HEAD` differs from its `origin/<branch>` tracking ref (False hides the Pull button when local and remote match); when shown and clean, Pull runs `createRun({command: "repo", args: ["pull", repo]})`. `POST /api/runs` `{command: "release", args: [repo, "--yes"]}` runs the release (confirm dialog appends `--yes`). New flags: `--checks ci|local|both|none` (default `ci`: local pre-release gate in a detached worktree at the pinned SHA, pinned-commit CI poll, or both), `--diff remote|local` (default `remote`), `--pre-release-timeout` (default `10m`), `--ci-timeout` (default `15m`), `--env-file <script>` (default `env_file` in `config.json`; sourced before the pre-release gate). Result summary: `{repo, pinned_sha, checks, strategy, mr_url, merged}` + stderr `released <sha> via <strategy> checks=<checks> <url>`. `release fix-default REPO [--action rebase|ci|both] [--run-mode preview|headless|terminal] [--harness NAME] [--extra-prompt] [--dry-run] [--json] [--yes]`: rebase default onto release (remote rebase, harness fallback rewriting origin/default with force-push) and/or fix failing CI via the harness (checks both the default and the release branch pipelines; `release_ci`/`release_ci_url` carry the release-branch result); no-op (exit 0) when `behind` = 0 and neither branch CI is failing. The ReleaseCard shows a "Fix default" button per releasable repo row when `behind > 0 || ci === "failure"`; its dialog (action segmented Both/Rebase/CI default Both, run-mode control, `<HarnessPicker>`, extra-prompt input, force-push warning for rebase/both) runs `createRun({command: "release", args: ["fix-default", repo, "--action", a, "--run-mode", m, ...extra]})` (`release` also accepts `--harness <name>`).
- `start REF [--register-only]` creates/registers the worktree and checks preconditions without launching an AI harness (used by Launch/New Task `Register` tab and Candidate Issues/PR/MR `Register` buttons).
## Runs (mutating CLI commands as child processes)
### `POST /api/runs` → 202
Request:
```json
{"command": "cleanup", "args": ["IPG-932"],
 "confirm": true, "force": false}
```
 - `action suggest-icon [--name --label --prompt --pre-execution --post-execution --used-icons --harness --json]` / `flow template suggest-icon [--name --label --steps --pre-execution --post-execution --used-icons --harness --json]`: Ask-AI Lucide icon (issues #62, #84) — ONE bare judge-style harness session (temp dir, no checkout; uses `Harness.judge_argv`, e.g. `agy-guard --model gemini-3.8-flash-low --disable-slash-commands --mode plan` or `omp --smol --no-skills --no-tools ...`), stdout `{"icon": "Rocket"}`; target keys `suggest-icon:action` / `suggest-icon:flow` so long AI runs never 409-block saves; the prompt lists all 1611 Lucide icons (pinned lucide-react 0.525.0 registry) plus the already-used icons of other actions/templates (explicit `--used-icons` unioned with the store-collected fallback) so the model avoids duplicates; parsed against `SUGGEST_ICON_CHOICES`, never confirmed.
 - `command`: one of `start | review | cleanup | release | sync | open | register |
   repo | link | tracker | action | flow | message | backup | prodtest | adhoc | orchestrate | config` (subcommand goes first in `args`: `["add", "--name", …]`; flow template CRUD nests two deep: `["template", "add", …]`; prodtest: `["plan", REF, …]` / `["run", REF, …]`; adhoc: `[REF, --prompt P, …]`; orchestrate: `[--repo R…, --prompt P, …]` (single-flight key `orchestrate:<repos>:<prompt-hash>`); config: `["show"]` or `["set", "default-harness|default-model|default-effort", VALUE]`, non-destructive under target key `"config"`).
 - `sync [REF]` accepts `--rebase` (default: remote rebase; mutually exclusive with `-m/--merge`), `--harness <name>` (selects the conflict-resolution harness; default: configured `default_harness`) and `--no-auto-register` (skip auto-registering an unlinked PR/MR worktree). `agy` (`agy-guard`) is the default AI harness (`DEFAULT_HARNESS = "agy"`): headless uses `agy-guard --dangerously-skip-permissions --output-format stream-json -p=<prompt>` and preview/interactive uses `agy-guard -i=<prompt>` (or bare `agy-guard`). `adhoc` also accepts `--no-auto-register`.
- Global `-v` may be passed as `args[0]`.
- `start`/`review`/`sync`/`release` take `--env-file <script>`: a shell script sourced before every harness command including the preview (`source <file> && …`; headless children also get it parsed into their environment) and before the release pre-release gate. Default: `env_file` in `config.json` (auto-created as `<config_dir>/env.sh` on first launch with `VISUAL`/`EDITOR`/`NO_PROXY`/`PI_EDIT_VARIANT` defaults). Delete the file to opt out; run resume reuses the originating run's explicit `--env-file` or the configured default.
- Destructive (`cleanup`, `sync` w/o `--dry-run`, `start`, `review`, `adhoc`, `orchestrate`,
  `action run`/`action delete`, `flow run`/`flow template delete`) need
  `confirm: true` → the server appends `--yes`. `action add`/`update` and
  `flow template add`/`update`/`clone`/`compose` are plain row writes — no confirm needed.
  `force: true` requires `confirm: true` and appends `--force` (cleanup, register, repo remove).
  `cleanup --released` (optional ref; Dashboard "Close released" button):
  closes the tracker issue (`done`) for links whose branch tip is an
  ancestor of `origin/<release_branch>`; worktrees/branches/PRs untouched.
  Best-effort per link (`closed | skipped:* | failed`), never aborts the loop.
  `cleanup <ref> --merge-only` (Dashboard "Merge PR only" button):
  squash-merges the open PR/MR (merge commit for long-lived sources);
  worktree, branch, and link all survive.
- Lifecycle status: `start`/`review`/`release` update the tracker issue
- `repo remove` of a repo with linked worktrees → 400
  `{code: "worktrees_exist"}` unless `force: true` (cascades them).
  `tracker add` without a non-blank `--remote-url` → 400 `bad_arg`.
  `start`/`review` with no `--repo` when the ref's tracker is linked to
  multiple repos → 400 `{code: "repo_ambiguous"}` naming the candidates
  (never a run — the Launch form also blocks submit until a repo is picked).
- `start`/`review` `--terminal` is XOR `--launch` (child CLI exits 2 when
  both are passed). `--terminal` records a preview run row, claims
  the harness lock with `origin=terminal` (pid = spawned terminal
  emulator), and prints `terminal_pid` in the run's JSON output; the
  agent is stopped via `POST /api/terminal/stop` with the worktree ref.
- `start` takes repeatable `--related-repo <name>` (one child worktree per
  repo on the same branch; the Launch Start form multi-selects from every
  registered repo except the main one).
- `start --plan` (issue #88): plan mode — the harness prompt forbids
  implementation and instructs the agent to brainstorm/grill the user,
  write spec + plan MD files, push and open a PR/MR, then stop. Without
  `--plan` the prompt tells the agent to read existing spec/plan files
  first. The Launch Start form exposes an Implement/Plan segmented toggle
  (persisted in launch prefs); flow `start` steps accept `plan: true` config
  (FlowWizard/ChainEditor checkbox).
- `link destroy-deleted [REF] [--all] [--dry-run] [--json]`: permanently destroys
  soft-deleted rows INCLUDING surviving local state — worktree dir teardown,
  local branch delete, AI harness transcript folders deleted from
  disk, then the run rows drop. Remote branches are NOT
  touched. `link remove --hard-delete` purges the same transcripts. Single-ref or `--all`; missing `--repo` scoping
  by design. The Dashboard Deleted table runs it per-row (trash icon) and
  per-selection (Destroy (N) button) with the same irreversible confirm.
- `link purge-orphans [--dry-run] [--json]`: deletes records whose worktree
  row no longer exists — dangling runs (pre-cascade leftovers) plus their
  recorded harness transcripts, flows, message worktree links, and child
  `parent_key_ref` labels. Runs automatically in serve startup maintenance
  and reports `{"orphans": {runs, flows, messages, parent_links,
  transcripts}}`. Prodtest items keep their issue-scoped `worktree_ref` on
  purpose (post-teardown attribution).
- Hard-delete cascade (issue #108): `runs.worktree_ref` is an optional FK to
  `worktrees(ref_key)` (`ON DELETE CASCADE`; NULL = repo-scoped run, `""` in
  API payloads), so every destroy path removes the worktree row with its
  runs, flows, message links, and child parent labels.

Response 202: `{"run_id": "abc123"}` — `409 {code:"conflict"}` when
another run holds the same target key.

### Target keys (409 collisions)
`start`, `review:<ref>` / `review:all` (`--all`, plus `--force-all` to
re-include already-reviewed and unresolved-comment worktrees),
`cleanup:<ref>` / `cleanup:all`, `action:<label>:<ref>` / `config` (template CRUD),
`flow:<template>:<ref>` / `config` (template CRUD, `config show`/`config set`),
`prodtest:<ref>` / `prodtest:run-all` — one prodtest plan/run per issue ref,
`adhoc:<first-ref>` — one adhoc run per ref. `prodtest plan <ref>` generates
the plan; `prodtest run <ref>` runs it at its release SHA (issue-key first,
worktree ref as accelerator); `prodtest list` / `prodtest status <ref>` are
read endpoints for the pages below. `prodtest run --all` runs every
released/stored plan sequentially in-process
(dry-run rows / `skipped:<reason>` / continue-past-failure).
`prodtest delete <ref> [--json]` drops one prodtest row; `prodtest cleanup
[--yes] [--dry-run] [--json]` drops every `passed` row (Prodtests page
per-row Delete + Cleanup-passed buttons; requires `--yes` non-TTY).

### Prodtests pages (issue #50: decoupled from worktree dev flow)
`GET /api/prodtests` → `{"prodtests": [...]}` newest first (Prodtests page
source; same storage as `prodtest list`). Each row: `issue_key`, `status`
(`pending` = plan generated, no release yet; `pending_prodtest` = release
SHA recorded, awaiting run; `running` = lock held by a live run; `passed` /
`failed` = last run verdict; `blocked` = run could not complete),
`plan_path`, `plan_revision`, `plan_risk`, `release_sha`, `last_run_id`,
`worktree_ref` (survives cleanup via `cleanup --create-prodtest`), and
`run_results` (JSON verdict payload). `GET /api/prodtests/{ref}` → one item
(404 `unknown prodtest item`). `/prodtests` (badge-link target of the
Dashboard prodtest cell; `?issue=` pre-filters) owns Plan/Run buttons; the
separate `/prodtest-launch` page owns the plan/run form (empty ref + Run =
`run --all`). `cleanup <ref> --create-prodtest` keeps the prodtest row while
tearing down the worktree; the run-time binding check (not cleanup) catches
stale plans.

### `GET /api/runs` → list
```json
[{"id": "abc123", "command": "sync", "args": ["IPG-932"],
  "state": "running", "exit_code": null, "truncated": false,
  "created": 1730000000.0, "target": "sync:jira:IPG-932",
  "session_file": "/home/x/.config/workagent/runs/agy/….jsonl",
  "transcript_ready": true,
  "session_type": "sync", "worktree": "/home/x/wt",
  "harness_name": "agy",
  "resume_command": "cd /home/x/wt && agy-guard --conversation …",
  "last_seq": 42,
  "parent_run_id": null,
  "step_index": null,
  "finished": 1730000012.5,
  "duration_ms": 12500,
  "model_id": "claude-sonnet-4",
  "effort": "high",
  "input_tokens": 1200,
  "output_tokens": 450,
  "cached_tokens": 300,
  "cost_usd": 0.0145}]
```
Run telemetry fields (`finished`, `duration_ms`, `model_id`, `effort`,
`input_tokens`, `output_tokens`, `cached_tokens`, `cost_usd`) are persisted
on `runs` in `state.db` when a run finishes (issue #155): `finished` is the
epoch-second completion timestamp (`null` while running), `duration_ms` is
elapsed milliseconds (`null` while running), `model_id`/`effort` record the
selected or reported model/effort (`""` when unset), and token/cost fields
aggregate `usage` events from ACP JSONL and headless stream-json transcripts
(`null` when unreported).
`session_file`/`worktree` are non-empty only for runs that executed a
harness transcript (`start`/`review`, or `sync` with an explicit
`--session-file`); the web server injects `--session-file` for
`start`/`review` launches — including the default preview (no `--launch`;
the CLI preview pins the path on the run row it records, so the first run
command already carries the full path and future runs of the same
transcript reuse it) — except `--all` runs, which
carry no `--session-file` (the CLI rejects it with `--all`; each
per-worktree child generates its own transcript). Clients show resume
buttons iff `session_file` is present. `transcript_ready` (bool) says the
stored path is a non-empty `.jsonl` on the server — a preview row's path is
only the `--session-dir` scope hint, so it is `false` and the copy button is
gated on it (resume resolves the minted file itself). `harness_name` is the
resolved harness (`"agy" | "omp"`, falling back to `store.default_harness()`).
`resume_command` is the server-computed shell command (`""` when not yet
resumable) built via `Harness.resume_argv` (`agy-guard --conversation <id>`
for `agy` or `omp --resume <session_file>` for `omp`, wrapped with the env
file). For `agy`, `agy-guard` streams NDJSON and mirrors
`brain/<cid>/.system_generated/logs/transcript.jsonl` into `runs/agy/….jsonl`
prefixed with an `agy_meta` header containing `conversation_id`.

`session_type` is the stored transcript kind
(`start|review|sync|fix_comments|custom_action|flow|prodtest_plan|prodtest_run`,
null for rows recorded before the column existed) — the Runs UI label. It is
authoritative over the command: a persisted fix-comments run carries
`command="review"` with the flags dropped, so only the type tells it apart
from the review it fixes.

Serve-persisted runs (`db-<id>`) are the CLI invocations persisted at
spawn with progressive output updates (throttled ~2s) and finalized at
finish — so history survives server restarts. Each web invocation persists
exactly one row, including preview/terminal runs and failed runs (exit
code set on finish; NULL while running); a headless child's own launch row
is adopted/finalized, never duplicated.

### `GET /api/runs/{id}` → detail (adds `lines`)
Includes every `Run` field (`harness_name`, `resume_command`, `transcript_ready`, etc.) plus `lines`: `[{"seq": 1, "text": "syncing jira:IPG-932 …"}, …]`
(ANSI already stripped server-side). Post-restart rows (`db-<id>`) replay
the DB-persisted output (last 2000 lines, `truncated` when the live buffer
had dropped older lines) — the log survives `serve` restarts.

### `GET /api/harnesses/{name}/options` → model/effort discovery (issue #112)
ACP-discovered options via `<bin> acp` (`initialize` + `session/new` in a
temp cwd, 10-min server TTL). 404 unknown harness (`{"error": {"code":
"not_found"}}`); 200 + `supported: false` for non-ACP harnesses (no probe);
200 + `warning` when the probe fails. Shape:
`{"harness", "supported", "models": [{"id", "name"}], "efforts": [...],
"current_model", "current_effort", "cached"}` (`warning` only on probe
failure). `?refresh=1` bypasses the TTL cache and re-probes the harness
(`cached` is false on the re-probed response). The UI disables the
Model/Effort selects when `supported` is
false or the lists are empty.

### `POST /api/runs/{id}/follow-up` → `202 {"run_id", "continued_from_run_id", "worktree_ref"}` (issue #144)
Spawns a linked continuation run (`workagent adhoc <ref> --prompt <prompt> --follow-up --continue-run <id> --run-mode <mode> --yes`) on the same worktree/session without weakening `POST /api/runs/{id}/input`'s strict `pending_question` guard. Body: `{"prompt": str, "harness"?: str, "run_mode"?: "preview"|"headless"|"acp"|"terminal", "model"?: str, "effort"?: str}`. Errors: `422` blank prompt, `404` unknown run or no associated worktree, `409` active run on target `adhoc:<ref>`.

### `POST /api/runs/{id}/input` → `{"id", "ok": true}` (issue #112)
Append a user answer to a running run's `input.jsonl` (the ACP run loop
tails `WORKAGENT_RUN_DIR/input.jsonl` from offset 0, consuming each line
once, qid-correlated). 404 unknown run,
409 finished run (`not_running`), 409 when no question is pending
(`no_pending_question`), 409 qid mismatch (`qid_mismatch`), 422 empty text
(`bad_arg`). Body: `{"text": "...", "qid": "..."}` — `answer` and `choice`
are accepted as aliases for `text` (the payload persists canonical `text`
plus `answer`, and the qid so the run loop can
correlate the answer). A pending ACP question surfaces as
`pending_question` (`{"qid", "text", "options", "multi"}` — dict-shaped
options normalized to label strings — or null) on
`GET /api/runs` rows and `GET /api/runs/{id}` detail — parsed from the
newest unanswered `ACP: {"t": "question", ...}` log line. `pending_question`
is null whenever the run is not running. A co-emitted
non-waiting `turn_end` from the same unanswered turn does not clear it;
only an `answer` event resolves it.

### `GET /api/events` — Global CDC invalidation SSE stream (issue #147)
Streams real-time SQLite `change_log` events (`worktrees`, `runs`, `flows`,
`messages`, `pr_cache`, `prodtest_items`) tailed every 250 ms.
- `event: change`, `id: <seq>`, `data: {"seq": N, "table": "worktrees", "row_key": "…", "op": "insert"|"update"|"delete", "created_at": 1700000000.123}`.
- Replays missed events when `Last-Event-ID` header (or `?last_event_id=N`) is supplied.
- Emits `: heartbeat` SSE comments every 15 s while idle; closes cleanly on server shutdown.

### `GET /api/runs/{id}/events` — SSE
- `event: log`, `data: {"seq": N, "text": "…"}`, `id: N`. For `db-<id>`
  rows the stream replays the stored lines then the terminal state event.
### `POST /api/runs/{id}/cancel` → `{"id": …, "state": "cancelled"}`
409 when the run already finished. Server sends SIGTERM to the detached
process group (persisted pgid in the per-run state dir), SIGKILL after 10 s.
The cancel path is ticks-guarded: a stale pid file pointing at an unrelated
live process is never killed — the run is marked `failed` instead.
### Detached runs (survive `serve` restarts)
Every `POST /api/runs` child runs detached: `serve` spawns
`workagent _run-supervised --run-dir <dir> <argv>`, which double-detaches
the real child (own pgid), streams stdout/stderr straight to
`<config_dir>/runs/_web/<id>/output.log` (1 MiB head-truncation cap,
`truncated` marker file + `truncated: true` in list/detail rows; RAM ring
stays 10k lines), and writes `pid` (pid + start-ticks) / `exit_code` /
`meta.json`. The `_web/` subdir keeps run state disjoint from the transcript
tree (`runs/<harness>/…`), so nothing is recognized by a meta.json/pid
heuristic. `serve` only tails the log file. At startup `serve` rehydrates live run
dirs into a fresh registry (log backfill + tail continuity); finished-run
dirs are pruned to the newest 100, running ones never. Only the
supervisor writes `output.log`. The systemd unit uses `KillMode=process`
so stop/re-install never kills detached children.

### `POST /api/runs/{id}/resume` → `{"id": …, "session_file": …, "worktree": …}`
When the pinned `--session-file` path is missing/empty (first command run
manually outside the web server), the endpoint adopts the transcript: for
`agy`, it resolves `conversation_id` from run metadata, `agy_meta`, or the
workspace in `$ANTIGRAVITY_HOME` (`conversation_summaries.db` / `brain/<cid>`)
and mirrors it into `session_file`; for `omp`, it adopts the newest non-empty
`.jsonl` in the scope dir minted after the run row was created, persists it
on that row, and resumes that transcript — so the first run stays resumable
and later runs reuse the full path.
Non-destructive (same class as `open`): no confirm needed. Detached-spawns the OS default terminal running `cd <worktree> && <harness resume_argv>` (`agy-guard --conversation <id>` for `agy`, `omp --resume <session_file>` for `omp`, routed through `Harness.resume_argv`; `$TERMINAL` → `xdg-terminal-exec` → gnome-terminal/konsole/xfce4-terminal/xterm; macOS `open -a Terminal`; Windows `cmd /k`). Terminal spawns claim the harness lock with `origin=terminal` (pid = spawned terminal emulator) and pre-spawn 409 `{code:"conflict"}` when a live harness already holds the worktree, so no orphan terminal launches; sources the originating run's `--env-file` (or the configured default). 404 `no_session` when the run
names no harness transcript, `missing_session` when the transcript is
absent, `no_worktree` when the target has no recorded worktree.

### `POST /api/runs/{id}/start-terminal` → `{"id": …}`
Same spawn running the run's last logged `harness command:` entry — the exact command behind the Run page "Copy harness command" button (the button is disabled exactly when this returns 404 `no_harness_command`). When a DB row, worktree, and `session_file` exist, wraps the command in `workagent watch-and-exec --run-id <id> --harness <name> ...` so the spawned terminal's minted transcript/conversation ID is captured back onto the run row (mirroring `agy` brain transcripts via `agy-guard` or `omp` `.jsonl` files). Claims the harness lock with `origin=terminal` and pre-spawn 409s `{code:"conflict"}` on a live lock, like the resume endpoints.

### `POST /api/worktrees/{ref}/terminal` → `{"id", "session_file", "worktree", "mode"}`
Spawns a bare harness (`agy-guard` or `omp`, via `Harness.bare_terminal_argv`) in the OS terminal for a worktree (no prompt). Body: `{"mode": "harness" | "prodtest", "harness"?: string, "model"?: string, "effort"?: string}` (`harness` optional, defaults to `store.default_harness()`, validated via `backend.get_harness` → 400 `bad_arg` on unknown harness; `model`/`effort` thread into `Harness.bare_terminal_argv`). Records one `preview` run row with the pinned transcript path (`session_type` from `mode`), wraps the spawn in `workagent watch-and-exec` for transcript/conversation capture, and claims the terminal lock (409 `conflict` if a live harness holds the worktree).

### `POST /api/terminal/stop` → `{"worktree_ref", "stopped": true}`
SIGTERM→SIGKILL the terminal-origin agent behind a worktree ref (the `--terminal` run mode). Body: `{"worktree_ref": "<key>"}`. 404 `not_found` when no live terminal lock (missing, dead-pid swept, or headless origin — headless runs are never stoppable here).

## Run states
`running` → `succeeded` (exit 0) | `failed` (exit 1 or other non-zero)
| `needs_input` (exit 2) | `cancelled`. A `needs_input` run shows its
captured stderr and offers **Re-run** (same command/args, `confirm`
preset as before).

## Client conventions
- TanStack Query for reads; poll `GET /api/status` every 15 s while the
  tab is visible and after any run finishes (invalidate the query).
- EventSource for the run log viewer (native `Last-Event-ID` resume on
  reconnect is built into EventSource).
- Theme: `light | dark | system` in localStorage, follows
  `prefers-color-scheme` for `system`.
- Launch prefs: localStorage key `launch.prefs.v1` — per-tab `{start:{runMode,base},review:{runMode,thenFix,fixAction,useReviewSession},sync:{merge}}` plus Prodtest Launch's `{prodtest:{runMode,prodtestAction}}` under the same key; only the active tab's slice persists on change, URL params (`ref`, `fixComments=1`) win for the live form and never persist. Start base/action/run-mode additionally stick per repo under `launch.startByRepo.v1` — picking a repo reloads its saved values. Repo tabs (`useRepoTabs`) show only repos with count > 0; a stale `?repo=` falls back to All. Candidates Review rows deep-link to `…/launch?mode=review&ref=<pr-url>` with the repo pre-selected. The run-mode segmented control labels are `Preview | Headless | ACP | In terminal` (no `Run` prefix).

---

## Prodtest YAML Plan Inspection (T08)

Read-only endpoints (all GET, no confirm):

- `GET /api/prodtests/{issue_key}/plan` — returns `{issue_key, status, plan_path, plan_revision, plan_risk, plan_diff_hash, release_sha, last_run_id, updated_at, truncated, yaml, parse_error, summary, preconditions, cases, metadata}`. `404` when the prodtest item or plan file is missing; `400` when `plan_path` is not a `.yaml`/`.yml` file.

### Worktree Watcher Overrides (`GET` / `POST /api/worktrees/{ref}/watcher`)
- `GET /api/worktrees/{ref}/watcher` → `{ key, auto_fix_ci, auto_fix_reviews, effective_auto_fix_ci, effective_auto_fix_reviews, last_nudge_signature }` (`auto_fix_*` is `true | false | null`, where `null` inherits from the repo).
- `POST /api/worktrees/{ref}/watcher` with JSON `{ "auto_fix_ci"?: bool | null, "auto_fix_reviews"?: bool | null }` persists or clears per-worktree overrides.
- `GET /api/repos` includes `auto_fix_ci` and `auto_fix_reviews` booleans; `repo update` accepts `--auto-fix-ci/--no-auto-fix-ci` and `--auto-fix-reviews/--no-auto-fix-reviews`.
- `GET /api/repos` includes `ci_ignore` (string array, default `[]`): glob patterns for side-CI pipelines/jobs/checks excluded from kanban CI status and the release CI gate; `repo update --ci-ignore "trivy:*,security/*"` sets it (empty string clears), and the Repos page per-row "CI ignore" editor edits it.
- `watch` command accepts `--once`, `--dry-run`, `--json`, `--interval`, `--repo`.

---

## Recurring Cron/RRULE Schedules (T13)

- `GET /api/schedules` — returns `{"schedules": [...], "starters": [...]}` (all persisted schedules plus built-in starter templates).
- `POST /api/schedules/preview` — body `{"cron_expr": string, "timezone"?: string, "count"?: number}` → `{"cron_expr", "timezone", "next_runs": string[]}` (`400` on invalid cron/RRULE or timezone).
- `GET /api/schedules/{id}` — one schedule by id or name (`404` when unknown).
- `POST /api/schedules` — create a schedule (`name`, `cron_expr`, `timezone`, `target_kind`: `flow|action|command`, `target_label`, `args_json`, `enabled`).
- `PATCH /api/schedules/{id}` — update mutable fields and recompute `next_run_at` when `cron_expr`/`timezone` changes.
- `DELETE /api/schedules/{id}` — delete a schedule (`{"removed": true, "id": ...}`).
- `POST /api/schedules/{id}/run` — trigger a schedule immediately via the run registry and record `last_run_at`, `next_run_at`, and `last_run_id`. Destructive targets require `{"confirm": true}` (`400 confirm_required` otherwise); serve-loop dispatches launch through the run registry (`--yes` appended for `start/review/cleanup/sync/release` unless already present).
- RRULE subset: `FREQ` (`HOURLY/DAILY/WEEKLY/MONTHLY`), `INTERVAL` (`=1` for non-`HOURLY`), `BYMINUTE`/`BYHOUR`/`BYDAY` (weekly)/`BYMONTHDAY` (monthly). `INTERVAL>1` for `DAILY/WEEKLY/MONTHLY` and any other key (`COUNT`/`UNTIL`/`DTSTART`/…) are `400` — bounded/anchored rules must not silently run forever. A failed `next_run_after` recompute keeps the old `next_run_at` and surfaces `status: error` (no silent wedge).
- CLI parity: `workagent schedule add|list|update|remove|run-due` mirrored in `web_args.py` (`schedule:run-due` target key, `confirm_subs: ("run-due",)`).
