# Web API contract — `harness serve`

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
Map of worktree key → entry (same shape as `harness status --json`):
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
(cached 10 min while the branch tip is unchanged). Optional query:
`?refresh=true` re-queries PR status (slow, hits the tracker CLI).

### `GET /api/status?ref=IPG-932` → single worktree detail
`_session_detail` shape: the entry fields plus
`key`, `harness`, `commits`, `pr` (display string), `commits_detail`,
`pr_detail`, `ci`, `base_branch`, `issue_url`, `wt_valid` (false when the
recorded path is missing or not a live git worktree), and `create_hint`
(only when there is no PR). `added_at` is present on entries stamped
after `store.record_link` gained it; older entries may lack it.

### `GET /api/path?ref=IPG-932`
```json
{"key": "jira:IPG-932", "worktree": "/home/x/wt"}
```
404 `{error:{code:"not_found",…}}` for unknown ref or missing worktree.

### `GET /api/repos`
```json
[{"name": "projectx", "path": "/home/x/projects/projectx"}]
```
(any extra keys from the registry entry are passed through).

### `GET /api/links`
```json
{"trackers": {"jira:IPG": {"repos": ["/home/x/projects/projectx"]}},
 "worktrees": { …same shape as GET /api/status… }}
```

### `GET /api/doctor`
```json
{"status": "ok", "live_hash": "…", "recorded_hash": "…",
 "tools": {"git-wt": true, "omp": true, "gh": true, "glab": true,
            "jira-cli": true}, "receipt": "/home/x/.local/share/…"}
```

### `GET /api/issues`
My open issues (jira To Do/In Progress reported by me in the last two
months + GitHub issues authored by me, state=open). Always 200 — a
missing/failing host CLI yields `[]` plus a `warning`:
```json
{"issues": [{"key": "jira:IPG-981", "title": "…",
             "url": "https://…/browse/IPG-981", "status": "To Do",
             "created": "2026-09-20T10:00:00.000+0000"}],
 "warning": "github: command not found: gh"}
```

### `GET /api/candidates`
Unlinked open PR/MRs across every registered repo (PR/MR URLs already
present in `links.json` are excluded server-side) plus my issues created
within the last 7 days (server-side filter) plus unregistered on-disk
worktrees under the scan root. Read-only:
```json
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
  `confirm: true` and appends `--force` (cleanup, register only).
Response 202: `{"run_id": "abc123"}` — `409 {code:"conflict"}` when
another run holds the same target key.

### Target keys (409 collisions)
`start`, `review` / `review:all` (`--all`), `cleanup:<ref>` / `cleanup:all`
(`--merged`), `open:<ref>`, `sync:<ref>` / `sync:all`, `config`
(register/repo/link subcommands).

### `GET /api/runs` → list
```json
[{"id": "abc123", "command": "sync", "args": ["IPG-932"],
  "state": "running", "exit_code": null, "truncated": false,
  "created": 1730000000.0, "target": "sync:jira:IPG-932",
  "last_seq": 42}]
```

### `GET /api/runs/{id}` → detail (adds `lines`)
`lines`: `[{"seq": 1, "text": "syncing jira:IPG-932 …"}, …]`
(ANSI already stripped server-side).

### `GET /api/runs/{id}/events` — SSE
- `event: log`, `data: {"seq": N, "text": "…"}`, `id: N`.
- `event: state`, `data: {"state": "succeeded", "exit_code": 0}` — final;
  the stream closes after it.
- `Last-Event-ID: N` resumes after seq N (replays log lines with
  `seq > N`; the final state event is always emitted again).
- Keepalive comment lines (`: keepalive`) may appear.

### `POST /api/runs/{id}/cancel` → `{"id": …, "state": "cancelled"}`
409 when the run already finished. Server sends SIGTERM to the process
group, SIGKILL after 10 s.

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
- Confirmation opt-outs: localStorage key `harness.confirm.optout` —
  JSON map `{"cleanup": true, ...}` resettable in Settings.
