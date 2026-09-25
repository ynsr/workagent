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
| `start --harness omp` | mutating | — | `start` | no | unsupported harness name | |
| `start --no-tty` | mutating | — | `start` | no | | none — headless mode (`omp -p --auto-approve`, `backend.command_argv(no_tty=True)`) |
| `start -N/--no-runtime` (was `--no-harness` before #14; old name rejected) | mutating | — | `start` | no | | lands in interactive shell — **UI must not use it** (needs a TTY shell) |
| `start --dry-run` | read-only-ish | — | `start` | no | | none |
| `start --yes` | mutating | — | `start` | no | | skips confirmations |
| `start --json` | mutating | — | `start` | no | | |
| `review REF` | mutating | — | `review` | no | parse failure; not a PR/MR ref and no discoverable PR; tracker linked to 2+ repos without `--repo` (#29, exit 2 naming candidates) | repo picker (TTY) |
| `review --repo/--depth/--harness/--no-tty/-N/--dry-run/--yes/--json` | mutating | — | `review` | no | as above | as above |
| `review --all [--sequential] [--fix] [--force-all] [--fix-comments]` | mutating | — | `review:all` | no (spawns agent children) | nothing to review → exit 0 note | none — non-TTY parallel/sequential children; `--force-all` re-includes already-reviewed and unresolved-comment worktrees (still PR/MR only); `--fix-comments` (exclusive with `--fix`/`--post-comments`) fixes open review comments: validate, apply, resolve/close (GitHub bot comments get a `Status: RESOLVED` second line), commit and push |
| `cleanup --merged [--force/--dry-run/--json]` | mutating | — | `cleanup:all` | **yes** (per merged/closed link) | `--merged` w/o `--yes` non-interactive (2); ref given with `--merged` (2) | none — requires `--yes`; skips live-harness/invalid worktrees |
| `open REF` | read-only-ish (OS opener) | — | `open:<ref>` | no | no linked state (2), invalid/missing worktree (1), no opener (1) | none — prints the worktree path |
| `cleanup REF` | mutating | — | `cleanup:<ref>` | **yes** (closes tracker issue, removes worktree, branch, PR) | no linked state | none (non-TTY) / y-N (TTY) |
| `cleanup --force` | mutating | — | `cleanup:<ref>` | **yes** | | skips state validation |
| `cleanup --yes` | mutating | — | `cleanup:<ref>` | **yes** | | skips confirmation |
| `cleanup --dry-run` | read-only-ish | — | `cleanup:<ref>` | no | no linked state | none |
| `cleanup --json` | mutating | — | `cleanup:<ref>` | **yes** | | |
| `status [REF]` | read-only | `_resolve_session_key` + `_enrich_entry` / `_session_detail` (`cli.py`) | — | no | unknown ref without linked state | none |
| `status --worktree` | read-only | same (`_session_rows(show_worktree=True)`) | — | no | | |
| `status --refresh-pr` | read-only (network) | `_enrich_entry(entry, refresh=True)` | — | no | | |
| `status --json/--csv` | read-only | same | — | no | | |
| `cd REF` | read-only | `_resolve_session_key` + links | — | no | unknown ref (2), missing worktree (1) | none |
| `sync [REF]` | mutating | — | `sync:<ref>` | yes-ish (rebase/merge + push) | ref resolution; dirty worktree (1) | conflict: TTY confirm; `--harness`/`--yes` run omp |
| `sync -m/--merge` | mutating | — | `sync:<ref>` | yes-ish | | |
| `sync --harness` | mutating | — | `sync:<ref>` | yes-ish | | launches omp on conflicts |
| `sync --all` | mutating | — | `sync:all` | yes-ish | | implies `--yes` |
| `sync --yes/--force/-y` | mutating | — | `sync:<ref>` | yes-ish | | runs omp headless on conflicts (`omp -p --auto-approve`) |
| `sync --dry-run` | read-only-ish | — | `sync:<ref>` | no | | none |
| `sync --json` | mutating | — | `sync:<ref>` | yes-ish | | |
| `repo add --name --path [--tracker] [--json]` | mutating | — | `repo:add` | no (config write) | path missing | none |
| `repo list [--json/--csv]` | read-only | `store.load_config()["repos"]` | — | no | | none |
| `repo remove NAME [--json]` | mutating | — | `repo:remove` | yes-ish (registry write) | unknown repo (2) | none |
| `link set TRACKER REPO [--json]` | mutating | — | `link:set` | no (config write) | resolve failure | none |
| `link remove REF [--repo] [--json]` | mutating | — | `link:remove` | yes-ish (drops relation/link) | repo not linked (2) | none |
| `link list [--worktree] [--refresh-pr] [--json/--csv]` | read-only | `store.load_config()["trackers"]` + `_enrich_entry` | — | no | | none |
| `tracker add KEY [--vendor] [--remote-url] [--json]` | mutating | — | `config` | no (trackers table write) | `--vendor` outside the jira/github enum (2) | none — vendor/URL derive from the key when blank |
| `tracker list [--json/--csv]` | read-only | `store_sqlite.load_tracker_rows` | — | no | | none |
| `tracker remove KEY [--json]` | mutating | — | `config` | yes-ish (row delete, links cascade) | unknown tracker (2) | none |
| `register PATH [--key] [--issue] [--repo] [--yes/--force] [--json]` | mutating | — | `register` | no (links.json write) | bad path/main checkout (2), key conflict (1) | none |
| `doctor [--json]` | read-only | `doctor.check(json_output=True)` returns dict | — | no | exit 1 when stale | none |
| `completions show/install` | mutating (writes rc) | — | — | — | — | **excluded from UI per prompt** |
| `workagent serve` | — | this feature | — | — | port busy (1), missing web/dist (1) | none |

## Headless agent mechanism (reused, not reimplemented)

- File: `src/workagent/backend.py` → `command_argv(harness, prompt, no_tty=True)` builds
  `omp -p --auto-approve [extra] "<prompt>"`.
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
- `cleanup:<normalized ref>` — one cleanup per session ref; `--merged`
  keys as `cleanup:all` (one bulk sweep at a time).
- `open:<ref>` — one opener spawn per ref (harmless, but keeps the
  registry consistent).
- `register`, `repo:add`, `repo:remove`, `link:set`, `link:remove` — config
  writes; single global key `config` to avoid lost updates.

## Destructive commands requiring `confirm: true` at the API

- `cleanup` (closes tracker issue, removes worktree + branch, closes PR)
- `repo remove` (registry write)
- `link remove` (drops tracker relation or session link)
- `sync` (without `--dry-run`) — mutates branches (server rebase rewrites
  remote history / local merge + push)
- `start`/`review` launch auto-approving agents — require `confirm: true`
  (the Launch dialog states the agent runs headless with auto-approve).

`--yes` semantics verified from source: `cleanup --yes` skips its
confirmation prompts; `sync --yes` auto-launches the harness on conflicts
(non-TTY `omp -p --auto-approve`); `register --yes/--force` overwrites an
existing link; `start/review --yes` skip repo-picker confirmations. The
API passes `--yes` only when the request carries `confirm: true`
(`force: true` additionally passes `--force` for cleanup/register, whose
`--force` skips state validation / link overwrite — verified in
`cleanup_cmd` and `link_wt`).
