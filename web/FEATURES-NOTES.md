# Feature coverage notes

Mapping of every command/option in FEATURE_INVENTORY.md to the web UI. Explicitly
out of scope per the task: shell `completions`, `--quiet`, `-N/--no-harness`.

## Coverage matrix

| Command / option | UI surface |
| --- | --- |
| `status` | Dashboard, Links → "Linked sessions" table (GET /api/status, polled 15 s while visible) |
| `status --refresh-pr` | Dashboard + Links → "Refresh PR" button (GET /api/status?refresh=true) |
| `status --worktree` | "Worktree paths" switch on Dashboard + Links (adds worktree column / card line) |
| `status --json` / `--csv` | Dashboard + Repos + Runs → JSON copy button and CSV download (client-side from the same data) |
| `status [ref]` detail (issue URL, PR, worktree, branch, base branch, create hint) | fetched per key inside the confirm dialog (GET /api/status?ref=) and shown in dialog detail rows |
| `sync [ref]` | Dashboard row action → confirm dialog (server-side rebase by default) |
| `sync -m/--merge` | Sync dialog option (local merge instead of remote rebase) |
| `sync --all` | Dashboard → "Sync all" button (dialog states it implies no per-step stopping) |
| `sync --harness` | Launch page "Harness" select (start only) — not a sync option in this UI; sync uses the harness configured in the CLI config |
| `sync --yes/--force` | not sent by the UI — the server appends it when `confirm: true`; the web confirm dialog is the interactive equivalent (see below) |
| `sync --dry-run` | Sync dialog option; when set, POST is sent with `confirm:false` |
| `start` / `review` | Launch page tabs, full option set |
| `start/review --repo` | Launch "Repo" select (populated from GET /api/repos, plus "Registry default (auto)") |
| `start/review --depth` | Launch "Clone depth" number input |
| `start/review --base` | Launch "Base branch" input (start only) |
| `start/review --no-tty` | always on for web launches (headless); the Launch page shows an auto-approve warning |
| `start/review --dry-run` | Launch checkbox (POST `confirm:false`) |
| `start/review --json` | Launch checkbox |
| `cleanup` | Dashboard row action → confirm dialog listing issue URL, PR, worktree path, branch from /api/status?ref= |
| `cleanup --force` | Cleanup dialog option; when set, POST sends `force:true` and the dialog's "don't ask again" checkbox is hidden (force always confirms) |
| `repo list` | Repos page table/cards (GET /api/repos) |
| `repo add --name --path [--tracker]` | Repos page "Add a repo" form (runs as a child process; log visible under Runs) |
| `repo remove` | Repos page per-row trash action → confirm dialog (action type "repo remove") |
| `link list` | Links page "Tracker mappings" card + "Linked sessions" table (GET /api/links) |
| `link set TRACKER REPO` | Links page "Link a tracker to a repo" form (runs as a child process) |
| `link remove REF [--repo]` | Links page "Remove a link" form → confirm dialog (action type "link remove") |
| `register PATH [--key --issue --repo --force]` | Links page "Register an existing worktree" form; force runs always confirm |
| `cancel` of a running command | Runs list + Run detail → Cancel button (POST /api/runs/{id}/cancel, no body) |
| `-v` (verbose) | Settings toggle; `useCreateRun` prepends `-v` as `args[0]` on every run started from the UI |
| Theme light/dark/system | Settings + system preference tracking (localStorage `theme`) |

## Documented exceptions

1. **`--yes` is never sent by the UI.** The server appends it when the POST body
   carries `confirm: true` (per API_CONTRACT.md). The web confirm dialogs with a
   per-action "Don't ask again for this action" opt-out (localStorage
   `harness.confirm.optout`) are the interactive equivalent of `--yes`.
2. **`sync --harness`**: no control in the web UI; sync runs use the harness
   configured in the CLI config file. Only start/review expose `--harness`.
3. **`completions`, `--quiet`, `-N/--no-harness`**: out of scope per task.
4. **Backend 404 error shape**: `GET /api/status?ref=` and `GET /api/path?ref=`
   return FastAPI's `{"detail": "…"}` instead of the documented
   `{"error": {"code", "message"}}` on 404 (observed against the running
   server). The client (`request()` in `src/lib/api.ts`) falls back to
   `code: "http_error"` + the HTTP status text, so the UI still surfaces the
   failure; flagged here for the backend owner.
5. **Runs page state filter** (`?state=`) and **target filter** (`?target=`) are
   client-side; the contract's GET /api/runs returns all runs.
