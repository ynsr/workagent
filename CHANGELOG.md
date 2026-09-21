# Changelog
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
- `install.sh` installs harness itself (uv preferred, pipx fallback) and
  derives the cli-hub `--version` from `pyproject.toml`.
- Fixed `review` reading `--dry-run` without defining the flag.

## Unreleased
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
- New `harness open <ref>`: open the linked worktree in the OS file
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
  `worktrees`, and `harness link list --json` renames its `sessions` key
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
  authored by me). `--json` prints `{"prs": […], "issues": […]}`;
  `--csv` renders the PR/MR table as CSV; CLI failures become stderr
  warnings, never a non-zero exit. Read-only web endpoints
  `GET /api/issues` (my issues + `warning`) and `GET /api/candidates`
  (same data server-side). `store.record_link` stamps `added_at` on
  first link (never bumped by updates).
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
  unchanged and the entry is <3h old — repeat `status` runs skip host-CLI
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
