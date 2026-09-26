# Changelog
## Unreleased
- Sessions: `serve` run completions for session-linked runs (those with a
  session file) are mirrored into the `runs` table against the child-owned
  session row, so `GET /api/sessions/{id}` keeps per-session run history
  across server restarts. Other runs stay ephemeral in the in-memory
  registry (live logs/SSE only).
- Dashboard review counts on GitLab: only resolvable review threads count
  toward unresolved/resolved — system/activity discussions ("added N
  commits", "marked as draft", individual notes) are excluded, matching
  GitLab's own UI counter.
- cleanup: GitHub merges pass an explicit `--squash` strategy (`gh pr merge`
  requires one non-interactively); `--no-squash` maps to `gh --merge`.
- start/review/sync: a new PR/MR (not already registered) creates a git
  worktree on the fetched source branch and records it under the branch
  name (pr_url kept on the row). `start` accepts PR/MR refs directly;
  `sync` creates the worktree first, then syncs it. `_ensure_local_branch`
  fetches from origin before materializing a remote-only branch so a
  freshly-pushed source branch is found even with a stale local mirror.
- Cleanup remote-branch rules: the remote branch is deleted only when the
  branch's PR/MR merged with it as source (latest open PR/MR wins,
  else latest overall; head_ref verified). 404/conflicted PRs stay open —
  nothing is torn down and the user is told to resolve the conflict first.
  `--force` merges first; when merge is impossible the PR is left open and
  the remote branch kept while everything else is cleaned up (notified).
- Remove-worktree modal: new live Cleanup-vs---force comparison table (mergeable / conflicted / merged / 404 rows) that follows the --force checkbox; all modal shells widened to 2xl with scroll caps to stop text overflow.
- Issue #32: GitHub Reviews detection now counts plain (non-inline)
  `# Code Review` bot comments as resolved only when their second non-empty
  line (below the header) is exactly `Status: RESOLVED` (GitHub-only; GitLab MRs keep native
  flags). New `review --fix-comments` (per-worktree and `--all`,
  exclusive with `--fix`/`--post-comments`) launches a fix agent that
  validates each open comment against code + PR/MR description, applies,
  resolves/closes (appending the marker line on GitHub), then commits and
  pushes. Dashboard gains a per-row Fix action (wrench icon) and a
  "Fix all PR comments" top-bar button, both routed via Launch review
  mode (`fixComments=1` prefill) + `--fix-comments` checkbox.
- Issue #28: Dashboard worktree table gains a Reviews R|U|R column (done
  `# Code Review` comments | unresolved threads | resolved threads, cached
  10 min by branch tip like CI) in the table, mobile cards, worktree
  detail, and `status --json/--csv`; `review --all` skips worktrees without
  a PR/MR, with a live harness, or with unresolved PR comments — new
  `--force-all` re-includes already-reviewed and unresolved worktrees.
- Cleanup merged: the glab branch query now passes `--all` so merged/closed
  MRs are found (previously only open MRs listed, so a merged MR like #1720
  reported `skipped:no-pr`); the merged/closed state compare is
  case-insensitive (`merged` from the normalizer now matches `MERGED`).
- Issue #29: `start`/`review` with no `--repo` when the ref's tracker is
  linked to multiple repos now errors (exit 2 CLI, 400 `repo_ambiguous`
  via `/api/runs` — never a Run-logs surprise); the Launch form blocks
  submit and disables Launch until a repo is picked when no default
  resolves. Single-repo and linked-worktree defaults are unchanged.
  per distinct repo before recomputing, so Behind/Ahead and PR lookups see
- Web `repo remove`: pre-run 400 `{code: "worktrees_exist"}` when the repo
  still has linked worktrees; `force: true` appends `--force` and cascades
  them. Repos page remove dialog gains a `--force` checkbox.
- Status Behind/Ahead now uses the MR/PR target branch as the base: a
  cached base that disagrees with the PR's `target_branch` is treated as
  stale and recomputed, and fresh queries prefer the target branch over
  repo default-branch detection (fixes `0|0` for GitLab MRs targeting
  e.g. `develop`). Status rows served from a healed cache stay stable.
- Fix `repo remove` AttributeError: missing `store_sqlite.remove_repo_row`
  (lost in the SQLite cutover) restored; `register_repo_row_unlinked` no
  longer deletes the repo row via a spliced-in stray body.
- `repo remove` refuses while linked worktrees exist (exit 2, names the
  count); `--force` cascades them. Web passes `--force` on forced runs.

- Rename project to workagent (Fix #25): package `src/harness` →
  `src/workagent`, binary `harness` → `workagent`, config
  `~/.config/harness` → `~/.config/workagent` (one-shot auto-migration of
  the legacy dir on first run), receipt `~/.local/share/workagent/`,
  `_HARNESS_COMPLETE` → `_WORKAGENT_COMPLETE`, `HARNESS_CONFIG_DIR` →
  `WORKAGENT_CONFIG_DIR`. Completions install strips pre-rename marker
  blocks. No `harness` shim (hard cutover).
- Fix web arg inventory drift found during the rename: `review
  --post-comments`, `sync --force/-y`, `register -y` added; `sync
  --harness` corrected from value flag to bool. New parity test
  `test_specs_mirror_cli_flags` introspects the real Typer app and fails
  on future drift.
- Drop the Actions column from the Dashboard/Links worktree tables:
  action buttons now render as a right-anchored overlay on the last
  column (hover/focus reveal on fine pointers, always visible on touch).
- Fix PR/MR links pointing at the dev-server origin (e.g.
  `http://127.0.0.1:3344/MR%20#1695%20(open)`): the backend `pr` cell is a
  display label, not a URL — the table, mobile cards, and worktree detail
  now link `pr_detail.url` via a shared `prUrl()` helper.
- Animate worktree row detail open/close (~160ms grid-rows + fade/slide;
  `motion-reduce` skips it).
- Make `trackers.remote_url` mandatory with a real value: every required
  column across all state.db tables is now `NOT NULL` with no DEFAULT
  (legacy `DEFAULT ''` tables are rebuilt in place; legacy blank values
  are backfilled with derived URLs / timestamps), and
  `tracker add --remote-url` is required — no key-derived fallback.
- Make `trackers.vendor` a closed enum: `jira` | `github` (`--vendor`
  outside the enum is a usage error; legacy `gitlab`/`unknown`/blank
  vendors migrate to an enum member — gitlab maps to the
  github-compatible vendor). The web Add-Tracker Vendor field is now a
  searchable dropdown with those two options.
- Promote the trackers table (schema v2): `trackers` gains mandatory
  `vendor` + `remote_url` (NOT NULL, derived from the key when blank —
  jira:PREFIX → jira site/browse URL, github:O/R → github.com URL,
  gitlab:host/g/r → host URL), `repos.tracker_key` is dropped (mapping
  lives only in `tracker_repos.tracker_key → trackers.key_ref`), and
  legacy DBs migrate in place on `init_db`. New CLI `tracker
  add/list/remove` (+ web Trackers page `GET /api/trackers`, nav, run
  types) with full CRUD parity; `repo list` shows joined `trackers`,
  `link list` shows vendor/remote_url.
- Ignore the CWD as default repo (Fix #26): trackers + repos now live in
  `state.db` (`trackers`/`repos`/`tracker_repos`, migrated from
  `config.json` with `tracker_key` backfilled from the origin remote);
  `start`/`review` reuse the linked-worktree repo, else the single linked
  repo, else prompt (no CWD default; headless aborts with `--repo` usage);
  Launch prefills the repo default via `GET /api/default-repo?ref=`.
## 0.2.1 — 2026-09-18

- Fix Tab completion: typer 0.27 never registers its shell completion
  classes for the env-var server path, so every completion attempt died
  with `Shell bash not supported.` — cli.main() now registers them.
- Silence completion-server stderr inside the sourced eval line; a failing
  server degrades to no candidates instead of printing into the shell.
- Add a subprocess regression test covering the real completion protocol.

## 0.2.0 — 2026-09-18

- Typer + Rich migration: `--help` with real examples and documented exit
  codes; Rich tables for `status`/`repo list` with `--csv`/`--json` opt-in.
- One completion system: `completions show <bash|zsh|fish>` (Click-generated)
  + `completions install [shell] [--rcfile] [--yes]` (idempotent marker block,
  atomic write, .bak, stale-block replace); replaces `completion`/
  `completion-install`. Repo names complete on `--repo` and `repo remove`.
- `install.sh` installs workagent itself (uv preferred, pipx fallback) and
  derives the cli-hub `--version` from `pyproject.toml`.
- Fixed `review` reading `--dry-run` without defining the flag.

## Unreleased
- Fix #22: `start` passes full GitHub issue URLs to `git-wt --link`
  (shorthand `OWNER/REPO#N` built `github:OWNER/REPO#N`, which git-wt
  rejects); `register --issue` records the full URL likewise. `review`
  rejects ambiguous shorthand refs with a PR/MR-URL hint instead of
  recording a non-URL `pr_url`.
- Fix #23: `start --yes` (incl. web Launch runs) no longer adopts an
  unlinked cwd repo when linked repos exist — the serve cwd is unrelated
  to the issue, so the established link wins instead of silently filing
  e.g. a personal-checkout path under `jira:IPG`.
- Fix #24: Launch Start/Review default to `--no-runtime` (print the
  runtime command for manual execution); `--no-runtime` previews are
  `cd <worktree> && …` copy-paste runnable with a `--resume` session path
  (server injects `--session-file` even for `--no-runtime`, creating
  nothing) and the Run page gains a Copy-runtime-command button; finished
  start runs resolve their worktree from links for resume/copy.
- Fix #17: dashboard PR/MR label links to the absolute URL; row actions
  hidden until hover (touch/keyboard unaffected) with a history button
  deep-linking the Sessions page (`?worktree=` filter); remote-call/TTL
  cost panel under the dashboard table.
- Fix #18: Sessions page gains a Title column (humanized branch, 50 chars),
  a searchable worktree filter dropdown (+ text filter), and a Kind column
  (Start (task)/Review/Sync from the initiator command).
- Fix #20: Dashboard/Links/Runs/Sessions gain per-repo tabs (`?repo=`,
  repo name = tab title) grouped by registered repo path; sessions resolve
  worktree refs via links, non-matching items under `(other)`.
- Fix #17 (follow-up): PR-status cache TTL extended 3h → 3d; no-PR
  negative cache stays 30min. `_merge_pr` tolerates already-merged/closed
  (cached OPEN state can lag up to 3d); Sessions `?q=` also matches Title.
- Fix cleanup on merged MRs: `glab mr close` fails with "already been
  merged" (not "already closed"), which aborted cleanup before tearing
  down the worktree — now treated like an already-closed MR. Cleanup also
  skips the remote close entirely when cached PR state is already
  merged/closed, and `gh issue close` tolerates already-closed/missing
  issues instead of aborting.
- Fix #16: `parse_ref` accepts the `jira:KEY` form `issue_key()` emits, so
  web Start runs launched from issue dropdown keys (`jira:IPG-984`) parse.
- Fix #15: stale `--no-harness` (renamed to `--no-runtime` in #14) is
  rejected by `/api/runs` validation with a hint naming `--no-runtime`;
  current `--no-runtime`/`-N` already validated (reporter's serve predated
  the rename — reinstall/rebuild after pulling).
- Fix #19: `repo add` no longer requires `--tracker` when the origin remote
  reveals it — GitHub remotes map to `github:OWNER/REPO`, GitLab remotes to
  `gitlab:<host>/<group>/<repo>`; unknowable remotes still exit 2 with no
  half-registered repo left behind.
- Runtime session resume: every real `start`/`review` launch carries a
  transcript path (`--session-file`, routed to omp as `--resume`; the web
  server injects one per run). Runs expose `session_file`/`worktree` and
  resume buttons (terminal + copy `cd <worktree> && omp --resume <file>`)
  appear on Sessions rows/detail always and on run rows/Run log page only
  when the run executed a runtime session. New `POST
  /api/runs/{id}/resume` and `POST /api/sessions/{id}/resume` spawn the OS
  default terminal detached (`$TERMINAL` → `xdg-terminal-exec` →
  gnome-terminal/konsole/xfce4-terminal/xterm). `--session-file` is
  rejected with `--all` (one transcript per worktree — omit it and each
  launch gets its own file). Rename `--no-harness`
  (`-N`) → `--no-runtime` everywhere (flag, `runtime_command` result key,
  messages, docs).
- `review` reuses a tracked worktree's row instead of inserting a
  `pr:<url>` alias that collides on the `worktrees.branch` UNIQUE key
  (crashed re-reviewing tracked worktrees); the row matcher keys on
  branch identity + normalized path (`worktrees.recorded_key`). The
  regression test now runs on a real `state.db`.
- Issue cache + mandatory repo tracker + candidate actions: `issue_cache`
  table (per-source rows, 1h TTL) behind `trackers.list_my_issues(force)`;
  `candidates --reset-cache` clears and re-fetches; `GET /api/issues` and
  `GET /api/candidates` accept `?force=true`. `repo add` requires
  `--tracker` (exit 2) and `repo list`/`GET /api/repos` show a resolved
  `tracker` column. LinkSet tracker dropdown with URL auto-ref. Candidate
  rows gain Start/Review/Register actions via the run pipeline; `register`
  runs are keyed per path (`register:<path>`).
- SQLite sessions + cutover: `workagent migrate` one-shots
  `links.json`/`pr_cache.json`/`harnesses.json` into `state.db`
  (counts verified, files deleted, `config.json` kept; idempotent re-run
  is a no-op) and all link/PR-cache reads+writes delegate to SQLite
  afterwards. Every real harness launch records a session row (timestamp
  id `…Z-<4-digit>`, runtime, initiator command, prompt, `.jsonl` path;
  `finished`/`failed` on completion) with `GET /api/sessions` +
  `GET /api/sessions/{id}` and a web Sessions page. `cleanup`
  squash-merges open PR/MRs first (`--no-squash` opts out; remote branch
  harness-name conditionals for launch argv.
- `candidates` now also lists unregistered on-disk worktrees under the
  scan root (default `~/dev/worktrees`, overridable via the `scan_root`
  config key): both `<repo>/<branch>` and `<repo>/<issue-type>/<branch>`
  shapes, validated via `git worktree list`, linked paths excluded. Third
  Rich table, `worktrees` key in `--json` and `GET /api/candidates`, and a
  "Worktrees" tab in the web Candidates card (copy-path per row,
  read-only — no auto-register).
- Picker raw-mode fix: option/label lines now end `\r\n` (plus `\r`
  before erase cycles), so arrow-key prompts render without the staircase
  effect on real ptys; `j`/`k` also move the selection.
- Recorded-PR fallback: when the cache and the live query yield no PR but
  the link records a `pr_url`, the recorded URL fills the PR cell (state
  unknown). Negative (no-PR) cache entries expire after 30 min instead of
  3 h; GitLab MRs render `MR #N`.
- CI status: `status`/`link list` tables, the `status <ref>` detail panel
  (`--json` included), and the web table/detail gain a `ci` column —
  `success`/`failure`/`running`/`not_started` via `gh`/`glab`
  (`refs.fetch_ci_status`; soft-fails to null with a stderr warning),
  cached 10 min per branch tip (`store.cache_ci_status` in
  `pr_cache.json`); Rich tables render ✓/✗/●, `-` otherwise.
- New `workagent open <ref>`: open the linked worktree in the OS file
  manager (`xdg-open`/`open`/`explorer`, detached); prints the path on
  stdout, refuses missing/invalid worktrees. Also runnable from the web
  UI (Status table row action) via `POST /api/runs`.
- `cleanup --merged --yes`: clean every linked worktree whose PR/MR is
  merged/closed (anything else, live-harness, and invalid entries become
  `skipped:<reason>` rows — never torn down; `--dry-run` previews;
  `--json` prints `{"results": [...]}`). The web UI offers it as a
  "Cleanup merged" bulk button next to "Review all" (`review --all`) and
  "Sync all".
- **BREAKING**: `GET /api/links` renames the `sessions` key to
  `worktrees`, and `workagent link list --json` renames its `sessions` key
  to `worktrees` — same shape, new key. Stored link state is unchanged.
- Web UX wave: `?q=` search over the status table (URL-synced), first-seen
  `added_at` column (stamped by `store.record_link`, never bumped; default
  sort newest-first), CI badge, per-row `open` action, bulk
  Review-all/Sync-all/Cleanup-merged buttons with confirm dialogs,
  dropdown selects for repo/launch pickers, a Candidates card backed by
  `GET /api/candidates`, and an invalid-worktree banner + row highlight
  (`wt_valid: false` from `/api/status` — missing path or not a live git
  worktree; remove via the Delete action).
- `candidates`: lists unlinked open PR/MRs across registered repos
  ("Unlinked PR/MRs" table; linked URLs excluded) and my recent issues
  ("Recent issues (reported by me, last 7 days)" — jira To Do/In
  Progress reported by me in the last two months + GitHub issues
  `--json` prints `{"prs": […], "issues": […]}`;
  `--csv` renders the PR/MR table as CSV with raw (unescaped) titles;
  CSV/JSON carry raw user content either way, Rich tables escape it.
  CLI failures become stderr warnings, never a non-zero exit. My-issues
  JQL is `status in ("To Do", "In Progress") AND created >= -60d`
  (Jira has no month unit; "To-Do" with a hyphen does not exist). glab
  installs lacking `--state` on `mr list` fall back to its stateless
  open-MR listing. Read-only web endpoints `GET /api/issues` (my issues
  + `warning`) and `GET /api/candidates` (same data server-side).
  `store.record_link` stamps `added_at` on first link (never bumped by
  updates).
- One live AI harness per worktree: `start`/`review` (and `sync`'s
  conflict-harness runs) record the running harness in a locked
  `harnesses.json` (pid-liveness sweep; dead entries self-heal on the
  next read) and refuse a second launch on the same worktree with
  exit 1 — `worktree <path> already has a live harness (<name>, pid
  <pid>) — wait for it to finish or kill it`. `--no-harness` is never
  guarded and never records.
- `status`/`link list` tables, the `status <ref>` detail panel
  (`--json` included), and the web table/detail gain a `harness`
  column: `<name> <pid>` while a harness is live on the worktree,
  `—`/empty otherwise (present in `--csv`, `--json`, and `/api/status`).
- `review --all`: reviews the PR/MR of every not-reviewed worktree in
  parallel `python -m harness review` children (`--sequential` spawns
  and waits one at a time; `--fix` appends an auto-fix prompt segment;
  `--post-comments` appends an auto-comment segment). Skips
  reviewed-at-tip, no-PR, missing-worktree, and live-harness entries
  with a stderr note; prints a key/PR/exit-code summary and exits 0
  with "nothing to review" when nothing is reviewable. Bare `review`
  without a ref is now a usage error (exit 2) suggesting `--all`.
- Reviewed flag: a successfully launched review records `reviewed`/
  `reviewed_at` (the worktree's branch tip) on the PR link; `review
  --all` skips it until the tip moves — a new commit resets the flag
  automatically so the worktree becomes reviewable again.
- Session→Worktree terminology: prompts, errors, and table titles now say
  "worktree" ("Linked worktrees", "multiple worktrees match"); `cleanup`,
  `cd`, `link remove`, `status`, `sync`, and the web `/api/status` +
  `/api/path` endpoints resolve refs via the shared
  `worktrees.resolve_worktree`/`pick_worktree`. JSON keys and stored
  link state are unchanged.

- Web UI: `harness serve` — local FastAPI server + React/Vite/TypeScript
  frontend (`web/`, built to `web/dist`) with CLI feature parity: session
  dashboard (branch, commits behind|ahead, PR), launch `start`/`review`,
  repos, tracker↔repo links, sync, cleanup, register, run logs over SSE
  with cancel, doctor. Read endpoints reuse the in-process CLI helpers;
  mutating commands run as `python -m harness` children with per-target
  concurrency (409), confirm/force gating, and ≤10k-line buffers. New
  optional dependency group: `web` (fastapi, uvicorn). The server is
  unauthenticated: loopback-only bind by default, Host/Origin allow-list;
  existing command help output is unchanged.
- `harness serve` default static dir now falls back to the install
  receipt's `source_dir` when running from a site-packages install, so a
  built `web/dist` in the source repo is found without `--static-dir`.
- `install.sh` installs the `web` extra (fastapi, uvicorn) and builds the
  web UI (`npm ci && npm run build`) automatically; graceful warnings when
  npm is missing or the build fails.
- Jira support: `KEY-123` / `<host>/browse/KEY-123` refs via `jira-cli`.
- Resilient `start`: existing branch/worktree resumes instead of failing;
  upstream verified to point at the feature branch, never the base.
- `start` from inside a linked worktree on a non-protected branch continues
  on that branch — no new issue-named branch is created (same rule when
  `--base` names a non-protected branch).
- AI-harness prompts now pin the push target: exact worktree and
  `push to origin/<branch>` — never create or push a different branch.
- Running `start`/`review` from a linked worktree resolves the repo to the
  main checkout (links and repo registry point at the main repo, not the
  worktree).
- `start`/`review` accept extra harness args after `--`; `doctor` checks `jira-cli`.
- Fix `start` in TTY mode: the harness now runs inside the new worktree
  (cwd changed before `execvp`) instead of staying in the launch directory.
- `-N` shorthand for `--no-harness` on `start` and `review`.
- `--base` completes local and remote-only git branches of the current
  directory (offline: reads the origin mirror, never the network; filtered
  by prefix; empty outside a repo).
- `--base <remote-only branch>` works: `start`/`review` materialize a local
  branch tracking `origin/<branch>` (fetch-free) before git-wt sees it;
  a branch missing locally and on the mirror fails with a `git fetch` hint.
- `start --no-harness`: skip launching the harness — print the exact harness
  command and replace the process with an interactive shell inside the
  worktree (non-TTY: print and exit). Same for `review --no-harness`.
- `start --base <branch>` naming a non-default branch (not `main`/`master`/
  `develop`/repo default) runs on that existing branch: worktree created for
  it via git-wt (naming unchanged), upstream `origin/<branch>`, no new branch.
  Default-base `--base` still creates a new feature branch as before.
- Tracker↔repo guard: `start`/`review` persist the tracker→repo mapping
  (`jira:PREFIX`, `github:OWNER/REPO`, `gitlab:host/group/repo`) on first use
  and require confirmation (`--yes` non-interactive) before using a different
  repo; manage via `harness link list|set|remove`.
- `cleanup` accepts fuzzy refs (session key, issue number, branch/worktree
  substring; interactive pick on ambiguity) and treats an already-closed
  PR/MR as success, continuing with local cleanup.
- Repo-picker flow for `start`/`review` (no `--repo`): cwd linked → use it;
  cwd unlinked → y/N (No falls through); outside any repo → single linked
  repo wins, multiple → interactive pick, none → type a repo (or abort
  non-interactively). Selected repo is linked to the tracker project.
- Shared ref resolution: `status`, `sync`, `review`, and `cleanup` accept
  fuzzy refs (session key, issue number, branch/worktree substring) with
  shell completion over session keys, branches, and worktree names.
- `status` and `link list` enrichment: `commits` column `B|A` = B commits
  behind, A ahead of the remote-tracking base branch (`gone` for missing
  worktrees; legend on stderr), latest PR/MR per branch with bright state
  colors, `--worktree` toggles the path column, `status <ref>` shows a
  detail panel (PR title/author/URL, counts; `--json` too).
- Status cache: per-branch entries in `pr_cache.json` (PR, host tool, base
  branch, branch/base tips, counts) are reused while both tips are
  unchanged and the entry is <3d old — repeat `status` runs skip host-CLI
  detection and PR/MR API calls entirely; `--refresh-pr` re-queries the
  PR/MR. gh↔glab mis-detection self-heals (the other CLI is tried and the
  working one is remembered). PR titles with `[` no longer crash Rich
  (markup is escaped).
- New `register <path>` subcommand: adopt an existing (unregistered)
  git worktree as a session link. Session key defaults to `jira:<KEY>`
  derived from the branch name, else `branch:<branch>`; `--issue`/
  `--key`/`--repo` overrides; `--force` overwrites an existing link for
  the key. Registered worktrees resolve for `status`/`sync`/`cd`/
  `cleanup` like `start`-created ones.
- The default `sync` remote rebase now pulls the rebased branch into the
  local worktree afterwards: fast-forward when possible, otherwise a
  hard reset guarded by `git cherry` (only when every local commit's
  patch-id exists on the rebased branch; genuinely new local commits
  leave the worktree untouched). If the server rebase fails (e.g.
  conflicts), sync falls back to the `--merge` flow automatically.
- `sync` pushes the session branch to origin after every successful local
  merge (`--merge`, including after harness-resolved conflicts) — the old
  `--push` flag is gone because push is now the default. `--yes`/`--force`
  also auto-launches the coding harness on unresolvable conflicts in
  non-interactive mode (`omp -p --auto-approve`), and `--all` continues
  with the remaining sessions even when one fails.
- `status <ref>` detail shows how to open a missing PR/MR: a web
  create-PR URL for GitHub remotes, otherwise a ready-to-run
  `glab mr create --repo <host>/<path> --source-branch <branch>` command
  (also present as `create_hint` in `--json`).
- New `harness cd <ref>` prints the session's worktree root
  (`cd "$(harness cd IPG-959)"`); `completions show|install` now also
  provides a `harness-cd` shell function so `harness-cd <ref>` changes
  directory directly.
- PR/MR lookups pick the host CLI from the repo's origin URL: the remote
  host is matched against gh's known hosts (`~/.config/gh/hosts.yml`) and
  glab's (`~/.config/glab-cli/config.yml`) — GitHub hosts query `gh`,
  everything else `glab`; unknown hosts fall back to the old auth-status
  probe. The choice is persisted per registered repo (`repos.<name>.tool`,
  validated against the stored remote URL — a changed remote re-detects),
  so GitLab repos never trigger a failing `gh pr list` (and its warning
  spam) and repeated runs skip detection entirely.
- `status <ref>` detail panel (and `--json`) reports the full issue URL —
  `<jira site>/browse/KEY` from jira-cli's config (previously the bare key;
  also replaces a hardcoded Jira site in `start`'s worktree link), or the
  GitHub issues URL derived from the session key; a stored URL wins.
  `start` records the URL as `issue_url` in links.json.
- Interactive multi-choice prompts (ambiguous session refs, multi-repo
  tracker pick) now use a shared arrow-key picker (`harness/pick.py`):
  ↑/↓ selection with a `❯` marker, optional dim description per item,
  Enter selects, q/Esc/Ctrl-C aborts; non-TTY runs keep the actionable
  error instead of prompting.
- New `harness sync [ref]`: default remote rebase (`gh pr update-branch
  --rebase` / `glab mr rebase`); `-m/--merge` merges origin/<default> into
  the session branch locally (push only with `--push`), no PR falls back to
  local merge; sole-conflict `CHANGELOG.md` auto-resolves when every hunk
  stays inside `## Unreleased` (bullet union); other conflicts prompt on a
  TTY, `--harness` launches the coding agent to resolve, non-TTY exits 2;
  dirty worktrees abort; `--all`/`--dry-run`/`--json` supported.

## 0.1.0 — 2026-09-17

- Initial release: `start`, `review`, `cleanup`, `repo add/list/remove`,
  `status`, `doctor`, `completion`/`completion-install`.
- GitHub (`gh`) + GitLab (`glab`) issue/PR fetching; `omp` harness (v1 only).
- State in `~/.config/harness/`; `git-wt` via subprocess.
