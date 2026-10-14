# Release Action — Design Spec (S3)

Date: 2026-10-14. Status: awaiting user review.
Terminology: **Release** everywhere (backend + frontend). The word Deploy MUST NOT appear in command names, flags, columns, buttons, or components.

## 1. Outcome

Repo owners ship `default → release` through a Merge Request that merges
fast-forward-only with auto-merge, from two surfaces: a Release overlay
button on each Repos page row and a "Ready to release" card on the
Dashboard. A repo is releasable only when it has a persisted release
branch AND the default/release diff is non-empty. Everything else is
disabled with a reason, never hidden.

## 2. Schema (repos table)

- `repos.default_branch TEXT NOT NULL DEFAULT ''` — mandatory for every repo.
- `repos.release_branch TEXT NOT NULL DEFAULT ''` — empty = no release support.
- Backfill on migration: for each existing repo, `repos.default_branch(path)`
  (gh/glab → origin/HEAD → main/master/develop); release branch: Jibit
  (GitLab) repos prefer `master` when it exists else `main` when it exists
  else empty; non-Jibit repos empty unless already configured. Backfill never
  raises: failure leaves `''` + stderr warning.
- Editable: `repo update NAME --default-branch X --release-branch Y`
  (empty string clears release support). Surfaced in the Repos page Add form
  (optional release-branch field) + per-row edit.
- `register_repo` seeds both at registration time via the same detection.

## 3. Diff gate (releasability)

- `release_diff(repo)`: `git fetch origin` + `git rev-list --count
  release..default` (direction: what default has that release lacks).
  Non-zero count = releasable.
- Cache: new `release_diff_cache(repo_key, count, fetched_at)` table,
  fresh 1h, SWR 1d via the existing `swr.serve_stale_or_refresh` primitive
  (same pattern as `pr_candidates_cache` / `issue_cache`). Expired cache
  serves stale counts instantly with one background revalidation; empty
  cache fetches live. `force` re-fetches inline.
- Releasable = `release_branch != '' AND default_branch != '' AND count > 0`.

## 4. Release command (CLI owns the logic)

`workagent release <repo> [--yes] [--dry-run] [--json]`:

1. Resolve repo (registered name/path; reject unknown exit 2).
2. Gate: missing release branch → exit 2 "no release branch configured";
   zero diff → exit 0 "already in sync, nothing to release".
3. Open MR `default → release` via host CLI (`glab mr create
   --source-branch <default> --target-branch <release>` / `gh pr create
   --base <release> --head <default>`). Reuse of an existing open
   default→release MR: adopt it instead of opening a duplicate.
4. Set auto-merge FF-only on the MR (`glab mr merge --ff-only --auto-merge`
   / `gh pr merge --auto --ff-only` equivalent). One remote-rebase retry
   when the MR reports conflicts: `glab mr rebase` (host-side), then
   re-check mergeability.
5. Fail-back: rebase fails, still unmergeable (approvals, red CI,
   permissions, true conflicts), or host rejects FF → leave the MR open,
   touch neither branch, exit non-zero with the host's reason. NEVER merge
   commit, NEVER squash, NEVER force-push, NEVER direct-push to release.

Exit codes: 0 released-or-nothing-to-do, 1 host failure, 2 usage/gate.
`--dry-run` prints the plan (branches, diff count, MR title) without acting.
`--json` emits `{repo, default_branch, release_branch, diff_count,
mr_url, merged}` on stdout; logs on stderr (stdout data-only rule).

## 5. Web wiring (CLI owns logic; web mirrors flags)

- `BOOL_FLAGS`/`VAL_FLAGS` gain `release` entries; `_validate_args` accepts
  `--yes/--dry-run/--json`; `SPECS["release"] = {"confirm": True, ...}`.
- `GET /api/release/status?name=` → `{repo, default_branch,
  release_branch, diff_count, releasable, reason, stale}` (read-only, SWR).
- `POST /api/runs {command: "release", args: [...]}` runs the CLI child;
  completion mirrors like other commands. Parity checklist applies:
  `API_CONTRACT.md` + `FEATURES-NOTES.md` + frontend + both test files.
- Frontend: `useReleaseStatus(name)` query (`staleTime` = 1h, `gcTime` = 1d).

## 6. UI

- **Repos page rows**: Release overlay button (same `StatusTableMain`
  `inset-y-1 right-1 group-hover` pattern as StatusTable row actions):
  enabled + rocket icon when `releasable`, disabled with reason tooltip
  (`title`: "no release branch" / "already in sync") otherwise. Click →
  confirm dialog (branches, diff count, MR title) → release run → toast +
  navigate to run.
- **Dashboard "Ready to release" card**: sibling card after `<StatusTable>`
  (same mount pattern as `<CandidatesCard />`): one row per releasable repo
  (non-releasable omitted — the card title carries the count), columns:
  Repo | default → release | diff count (relative fetch time, exact on
  hover) | release-branch CI status (badge + permanent pipeline link, same
  pattern as the StatusTable CI cell via `refs.fetch_ci_status`) | Release
  overlay button (same component as the Repos row). Stale-while-revalidate:
  cached rows render instantly + `<RefreshOverlay />` while revalidating;
  skeleton only on first load.
- Release-branch CI link: `fetch_ci_status(tool, repo, release_branch)` —
  extend the existing helper with a branch param rather than a new fetcher.

## 7. Testing

- CLI: gate tests (no branch → 2, zero diff → 0 no-op), MR-create argv per
  host, FF-only merge flag present, rebase-retry once then fail-back, never
  squash/merge-commit (assert argv), `--dry-run` no-op.
- Cache: stale-serve + background single-flight (same shape as the PR/issue
  SWR tests), force bypass.
- Schema: migration backfills defaults without raising on unreachable hosts.
- Web: specs-mirror test, `/api/release/status` stale flag, frontend
  `tsc` + build.
- Full suite green + `./install.sh` + commit/push before done.

## 8. Out of scope

Tag-based releases, changelog automation, multi-repo batch release,
scheduled/auto release on push, rollback command, non-FF strategies.
