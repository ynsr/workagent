# Issue #11 remainder — design

Follow-up to #9 (core landed, closed). Four items, all approved 2026-09-22.
Approach: SQLite-backed, CLI-first, thin web. No new top-level commands,
no new pages/endpoints. Repo conventions hold: exit 0/1/2, stdout
data-only, TTY-only confirmations, `rich.markup.escape` on user content.

## 1. Launch cache (1h + `--reset-cache`)

New `issue_cache(source TEXT PK, payload TEXT, fetched_at TEXT)` table in
`src/harness/store_sqlite.py` with `get_issue_cache` / `set_issue_cache`.
TTL 3600s. Cached rows are exactly what `list_my_issues()` returns today;
per-source keys (`jira`, `github`) so one failing tracker never poisons
the other. Per-source failure still yields `[]` + stderr warning
(the never-raise contract is preserved).

`harness candidates` gains `--reset-cache` (deletes rows, forces
re-fetch). `GET /api/candidates` and `GET /api/issues` accept
`?force=true` (wired to the web Refresh buttons). Web adds a second
layer: React Query `staleTime` 1h on `useCandidates` / `useIssues`.

## 2. Repos tracker mandatory + save/show fix

`harness repo add` without `--tracker` exits 2 with
`tracker is required: pass --tracker IPG|github:O/R|GitLab URL`.
`register_repo` is unchanged — the tracker lives in the `trackers`
mapping, not the repo registry.

Save/show fix: `repo list` (table + `--json`) and `GET /api/repos` gain
a `tracker` field resolved per repo by reverse-lookup in the `trackers`
mapping (first matching tid; `""` when none so existing trackerless
repos degrade to an empty cell, never an error). Web Repos page makes
the tracker input required and shows the same column. `repo add
--tracker` keeps writing through the existing mapping code in `repo_add`.

## 3. Tracker dropdown + auto-ref (minimal)

The LinkSet dialog Tracker field becomes a `SearchableSelect` dropdown
of known ids (from `/api/links → trackers`) with `allowCustom` for new
ids — the same component as the Launch ref picker. On paste of an
issue/PR URL, `refs.parse_ref` → `trackers.tracker_id()` pre-fills the
dropdown; unparseable input keeps the free text.

No new page or endpoint. The `gitlab:git.jibit.cloud/server/projectx`
scoping answer is docs-only in `AGENTS.md`: canonical ids are
host-scoped (`gitlab:<host>/<group>/<repo>`); a bare
`server/projectx` is not portable across hosts. GitLab URL
normalization reuses `trackers.normalize_id`.

## 4. Candidate actions (start / review / register)

Issue rows get **Start** (`start <key>`), PR/MR rows get **Review**
(`review <url>`), unregistered-worktree rows get **Register**
(`register <path>`) — all via the existing `useCreateRun` + confirm
modal pipeline (same shape as the Links page `useLinkSubmit`).
Read-only default is preserved: actions fire only on explicit click,
never automatically; copy-ref stays. Backend needs no new SPECS
entries (verify the `register` run-key mapping exists).

## Non-goals

Editable mapping grids, a dedicated Trackers page, `/api/trackers`,
server-side memory caches, relaxing the never-raise issues contract,
touching CHANGELOG/AGENTS/README beyond the §3 paragraph (docs owner
decides at implementation time).
