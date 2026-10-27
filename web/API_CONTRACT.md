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
                   "author": "…", "url": "https://…", "tool": "glab"}
}
```
`harness` is `"<name> <pid>"` while a harness is live on the worktree,
`""` otherwise. `commits` is the display string `"B|A"`; prefer
`commits_detail`.
`pr_detail` is `null` when no open/known PR. `ci` is the latest CI
pipeline status for the PR — `success` | `failure` | `running` |
`not_started` — or `null` when there is no PR or the lookup failed
(cached within max(10 min * 10, 1 day) while the branch tip is unchanged). `ci_url` is the
latest CI job page (GitLab `.../-/jobs/<id>`, GitHub check details URL)
or `""` when unknown — the CI badge links there. `reviews` is the
display string `"R|U|R"` (done reviews | unresolved | resolved);
`reviews_detail` is `{"reviews", "unresolved", "resolved"}` or
`null` when there is no PR or the lookup failed (same 10-min/tip cache).
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
  "created_at": "…", "modified_at": "…"}]}
```
(CRUD page source: `action add/list/show/update/delete` run as runs; detail 404s
on unknown label. `action update OLD [--new-label NEW] ...` renames the label (flow steps cascade). Pre/post-execution are multi-line scripts; the details dialog highlights them via shebang detection. `action run <label> <ref>` runs via `POST /api/runs`
with target key `action:<label>:<ref>` — one run per worktree, fanned out
by the Dashboard Actions dropdown.)

### `GET /api/flow-templates` → `{"templates": [...]}`, `GET /api/flow-templates/{label}`, `GET /api/flows?worktree=`
```json
{"templates": [{"id": 1, "label": "full-review-cycle",
  "name": "Full review cycle",
  "steps": [{"kind": "start"}, {"kind": "review"}, {"kind": "cleanup"}],
  "pre_execution": "", "post_execution": "",
  "created_at": "…", "modified_at": "…"}]}
```
(Flow page source: `flow template add/list/show/update/delete` run as runs; detail 404s
 on unknown label. `flow template update OLD [--new-label NEW] ...` renames the label (flow history retargeted). Pre/post-execution are multi-line scripts; the details dialog highlights them via shebang detection. `flow run TEMPLATE REF [--step-config JSON] [--skip-steps 0[,N]] [--dry-run] [--fresh] [--yes] [--json]` runs via `POST /api/runs`
 with target key `flow:<template>:<ref>` — one run per template+worktree pair; `--skip-steps` marks the paused resume step done-without-running (already finished by hand in the spawned terminal) and advances the cursor; `flow list [--worktree REF] [--json/--csv]`
 and `flow show ID [--json]` run as runs. `GET /api/flows?worktree=` scopes the run list to one worktree.)
### `GET /api/links`
```json
{"trackers": {"jira:IPG": {"repos": ["/home/x/projects/projectx"]}},
 "worktrees": { …same shape as GET /api/status… }}
```

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
Latest non-running `review` session for the ref's worktree key (fix-continue
target). Read-only, always 200 — nulls when the ref is unlinked or no review
session exists (caller falls through to a fresh `fix_comments` session):
```json
{"ref": "jira:IPG-1", "session_id": "2026-09-26T00-00-00-000Z-1234", "file_path": "/home/x/.config/workagent/sessions/omp/….jsonl"}
```

### `GET /api/doctor`
```json
{"status": "ok", "live_hash": "…", "recorded_hash": "…",
 "tools": {"git-wt": true, "omp": true, "gh": true, "glab": true,
            "jira-cli": true}, "receipt": "/home/x/.local/share/…"}
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

### `GET /api/sessions` → list (newest first, no prompts)
```json
{"sessions": [{"id": "2026-09-22T10-00-00-123Z-4567",
  "worktree_ref": "jira:IPG-932", "harness_name": "omp",
  "initiator_command": "start", "session_type": "start",
  "metadata": {}, "state": "finished",
  "created_at": "2026-09-22T10:00:00.123Z",
  "file_path": "/home/x/.config/workagent/sessions/omp/2026-09-22T10-00-00-123Z-4567.jsonl"}]}
```

### `GET /api/sessions/{id}` → detail (prompt + session runs)
```json
{"id": "…", "worktree_ref": "jira:IPG-932", "harness_name": "omp",
 "initiator_command": "start", "state": "finished", "prompt": "…",
 "created_at": "…", "file_path": "…",
 "transcript": "missing",
 "runs": [{"id": 1, "command": "start", "args": ["IPG-932", "--yes", "--session-file", "…"],
           "exit_code": 0, "created_at": "…"}]}
```
Unknown id → 404. `transcript` is `"missing"` when the per-session
`.jsonl` file is absent. `runs` holds the serve-mirrored CLI invocations
for session-linked runs (those launched with a session file), mirrored at
run completion — so history survives server restarts. Non-session runs are
never mirrored (live registry only).

### `GET /api/candidates` → `{prs, issues, worktrees, warnings}`
`issues` rows carry `repo_hint`: the Launch default repo for that ref
(linked worktree's repo, else the tracker's single linked repo, else
`""`). Shown in the Link page issue rows.

### `GET /api/trackers` → `{trackers: [{key, vendor, remote_url, fetch_enabled, is_enabled, repos}]}`
`is_enabled` 0 = disabled (skipped by issue/PR fetching); updates run
through `POST /api/runs` with `tracker update KEY [--vendor] [--remote-url] [--fetch/--no-fetch] [--enable/--disable]`.
### `GET /api/tracker-domains` → `{disabled_domains: [...]}`; `POST /api/tracker-domains` `{domains: [...]}` replaces the list (normalized lowercase/sorted; empty = all enabled). Trackers on a listed host (or subdomain) are skipped by fetching.
### `GET /api/release/status?name=` → `{repo, path, default_branch, release_branch, diff_count, behind, releasable, reason, stale, ci, ci_url, fetched_at, releasing}`
Read-only releasability for one repo (1h fresh / 1d SWR diff cache). Always serves the cached row instantly and revalidates the diff in the background -- the ReleaseCard polls every 30 s, so fresh diffs swap in on the next poll without blocking or clearing the rendered rows. `releasable` = release branch set + diff > 0. `behind` = commits the release branch has that default lacks (local rev-list, 0 on unknown refs: drives the Fix-default button, never a gate); `ci`/`ci_url` are the release branch's newest pipeline (`success|failure|running|not_started`, empty when the lookup soft-fails); `fetched_at` is the diff-cache stamp. `releasing` is True while the repo's release lock is held (a release is running). `POST /api/runs` `{command: "release", args: [repo, "--yes"]}` runs the release (confirm dialog appends `--yes`). New flags: `--checks ci|local|both|none` (default `ci`: local pre-release gate in a detached worktree at the pinned SHA, pinned-commit CI poll, or both), `--diff remote|local` (default `remote`), `--pre-release-timeout` (default `10m`), `--ci-timeout` (default `15m`), `--env-file <script>` (default `env_file` in `config.json`; sourced before the pre-release gate). Result summary: `{repo, pinned_sha, checks, strategy, mr_url, merged}` + stderr `released <sha> via <strategy> checks=<checks> <url>`. `release fix-default REPO [--action rebase|ci|both] [--run-mode preview|headless|terminal] [--extra-prompt] [--dry-run] [--json] [--yes]`: rebase default onto release (remote rebase, harness fallback rewriting origin/default with force-push) and/or fix failing default CI via the harness; no-op (exit 0) when `behind` = 0 and CI is not failing. The ReleaseCard shows a "Fix default" button per releasable repo row when `behind > 0 || ci === "failure"`; its dialog (action segmented Both/Rebase/CI default Both, run-mode control, extra-prompt input, force-push warning for rebase/both) runs `createRun({command: "release", args: ["fix-default", repo, "--action", a, "--run-mode", m, ...extra]})`.
## Runs (mutating CLI commands as child processes)
### `POST /api/runs` → 202
Request:
```json
{"command": "cleanup", "args": ["IPG-932"],
 "confirm": true, "force": false}
```
- `command`: one of `start | review | cleanup | release | sync | open | register |
  repo | link | action | flow` (subcommand goes first in `args`: `["add", "--name", …]`; flow template CRUD nests two deep: `["template", "add", …]`).
- Global `-v` may be passed as `args[0]`.
- `start`/`review`/`sync`/`release` take `--env-file <script>`: a shell script sourced before every harness command including the preview (`source <file> && …`; headless children also get it parsed into their environment) and before the release pre-release gate. Default: `env_file` in `config.json` (auto-created as `<config_dir>/env.sh` on first launch with `VISUAL`/`EDITOR`/`NO_PROXY`/`PI_EDIT_VARIANT` defaults). Delete the file to opt out; session-resume endpoints reuse the originating run's explicit `--env-file` or the configured default.
- Destructive (`cleanup`, `sync` w/o `--dry-run`, `start`, `review`,
  `action run`/`action delete`, `flow run`/`flow template delete`) need
  `confirm: true` → the server appends `--yes`. `action add`/`update` and
  `flow template add`/`update` are plain row writes — no confirm needed.
  `force: true` requires `confirm: true` and appends `--force` (cleanup, register, repo remove).
- `cleanup` merges open PRs/MRs with the CLI rule: non-long-lived sources squash-merge into default/protected branches; long-lived sources (`dev/develop/main/master`) merge with a merge commit; explicit `--no-squash` on other sources fails (exit 2, in the run log). The web UI never sends `--no-squash`.
- `repo remove` of a repo with linked worktrees → 400
  `{code: "worktrees_exist"}` unless `force: true` (cascades them).
  `tracker add` without a non-blank `--remote-url` → 400 `bad_arg`.
  `start`/`review` with no `--repo` when the ref's tracker is linked to
  multiple repos → 400 `{code: "repo_ambiguous"}` naming the candidates
  (never a run — the Launch form also blocks submit until a repo is picked).
- `start`/`review` `--terminal` is XOR `--launch` (child CLI exits 2 when
  both are passed). `--terminal` persists a preview session row, claims
  the harness lock with `origin=terminal` (pid = spawned terminal
  emulator), and prints `terminal_pid` in the run's JSON output; the
  agent is stopped via `POST /api/terminal/stop` with the worktree ref.
- `start` takes repeatable `--related-repo <name>` (one child worktree per
  repo on the same branch; the Launch Start form multi-selects from every
  registered repo except the main one).
- `link purge-deleted [--repo NAME] [--dry-run] [--json]` (planned — Task 2
  `link` SPECS entry not yet landed): permanently drops soft-deleted
  worktree rows from the database (bookkeeping only: worktree files and
  branches are already gone; never touches disk). `--repo` scopes to one
  repo display name (registered name, else the basename of the recorded
  repo path); no `--repo` = all soft-deleted rows. Naming a repo with zero
  deleted rows prints an empty result, exit 0. The Dashboard Deleted table
  runs it via `createRun` with an irreversible confirm dialog and
  invalidates `statusDeleted` on success.

Response 202: `{"run_id": "abc123"}` — `409 {code:"conflict"}` when
another run holds the same target key.

### Target keys (409 collisions)
`start`, `review:<ref>` / `review:all` (`--all`, plus `--force-all` to
re-include already-reviewed and unresolved-comment worktrees),
`cleanup:<ref>` / `cleanup:all`, `action:<label>:<ref>` / `config` (template CRUD),
`flow:<template>:<ref>` / `config` (template CRUD)

### `GET /api/runs` → list
```json
[{"id": "abc123", "command": "sync", "args": ["IPG-932"],
  "state": "running", "exit_code": null, "truncated": false,
  "created": 1730000000.0, "target": "sync:jira:IPG-932",
  "session_file": "/home/x/.config/workagent/sessions/omp/….jsonl",
  "worktree": "/home/x/wt", "last_seq": 42}]
```
`session_file`/`worktree` are non-empty only for runs that executed a
harness session (`start`/`review`, or `sync` with an explicit
`--session-file`); the web server injects `--session-file` for
`start`/`review` launches — including the default preview (no `--launch`;
the CLI preview pins the path as the session row id (transcript stem) and
`file_path`, so the first run command already carries the full path and
future runs of the same session reuse it) — except `--all` runs, which
carry no `--session-file` (the CLI rejects it with `--all`; each
per-worktree child generates its own transcript). Live summaries also carry
`session_id` (pinned transcript stem). Clients show resume buttons iff
`session_file` is present.

### `GET /api/runs/{id}` → detail (adds `lines`)
`lines`: `[{"seq": 1, "text": "syncing jira:IPG-932 …"}, …]`
(ANSI already stripped server-side). Post-restart rows (`db-<id>`) replay
the DB-persisted output (last 2000 lines, `truncated` when the live buffer
had dropped older lines) — the log survives `serve` restarts.

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
`<config_dir>/runs/<id>/output.log` (1 MiB head-truncation cap, `truncated`
marker file + `truncated: true` in list/detail rows; RAM ring stays 10k
lines), and writes `pid` (pid + start-ticks) / `exit_code` / `meta.json`.
`serve` only tails the log file. At startup `serve` rehydrates live run
dirs into a fresh registry (log backfill + tail continuity); finished-run
dirs are pruned to the newest 100, running ones never. Only the
supervisor writes `output.log`. The systemd unit uses `KillMode=process`
so stop/re-install never kills detached children.

### `POST /api/runs/{id}/resume` → `{"id": …, "session_file": …, "worktree": …}`
When the pinned `--session-file` path is missing/empty (first command run
manually outside the web server), the endpoint adopts the newest non-empty
`.jsonl` in the scope dir minted after the session row was created, persists
it on the session, and resumes that transcript — so the first run stays
resumable and later runs reuse the full path.
Non-destructive (same class as `open`): no confirm needed. Detached-spawns the OS default terminal running `cd <worktree> && omp --resume <session_file>` (`$TERMINAL` → `xdg-terminal-exec` → gnome-terminal/konsole/xfce4-terminal/xterm; macOS `open -a Terminal`; Windows `cmd /k`). Terminal spawns claim the harness lock with `origin=terminal` (pid = spawned terminal emulator) and pre-spawn 409 `{code:"conflict"}` when a live harness already holds the worktree, so no orphan terminal launches; sources the originating run's `--env-file` (or the configured default). 404 `no_session` when the run
executed no harness session, `missing_session` when the transcript is
absent, `no_worktree` when the target has no recorded worktree.

### `POST /api/runs/{id}/start-terminal` → `{"id": …}`
Same spawn running the run's last logged `harness command:` entry — the exact command behind the Run page "Copy harness command" button (the button is disabled exactly when this returns 404 `no_harness_command`). Claims the harness lock with `origin=terminal` and pre-spawn 409s `{code:"conflict"}` on a live lock, like the resume endpoints.

### `POST /api/terminal/stop` → `{"worktree_ref", "stopped": true}`
SIGTERM→SIGKILL the terminal-origin agent behind a worktree ref (the `--terminal` run mode). Body: `{"worktree_ref": "<key>"}`. 404 `not_found` when no live terminal lock (missing, dead-pid swept, or headless origin — headless runs are never stoppable here).

### `POST /api/sessions/{id}/resume` → same shape
Same terminal spawn for a persisted session row (`worktree` resolved from the row, else the recorded worktree ref via `/api/path`); sources the latest run's `--env-file` for the session's worktree when present. Claims the terminal lock / pre-spawn 409s like the run resume endpoints; 404 `not_found`/`missing_session`/`no_worktree` as applicable.

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
- Confirmation opt-outs: localStorage key `workagent.confirm.optout` — JSON map `{"cleanup": true, ...}` resettable in Settings.
- Launch prefs: localStorage key `launch.prefs.v1` — per-tab `{start:{runMode,base},review:{runMode,thenFix,fixAction,useReviewSession},sync:{merge}}`; only the active tab's slice persists on change, URL params (`ref`, `fixComments=1`) win for the live form and never persist. Repo tabs (`useRepoTabs`) show only repos with count > 0; a stale `?repo=` falls back to All. Candidates Review rows deep-link to `…/launch?mode=review&ref=<pr-url>` with the repo pre-selected.
