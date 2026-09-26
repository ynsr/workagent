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
Map of worktree key → entry (same shape as `workagent status --json`):
```json
{
  "jira:IPG-932": {
    "issue": "IPG-932", "worktree": "/home/x/wt", "branch": "feat/…",
    "harness": "omp 4242",
    "repo": "/home/x/projectx", "issue_url": "https://…/browse/IPG-932",
    "commits": "0|1", "pr": "…",
    "ci": "success",
    "added_at": "2026-09-20T10:00:00+00:00",
    "wt_valid": true,
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
(cached 10 min while the branch tip is unchanged). `reviews` is the
display string `"R|U|R"` (done reviews | unresolved | resolved);
`reviews_detail` is `{"reviews", "unresolved", "resolved"}` or
`null` when there is no PR or the lookup failed (same 10-min/tip cache).
GitHub-only (#32): plain (non-inline) `# Code Review` bot comments carry
no native resolution state, so one counts as resolved only when its second
non-empty line (below the header) is exactly `Status: RESOLVED`; GitLab MRs use native flags.
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
  "tracker": "jira:IPG", "trackers": ["jira:IPG"]}]
```
(`tracker` = first linked tracker; `trackers` = all linked tracker ids
from the tracker_repos join; any extra registry keys pass through).

### `GET /api/trackers`
```json
{"trackers": [{"key": "jira:IPG", "vendor": "jira",
  "remote_url": "https://jira/…", "repos": 2}]}
```
(CRUD page source: `tracker add/list/remove` run as runs; `repos` is the
linked-repo count. `vendor` is an enum: `jira` | `github`; `remote_url` is mandatory —
`tracker add` runs require `--remote-url`.)

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

### `GET /api/doctor`
```json
{"status": "ok", "live_hash": "…", "recorded_hash": "…",
 "tools": {"git-wt": true, "omp": true, "gh": true, "glab": true,
            "jira-cli": true}, "receipt": "/home/x/.local/share/…"}
```

### `GET /api/issues`
My open issues (jira To Do/In Progress reported by me in the last two
months + GitHub issues authored by me, state=open). Per-source rows are
cached 1h server-side; `?force=true` bypasses the cache and re-queries
the tracker CLIs live (slow). Always 200 — a
missing/failing host CLI yields `[]` plus a `warning`:

### `GET /api/candidates`
Unlinked open PR/MRs across every registered repo (PR/MR URLs already
present in `links.json` are excluded server-side) plus my issues created
within the last 7 days (server-side filter) plus unregistered on-disk
worktrees under the scan root. `?force=true` re-queries the tracker CLIs
live instead of the 1h issue cache (slow). Read-only:
{"prs": [{"key": "github:o/r#33", "number": 33, "title": "…",
          "url": "https://github.com/o/r/pull/33", "branch": "feat/x",
          "updated": "2026-09-20T10:00:00Z", "state": "OPEN",
          "repo": "o/r"}],
 "issues": [{"key": "jira:IPG-981", "title": "…", "url": "…",
             "status": "To Do", "created": "2026-09-20T10:00:00.000+0000"}],
 "worktrees": [{"path": "/home/x/dev/worktrees/proj/feat/x",
               "repo": "proj", "branch": "feat/x",
               "key_guess": "branch:feat/x"}],
 "warnings": ["proj: gh pr list failed: …"]}
```
Scan root defaults to `~/dev/worktrees`, overridable via the `scan_root`
config key. Paths already linked are excluded server-side.

### `GET /api/sessions` → list (newest first, no prompts)
```json
{"sessions": [{"id": "2026-09-22T10-00-00-123Z-4567",
  "worktree_ref": "jira:IPG-932", "harness_name": "omp",
  "initiator_command": "start", "state": "finished",
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

## Runs (mutating CLI commands as child processes)

### `POST /api/runs` → 202
Request:
```json
{"command": "cleanup", "args": ["IPG-932"],
 "confirm": true, "force": false}
```
- `command`: one of `start | review | cleanup | sync | open | register |
  repo | link` (subcommand goes first in `args`: `["add", "--name", …]`).
- Global `-v` may be passed as `args[0]`.
- Values must not start with `-`; unknown options → 400.
- Destructive (`cleanup`, `sync` w/o `--dry-run`, `start`, `review`) need
  `confirm: true` → the server appends `--yes`. `force: true` requires
  `confirm: true` and appends `--force` (cleanup, register, repo remove).
- `repo remove` of a repo with linked worktrees → 400
  `{code: "worktrees_exist"}` unless `force: true` (cascades them).
  `tracker add` without a non-blank `--remote-url` → 400 `bad_arg`.
  `start`/`review` with no `--repo` when the ref's tracker is linked to
  multiple repos → 400 `{code: "repo_ambiguous"}` naming the candidates
  (never a run — the Launch form also blocks submit until a repo is picked).

Response 202: `{"run_id": "abc123"}` — `409 {code:"conflict"}` when
another run holds the same target key.

### Target keys (409 collisions)
`start`, `review:<ref>` / `review:all` (`--all`, plus `--force-all` to
re-include already-reviewed and unresolved-comment worktrees),
`cleanup:<ref>` / `cleanup:all`

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
`start`/`review` launches — including the default preview (no `--launch`; the CLI preview
carries it as `--resume` but creates nothing, so resume/copy buttons work
once the printed command is run manually). Clients show resume buttons iff
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
409 when the run already finished. Server sends SIGTERM to the process
group, SIGKILL after 10 s.

### `POST /api/runs/{id}/resume` → `{"id": …, "session_file": …, "worktree": …}`
Non-destructive (same class as `open`): no confirm needed. Detached-spawns
the OS default terminal running
`cd <worktree> && omp --resume <session_file>` (`$TERMINAL` →
`xdg-terminal-exec` → gnome-terminal/konsole/xfce4-terminal/xterm;
macOS `open -a Terminal`; Windows `cmd /k`). 404 `no_session` when the run
executed no harness session, `missing_session` when the transcript is
absent, `no_worktree` when the target has no recorded worktree.

### `POST /api/sessions/{id}/resume` → same shape
Same terminal spawn for a persisted session row (`worktree` resolved from
the row, else the recorded worktree ref via `/api/path`); 404
`not_found`/`missing_session`/`no_worktree` as applicable.

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
- Confirmation opt-outs: localStorage key `workagent.confirm.optout` —
  JSON map `{"cleanup": true, ...}` resettable in Settings.
