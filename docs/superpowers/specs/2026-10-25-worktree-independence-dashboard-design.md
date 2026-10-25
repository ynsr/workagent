# Worktree Independence + Dashboard Upgrades — Design

Date: 2026-10-25. Approved in chat 2026-10-25.

## Outcome
- Related-repo parent/child worktrees become fully independent: no action
  (cleanup single/`--merged`, sync, deactivate) ever cascades to the other
  worktree. `parent_key_ref`/`related_repos` remain as display-only grouping.
- Deleted card gains a purge (DB hard-delete) with a per-repo dropdown
  (only repos that have deleted rows).
- Candidates Unlinked PR/MRs rows compact: `#N · branch · relative date` +
  highlighted author badge + `repo_name` badge; full key/URL in tooltip.
- ReleaseCard gains a "Fix default" modal: Rebase default onto release /
  Fix CI / Both, with Launch-style run-mode + extra prompt, executed via a
  new `release fix-default` CLI action + harness.

## Constraints
- CLI owns logic; web mirrors via `createRun`/API contract + parity
  checklist (CLI flag → web arg → API_CONTRACT.md → frontend → both tests).
- New files/components ≤500 lines; follow existing patterns (StatusTable
  actions, StartForm RunModeSelect, DeletedTable, ReleaseCard).
- stdout data-only; destructive paths need `--yes`/confirm + `--dry-run`.

## 1. Parent/child independence
- `src/workagent/cli_cleanup.py`: delete `_cleanup_children` calls (single
  + `--merged` loop) and the `skipped:cleaned-with-parent` branch;
  `--merged` iterates every row (children evaluated on their own PR state).
- `_child_keys` stays only if the UI adopts orphans; otherwise remove.
- `StatusTableMain.tsx` grouping unchanged (indent + `child` badge).
- Child PRs merge/close solely via their own cleanup runs.
- Tests: parent cleanup leaves child row linked + worktree valid; `--merged`
  with merged parent + open child cleans parent only.

## 2. Deleted purge
- CLI: `link purge-deleted [--repo NAME] [--dry-run] [--json]` — drops
  soft-deleted rows from the DB (files/branches already gone; never touches
  disk). `--repo` scopes; no `--repo` = all deleted rows.
- `DeletedTable.tsx`: repo dropdown (repos present in `deletedAll`, default
  All) + Clear button → `confirm()` (irreversible wording) → createRun →
  invalidate `statusDeleted`.
- API contract + `webapp.py` SPECS/BOOL_FLAGS update; tests both sides.

## 3. Candidates compaction
- `refs.fetch_open_prs`: add `author` (gh `author{login}`, glab
  `author.username`, `""` fallback) → `_candidate_prs` → API type
  (`api.ts` PrRow + contract).
- Row: title link + author badge (highlighted, e.g. accent variant);
  subline `#{number} · {branch} · {relativeDate}`; `repo_name` badge only
  (drop full `repo` remote string); `title=` tooltip keeps
  `key · branch · repo · exact date`; keep Review/copy buttons.
- Tests: author present/absent; subline format; tooltip retention.

## 4. Release "Fix default"
- UI: `FixDefaultButton` + modal in `ReleaseCard.tsx` row: 3-part action
  select (Rebase / Fix CI / Both) + `RunModeSelect` + Extra prompt
  (import from `StartForm.tsx`), reusing `useConfirmedRun` → createRun.
- CLI: `release fix-default NAME --action rebase|ci|both [--run-mode
  preview|headless|terminal] [--extra-prompt TEXT] [--dry-run] [--json]`.
  - Rebase: fetch; if default behind release, open default→release MR (reuse
    `_find_or_open_mr` direction-swapped) and attempt remote rebase/merge via
    host API; on failure emit harness prompt (both SHAs, rebase strategy,
    no-new-commits) in chosen run mode.
  - CI: fetch default-branch pipeline status + failing logs, inject link +
    logs into harness prompt; harness fixes + pushes.
  - Both: rebase first, then CI; single harness session when headless.
- Confirm dialog warns default-history rewrite + force-push guard.
- Tests: dry-run plans per action; remote-fail → harness fallback prompt
  contents; web SPECS mirror test.

## Open follow-ups (implementation plan stage)
- Exact new command name if `release fix-default` collides with review.
- Whether `--merged` orphan-adoption notice is needed in output rows.
