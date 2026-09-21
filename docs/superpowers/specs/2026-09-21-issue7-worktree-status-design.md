# Issue #7 — Worktree Status, Bugs & Web UX Wave

**Issue:** https://github.com/ynsr/harness/issues/7 (9 items + minor cleanups)
**Status:** Approved design (user decisions: full rename incl. JSON key; one spec+plan; CI = own column)
**Date:** 2026-09-21

## Goals (mapped to issue items)

| # | Item | Phase |
|---|------|-------|
| G1 | Fix picker column drift (raw-mode OPOST bug) | Bugs |
| G2 | Fix empty PR/MR column (recorded `pr_url` ignored; negatives cached 3h) | Bugs |
| G3 | CI pipeline status per worktree PR/MR, cached, CLI + Web | CI |
| G4 | "Copy worktree path" button → open folder in OS file explorer | Web |
| G5 | Launch Issue Ref box → searchable dropdown of my issues | Web |
| G6 | Rename Sessions → Worktrees (labels AND `/api/links` JSON key) | Web |
| G7 | Links/Worktrees table: searchable by text columns, `added_at` column + field, sort desc | Web |
| G8 | Bulk buttons: auto-cleanup merged, review all, sync all (exists); fake/buggy worktree detect + highlight + delete | Web |
| G9 | Candidates: unlinked PR/MRs + last-week my-issues (To-Do/In-Progress), CLI subcommand + Web | Web |

## Phase A — Bugs

### A1. Picker raw-mode column drift (G1)
**Root cause (verified):** `src/harness/pick.py` emits bare `\n` (option line :62, `_redraw` erase pass :82, label :126) while `tty.setraw` (:96) cleared `OPOST` → LF without CR; each redraw staircases one full list-width right. The `ccd9603` fix removed vertical accumulation only. Stale-install hypothesis disproven (installed copy byte-identical).

**Fix:**
- All picker stream writes that end a line use `\r\n` instead of `\n` (option line, `_redraw` erase pass, label draw).
- `_erase` (:70-75) writes a final `\r` so the shell prompt after abort starts at column 0.
- Implement `j`/`k` keys (promised in module docstring since inception, never implemented); keep doc and code in sync.
- Tests (existing seams `read=`/`stream=`): every rendered option line and erase line ends `\r\n`; j/k navigation selects correct index. Note: StringIO-based tests cannot catch this class of bug — the terminator assertion is the regression test.

### A2. Empty PR/MR column (G2)
**Root cause (verified):** `_status_cells` (cli.py:979-1040) never reads `entry['pr_url']`; the cell comes solely from `pr_cache.json` keyed by branch (`_query_pr` → `refs.fetch_pr_list_for_branch`). Negative results cache for 3h with tip-only invalidation — creating an MR changes no tip, so a new MR stays invisible up to 3h. Live cache shows `jira:IPG-929` with recorded MR 1699 yet `pr:null`.

**Fix:**
- Recorded `pr_url` wins: when the link entry has `pr_url` and the cache has no PR (or is stale), seed the cell from the recorded URL (fetch its detail for `pr_detail` via the existing `refs` single-PR fetch) rather than showing `-`. A recorded PR URL is authoritative for display.
- Negative lookups (no PR found) get a 30-minute effective TTL instead of 3h — creating an MR surfaces within 30 min without `--refresh-pr`.
- When no PR exists, the table row keeps `-`; the create-hint stays on the detail path only (unchanged product decision).
- Tests: recorded-pr_url wins over cached null; negative TTL short-circuit.

## Phase B — CI pipeline status (G3)

### Data
- `pr_cache.json` entries gain additive fields: `ci` (`"success"|"failure"|"running"|"not_started"|null`), `ci_checked_at` (ISO UTC), `ci_sha` (the head commit CI ran on).
- CI invalidation is independent of PR invalidation: a pipeline transitions without tips changing. Fresh rule: `ci` reused while `ci_sha == branch_tip` AND `ci_checked_at` younger than **10 min**; otherwise re-fetch. (Pipeline completion latency budget; CI fetches are cheap single CLI calls.)
- Only fetched when the entry has a PR (recorded or found). No PR → `ci = null` → displayed as "not started" cell `-`.

### Fetch (`refs.py`)
New `fetch_ci_status(tool, pr_url_or_number, cwd) -> str|null`, sibling of `fetch_pr_list_for_branch`:
- gh: `gh pr checks <url>` — parse exit/output; alternatively `gh pr view <url> --json statusCheckRollup`. Normalize: any fail → `failure`; all pass → `success`; pending/running → `running`; no checks → `not_started`.
- glab: `glab api projects/:id/merge_requests/:iid/pipelines` (host handled by glab config) → latest pipeline `status` ∈ {success, failed, running, pending...} → same normalization; no pipeline → `not_started`.
- All calls through `run_cmd`; failure → `ci = null` (cell `-`), warning to stderr (same pattern as `_query_pr`'s swallowed-error warning). Host tool = `_repo_tool` with cross-CLI fallback (unchanged).

### Display
- **CLI:** new `ci` column after PR/MR: `✓` success, `✗` failure, `●` running, `-` not started. CSV/JSON carry the raw string (`success`/`failure`/`running`/`not_started`/`""` for no-PR). Included in `_session_detail`.
- **Web:** `SessionEntry.ci` typed; badge in StatusTable row + WorktreeDetail row mirroring `PrBadge`/`PR_STATE_STYLES` (green/red/blue pulsing-or-solid/gray). Flows automatically via `_enrich_entry` spread.
- `--refresh-pr` also refreshes CI (single flag, no new flag).

### Store cleanup (in scope, adjacent)
Delete dead `store.py` duplicate `get_cached_pr_status` (:150-151) and unused `save_pr_cache` (:116-117).

## Phase C — Web UX + candidates (G4–G9)

### C1. Open worktree folder (G4)
- New CLI subcommand `harness open <ref>`: resolve ref → linked worktree (like `cleanup`), refuse missing/invalid with exit 1, then spawn OS opener on the serve/CLI host: Linux `xdg-open`, macOS `open`, Windows `explorer`. Never accepts an arbitrary path — linked worktrees only.
- Non-destructive: NOT added to webapp's auto-`--yes` confirm list; `confirm: False`, `force: False`, target key `open:<ref>` (mirrors cleanup's lambda). Added to webapp `SPECS` + flag allowlists (it takes no flags beyond the positional ref).
- Web: StatusTable row action replaces "Copy worktree path" with "Open worktree folder" (uses the freed FolderOpen icon correctly); hidden/disabled with tooltip when `/api/info.network_exposed` (opening on the server host is useless). Keep copy available elsewhere? No — issue says replace.
- Tests: open resolves ref, refuses invalid; webapp spec registration.

### C2. Launch Issue Ref searchable dropdown (G5)
- **Backend:** new `GET /api/issues?repo=<name>&state=todo,inprogress` (read endpoint, in-process helper `trackers.list_my_issues(repo=None)`): jira → `jira-cli issue list -q 'reporter = currentUser() AND status in ("To-Do", "In Progress") AND created >= -2m ORDER BY created DESC' --plain --no-headers` parsed to rows; GitHub → `gh issue list --author @me --state open --limit 100 --json number,title,state,updatedAt,url` (no To-Do/In-Progress concept — return open issues authored by me, newest first); GitLab via glab analog. Response: `{"issues": [{key, title, url, status, updated}]}`. Server-side filtering/limit (JQL), client renders virtualized-ish list (simple max-height scroll suffices; ≤100 rows). Failures (CLI missing, auth) → empty list + `{"warning": "..."}` field, dropdown falls back to free text.
- **Frontend:** `SearchableSelect` gains optional async mode (loading/error states, debounced fetch on open/typing, keeps `allowCustom` so pasting an arbitrary ref — the current behavior — still works). Launch swaps the free-text Input for this control; on selection fills the ref (key), preserving prefill contract (`?ref=` launch links still bypass the dropdown).
- CLI completion stays offline (product rule — no network in completions).
- Tests: endpoint parses jira-cli/gh fixtures (mocked run_cmd); free-text escape works.

### C3. Renames (G6)
- **Labels (web/src, all user-visible strings):** "Session(s)" → "Worktree(s)", "Linked sessions" → "Linked worktrees", per StatusTable/Launch/Dashboard/Links/settings/confirm/NotFound/AppLayout. TS identifiers renamed too (`SessionEntry`→`WorktreeEntry` etc.) for consistency — code-only.
- **JSON contract:** `/api/links` key `"sessions"` → `"worktrees"` (webapp.py, api.ts, Launch.tsx sole runtime consumer, API_CONTRACT.md). Version-bump note in API_CONTRACT.md; no other endpoint changes.
- **Code-referenced trap:** Launch.tsx `copy.title === "Sync a session"` string comparison (:179-180) — refactor to an explicit field (e.g. `copy.confirmDesc`) so label renames can't silently change confirm text.
- CLI table header "Session" (cli.py `_session_rows`/`_print_rows`) → "Worktree" for consistency (cosmetic; not UI-reachable but cheap).

### C4. Searchable table + Added Date (G7)
- **Store:** `record_link` sets `added_at` (ISO UTC) via setdefault on first record; merges never bump it. Existing entries without the field sort last (treated as oldest).
- **Passthrough:** `_enrich_entry` spreads entries → automatic; api.ts `WorktreeEntry.added_at?: string`; API_CONTRACT.md.
- **Sort:** both Dashboard and Links default-sort by `added_at` desc (missing = oldest); replaces alphabetical key sort in StatusTable.
- **Search:** shared text filter above the table (one component, StatusTable prop or wrapper), substring match across key, branch, pr, worktree path — mirrors Runs page filter pattern (URL-param state + clearable chip, Runs.tsx:27-51).
- Tests: added_at setdefault semantics; sort order; filter matching.

### C5. Bulk actions + fake worktrees (G8)
- **Review all (web):** add `--all/--sequential/--fix` to webapp review `BOOL_FLAGS` (currently 400s). New "Review all" button on Dashboard + Links (confirm dialog mirroring Sync all), single run `review --all`, target `review:all`.
- **Cleanup merged (web + CLI):** new CLI flag `harness cleanup --merged` (ref optional with the flag): loops in-process over links whose PR/MR state is merged/closed (source of truth: `_status_cells` PR data / recorded URL fetch), cleaning each with the same per-key semantics (`--yes` implied for the loop; skips worktrees with live harness guard — the #6 guard applies). Target key `cleanup:all` in webapp SPECS. New "Cleanup merged" button + confirm.
- **Fake/buggy worktrees:** `_status_cells` sets additive `wt_valid: bool` from `worktrees.is_valid_worktree`: dir missing → `false` immediately (no subprocess); dir present → run the git checks (one `rev-parse` + one `worktree list` per session per poll, ~5ms each — acceptable for the 15s poll with few sessions). `false` also marks the existing `commits="gone"` state. Frontend: row highlight (red tint/badge "invalid") + per-row Delete action (existing cleanup flow; for unresolvable refs offer direct `git worktree remove --force` + link drop).
- CLI mirror: status table marks invalid rows (e.g. `!invalid` suffix on key).
- Tests: wt_valid true/false; cleanup --merged selection loop; guard interaction.

### C6. Candidates (G9)
- **CLI:** `harness candidates` — two sections:
  1. **Unlinked PR/MRs:** for each registered repo (`repos.repo_names()`), list open PR/MRs (`gh pr list --state open` / `glab mr list -F json`) minus URLs already recorded in links.json (`pr_url` fields) → candidates.
  2. **Recent issues:** same query as C2 (`list_my_issues`), i.e. reported-by-me, To-Do/In-Progress (jira; open for GH), created within last week, newest first.
- Table output (Rich, `--json`/`--csv` like other commands): `key-or-url, title, repo, created/updated, status`. Selecting a candidate is out of scope (user copies ref / uses `harness start`).
- **Web:** Candidates card on Links page (or its own page — implementer's judgment, Links fits): two tabs reusing the same data; fetch via new `GET /api/candidates` reusing `trackers.list_my_issues` + the PR listing helper. 15s-poll not needed; fetch on mount + manual refresh button.
- Tests: exclusion of already-linked PR URLs; date-window filter; fixture parsing.

## Non-goals
- No auth on webapp (existing posture).
- No CI logs/detail view — status badge only.
- No candidate auto-start/link flows.
- Windows opener best-effort (`explorer`), not tested in CI (Linux dev machine).
- gh has no To-Do/In-Progress — its issue list is "open, authored by me"; documented in API_CONTRACT.md.

## Contract changes (API_CONTRACT.md must document all)
1. `WorktreeEntry` additive: `added_at`, `wt_valid`, and top-level `ci` status string on status endpoints (`success|failure|running|not_started|""` for no-PR).
2. New endpoints: `GET /api/issues`, `GET /api/candidates`; new run command `open`, new flag sets (review `--all/--sequential/--fix`, cleanup `--merged`), and `/api/links` key `sessions` → `worktrees`.

## Verification (per phase)
- A: `uv run pytest` picker terminator tests fail pre-fix, pass post-fix; recorded-PR-wins test; live smoke `harness cd <ambiguous>` in a pty.
- B: CI fixtures mocked at run_cmd seam; live smoke against a real repo if available; web build + eslint.
- C: full suite; web build + eslint; `harness serve` smoke exercising renamed key, open button (disabled network_exposed case), dropdown fetch (mock), bulk buttons (dry-run/child spawn), candidates rendering.
