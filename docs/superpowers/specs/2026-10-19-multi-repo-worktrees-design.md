# Multi-repo worktrees (`related_repos` / `parent_key_ref`) + conditional Start MR prompt

Date: 2026-10-19. Status: proposed — awaiting user review before `writing-plans`.

## 1. Intent

A single issue's code changes often spread across multiple repos. Today `start`
creates exactly one worktree in one repo, so cross-repo work has no recorded
linkage and the harness prompt never mentions the other checkouts.

Goal: `start <issue>` optionally fans out one child worktree per related repo,
records the parent↔child linkage in the DB, surfaces child paths in the Start
prompt, groups children under the parent in the Dashboard StatusTable, and
cascades cleanup. Separately, the headless Start suffix stops telling the agent
to always open an MR — only when there are code changes.

Assumptions (confirmed with user): child branch = same branch name as parent;
multi-select options = all registered repos except main; cleanup cascades;
prompt change scoped to the headless (`--no-tty`) issue-mode suffix only.

## 2. Scope

In scope:

- `worktrees.related_repos` (JSON list of repo keys, parent row only) and
  `worktrees.parent_key_ref` (child row → parent `ref_key`, `''` = top-level).
- `start` gains repeatable `--related-repo NAME`; per-repo child worktree
  creation + child link records; child paths appended to the Start prompt.
- Headless issue-mode suffix becomes conditional on code changes.
- `cleanup` cascades parent → children (merge-if-open-PR, teardown, drop).
- Web passthrough (`VAL_FLAGS`), StartForm multi-select, StatusTable
  parent/child sub-grouping, API contract + TS type updates.
- Tests: migration, start fan-out + idempotent re-run, prompt text, cascade
  (soft/hard/dry-run/`--merged` skip), webapp flag mirror.

Out of scope:

- `related_repos` on PR-mode start (`start <pr-url> --related-repo` exits 2
  with a usage error); `review`/`fix-comments` multi-repo; linking one MR
  across repos; syncing parent+children as a unit (each worktree syncs
  independently, as today).

## 3. Data model + migration

No numeric schema version exists; evolution is ordered idempotent migrations in
`init_db` (`src/workagent/store_sqlite.py:514-549`). Follow the
`_migrate_worktrees_active` (`:197-207`) / `_migrate_worktrees_deleted_at`
(`:208-219`) ALTER-TABLE template — NOT the rebuild pattern (rebuilds are for
constraint changes; SQLite cannot add FK clauses via ALTER, so `parent_key_ref`
is a plain TEXT column with no DB-level FK, matching the `active`/`deleted_at`
precedent):

- `SCHEMA` worktrees block (`store_sqlite.py:30-37`) gains:
  `related_repos TEXT NOT NULL DEFAULT '[]'`,
  `parent_key_ref TEXT NOT NULL DEFAULT ''`.
- New `_migrate_worktrees_related(conn)`: `PRAGMA table_info` probe, two
  `ALTER TABLE … ADD COLUMN` statements, chained in `init_db` after
  `_migrate_worktrees_deleted_at` (`:535`).
- `_schema_current` (`:449-497`) probes both new columns or stale DBs skip the
  migration forever via `_INIT_CACHE`.
- Read path: `store_links.load_links_rows` (`store_links.py:37-46`) adds both
  keys to the `entry.update({...})` overlay.
- Write path: `save_links_rows` (`store_links.py:57-59`, `:91-105`) excludes
  both keys from the `extra`→payload set and names them in the explicit upsert
  column list + `ON CONFLICT` update.
- Single-level invariant (enforced in code, not DB): a row with non-empty
  `parent_key_ref` never carries `related_repos`. `start` refuses
  `--related-repo` when the resolved parent key already has `parent_key_ref`
  set (a child can never become a parent).

`related_repos` holds **repo registry keys** (`repos.key_ref`), in selection
order. Children records are keyed per the existing per-repo scheme
(`jira:KEY@<repo-name>` via `repos.repo_name`), same branch name as the parent.

## 4. Start flow (`src/workagent/cli_start.py`)

- Flag after `extra_prompt` (`:61`):
  `related_repo: Optional[list[str]] = typer.Option(None, "--related-repo", help="…(repeatable)")`.
  PR-mode + `--related-repo` → exit 2 usage error.
- Issue-mode child loop between the `register_repo` block (`:217-222`) and
  `prompt_for_issue` (`:223`):
  1. Dedupe values; resolve each via `repos.resolve_repo(value, cwd, depth)`
     (registered name / local path / clone URL, same as `--repo`).
  2. Reject a value resolving to the main repo `r` (exit 2 naming it).
  3. `repos.default_branch(child)` → `gitwt.start_worktree(child,
     issue=issue_id, slug=slug, base=child_default)` (same slug ⇒ same branch
     name; `--resume` adoption makes re-runs idempotent, same as parent).
  4. Reuse-check via `worktrees.recorded_key(...)` (parent pattern, `:211-213`);
     `store.record_link(child_key, {"issue": ref, "worktree":…, "branch":…,
     "repo": str(child), "parent_key_ref": key})`;
     `repos.register_repo(repos.repo_name(child), child)`.
  5. Persist `related_repos` (resolved repo keys) on the parent row via
     `store.record_link(key, {"related_repos": […]})`. A child-loop failure
     aborts with an error naming the repo and what was already created; the
     parent stays usable.
- `--dry-run` result includes the planned children (repo + branch) without
  creating anything.
- Result dict (`:228-231`) gains `"related_repos"` (child worktree paths) for
  `--json` consumers.

## 5. Prompt (`src/workagent/backend.py`, `cli_start.py:226`)

- `prompt_for_issue` gains `related_paths: list[tuple[str, str]] | None`
  (`(repo_name, worktree_path)`), appended `_push_target_lines`-style:
  `"\n\nRelated repo worktrees (apply cross-repo code changes here):\n- <name>: <path>…"`.
- Headless suffix (`cli_start.py:225-226`) becomes: `"When done: if there are
  any code changes, commit, push to this branch, and open a PR/MR to the
  default branch. If there are no code changes, do not commit — just report
  that."` PR-mode suffix (`:263-264`) untouched.

## 6. Cleanup cascade (`src/workagent/cli_cleanup.py`)

Per-worktree semantics inside `_cleanup_one` (`:301-465`), after the parent
teardown has definitively succeeded (merge-or-close raises before teardown
otherwise, `:386-397`):

- Resolve children from a fresh `store.load_links(include_inactive=True)`
  filtered on `parent_key_ref == key` (covers deactivated children).
- Recurse `_cleanup_one(child_key, child_entry, same force/yes/dry_run/json/
  squash/merge/hard_delete)`; collect into `result["children"]`. Single-level
  invariant ⇒ recursion depth ≤ 1 by construction.
- Dry-run: the plan branch (`:339-342`) lists planned child cleanups.
- Soft-delete (default, `:430-440`) cascades child soft-deletes (files/branches
  survive, matching parent semantics); hard-delete (`:441-456`) cascades full
  teardown + row drops.
- `--merged` loop (`:74-101`): track cleaned keys in a set; skip rows whose key
  or `parent_key_ref` is already cleaned (`"skipped:cleaned-with-parent"`) so a
  cascaded child is not re-processed from its stale snapshot.

Children keep participating in `sync --all` / `review --all` as normal
worktrees (they can accumulate their own PRs); no bulk-op exclusion.

## 7. Web + frontend

- `src/workagent/web_args.py`: `VAL_FLAGS["start"]` (`:66`) gains
  `"--related-repo"`. Repeated occurrences already pass `_validate_args`
  (`:131-146`, each occurrence consumed independently) and `_build_argv`
  (`:224-241`) passes them through verbatim; verify the single-`--repo`
  assumption in the repo-ambiguity block (`:181-214`) tolerates repeats.
  `tests/test_webapp.py::test_specs_mirror_cli_flags` guards parity.
- `web/src/components/StartForm.tsx`: new multi-select over the existing repo
  options union (`candidates` + `repoNames`, `:110-117`), minus the chosen main
  repo. No multi-select component exists — build a checkbox-list dropdown
  (button + popover, toggle without closing, selected-count label). `LaunchForm`
  (`launchConfig.ts:24-46`) gains `relatedRepos: string[]`; `buildStartArgs`
  (`:252-266`) appends `--related-repo <name>` per entry; `startFlagList`
  (`:268-284`) shows them; prefs persist like other fields. Shared
  `StartFormFields` ⇒ Launch page + candidate Start dialog both get it.
- `/api/status` needs no endpoint change: new fields flow through
  `_enrich_entry`'s spread (`cli_status.py:344-353`) once the store exposes
  them. `WorktreeEntry` (`web/src/lib/api.ts`) gains `related_repos?: string[]`
  and `parent_key_ref?: string`; `web/API_CONTRACT.md` documents both.
- `StatusTableMain.tsx`: between the `keys` memo (`:99-117`) and the two
  `keys.map` render loops (desktop `:208-302`, mobile `:305-400`), build
  parent→children order: top-level rows (empty `parent_key_ref`) sorted as
  today; each parent immediately followed by its indented child sub-rows
  (children sorted `added_at` desc). Children with unresolvable parents render
  top-level. Selection/bulk ops treat child keys as ordinary keys.

## 8. Tests (TDD: failing-first per case, then implement)

- `tests/test_store_sqlite*.py` (or nearest store test file): legacy DB without
  the columns → open → columns exist with defaults (`'[]'` / `''`);
  `_schema_current` False before / True after; record/load roundtrip preserves
  `related_repos` + `parent_key_ref` (and they do NOT leak into `payload`).
- `tests/test_cli_start*.py`: start with two `--related-repo` creates parent +
  2 children with `parent_key_ref` set, prompt contains both child paths,
  parent row carries repo keys; re-run adopts existing children (no duplicate
  rows); related == main → exit 2; PR-mode + `--related-repo` → exit 2;
  dry-run plans children without creating.
- Prompt unit test (`backend.prompt_for_issue` with `related_paths`) + headless
  suffix wording test (conditional MR language, no unconditional "create an
  MR/PR").
- `tests/test_cli_cleanup*.py`: single cleanup cascades to children (soft:
  children stamped, files survive; hard: children torn down + rows dropped;
  child with open PR merges first per squash rules); dry-run plans children;
  `--merged` skips already-cascaded children (`skipped:cleaned-with-parent`).
- `tests/test_webapp.py` parity (existing mirror test) + StartForm args test if
  a frontend test harness exists (check first; else rely on typecheck + build).
- Full suite + `tsc -b` + `npm run build`; CHANGELOG entry (≤150 chars,
  `[no-issue]`, gap-insert under Unreleased); parity checklist per AGENTS.md
  (CLI flag → web arg → contract → frontend control → both test files).

## 9. Risks

- `save_links_rows` deletes rows absent from the dict — child creation must go
  through `record_link` (unfiltered read-merge-write, `store.py:229-243`), never
  a filtered snapshot.
- Repo-name → child-key derivation must match the existing `jira:KEY@repo`
  scheme exactly; verify key construction in `start` during implementation
  (scout did not pin the exact line).
- Web `_validate_args` ambiguity block may assume one `--repo`-like value;
  confirm repeats pass before frontend work lands.
- `web/src` files ≤ 500 lines: the new multi-select control must live in its
  own component file, not inside `StartForm.tsx` (308 lines + additions).
