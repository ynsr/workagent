# Worktree Entity Redesign — Design Spec

**Date:** 2026-09-21
**Issue:** ynsr/harness#2 — Sessions with Unique Branch Name
**Status:** Approved (in-chat section review)

## 1. Background

`harness review <ref>` creates duplicate state: new `pr:<url>` link keys and
new worktrees (e.g. `~/dev/worktrees/projectx/review`) even when a worktree
for the same branch already exists. Root cause: links are keyed only by exact
key string; no identity across branch name / worktree path / ref key. This
spec renames `Session` → `Worktree`, adds a triple-key uniqueness index, and
makes every subcommand resolve-before-create.

## 2. Goals

- `review`, `start`, `sync`, `cleanup` reuse the existing worktree when any
  of branch name, worktree path, or ref key matches.
- Single terminology: `Worktree` everywhere (CLI output, web types, docs).
- Old `links.json` entries keep working (lazy migration on load).
- Per-property cache TTLs for transient git/host-derived data.

## 3. Non-goals

- Changing the `git-wt` worktree layout on disk.
- Changing tracker↔repo relation semantics (`trackers.py` untouched except
  imports if renamed symbols move).
- Server-side rebase vs local merge strategy (unchanged).

## 4. Entities

### Worktree (persistent, in `links.json` keyed by ref key)

| Field | Required | Notes |
|---|---|---|
| `ref_key` | yes | e.g. `jira:IPG-929`, `pr:<mr-url>` |
| `path` | yes | worktree root, absolute |
| `branch` | yes | from worktree `.git` info |
| `tracker_url` | yes | full issue/MR URL |
| `repo` | yes | local repo root, absolute |

### Local Repo (persistent, in `config.json` `repos`)

| Field | Required | Notes |
|---|---|---|
| `name` | yes | e.g. `projectx` |
| `path` | yes | absolute |
| `tracker` | yes | e.g. `jira:IPG`; one tracker project may map to many repos |

Transient: remote address from `.git` info. Validation: path exists and is a
valid git repo (`git rev-parse --git-dir` exit 0).

### Issue Tracker Project (persistent, in `config.json` `trackers`)

| Field | Required | Notes |
|---|---|---|
| `key` | yes | e.g. `jira:IPG` |
| `remote` | yes | e.g. `https://tribe.jibit.cloud/projects/IPG` |
| `vendor` | yes | enum: `github` \| `gitlab` \| `jira` |

### Transient Worktree properties (never persisted except via cache)

| Property | Source | Cache TTL |
|---|---|---|
| Remote repo address | local `.git` config | none (cheap, always live) |
| PR/MR status | `gh`/`glab` | 3h (existing `pr_cache.json` behavior) |
| Behind/ahead vs base | local branch compare; `--remote` uses remote-tracking refs | 1h |
| Open/resolved comment threads | `gh`/`glab` | 1h |
| Latest PR/MR link | `gh`/`glab` list, most recent | 1d |
| Branch name | worktree `.git` info | none |
| Local repo path | `git rev-parse --path-format=absolute --git-common-dir` | none |

Default TTL 3h unless the table says otherwise. `--refresh-pr` (and
`--reset-cache` where it exists) invalidates all cached fields for the branch.

`isValid(path)`: path exists AND `git -C <path> rev-parse --git-common-dir`
succeeds AND `git worktree list` in the main checkout contains the path.

## 5. Uniqueness and lookup

Build an in-memory index at `load_links()` time over three keys: normalized
branch name, resolved absolute path, ref key. `resolve_worktree(ref)`:

1. Exact ref-key hit → return.
2. Branch-name hit (compare `entry.branch` and, as fallback, live
   `git -C <path> branch --show-current`) → return.
3. Path hit (resolve symlinks/`~`, compare against `entry.path`) → return.
4. Substring over keys/branches/paths: one hit → return with confirmation
   semantics per command; several → interactive pick; none → miss (create).

On create, assert none of the three keys collides; on collision, reuse the
existing entry and update its `pr_url`/`tracker_url` instead of inserting.

## 6. Command behavior changes

- `review <anything>`: parse → `resolve_worktree` → hit: reuse worktree, set
  `pr_url`, launch harness in place. Miss: fetch `head_ref` (with `cwd`,
  per #3 fix), `start_worktree`, record. Never `slug="review"`.
- `start`: same resolve-first; existing worktree for the issue key is resumed.
- `sync` / `cleanup`: resolve via the index (replaces `_resolve_session_key`
  exact/substring logic; keep its confirm-on-fuzzy-match UX).
- `status` / `link list`: display `Worktree` columns; rename `SessionMap`
  → `WorktreeMap` in `webapp.py` and `web/src/lib/api.ts`.

## 7. Files to touch

- New: `src/harness/worktrees.py` (index, `resolve_worktree`, `is_valid`).
- Modify: `src/harness/store.py` (lazy migration of old entries),
  `src/harness/cli.py` (`review`/`start`/`sync`/`cleanup`/`status` resolve
  path), `src/harness/webapp.py` (rename map type), `web/src/lib/api.ts`,
  `web/src/components/StatusTable.tsx`, `web/src/pages/Dashboard.tsx`,
  `web/src/pages/Links.tsx`, `tests/` (mirror names), `AGENTS.md`
  (Session→Worktree terminology), `CHANGELOG.md`.
- CLI stdout JSON keys stay backward compatible for one release where cheap
  (`worktree_path`, `branch` unchanged); renamed human labels change.

## 8. Verification

- Repro: record IPG-929 worktree entry; `review` same branch/MR URL/path →
  same path returned, no new branch, no `review` dir.
- Uniqueness: three entries colliding on each key → single entry after
  resolve+record cycle.
- Migration: old `links.json` (no `ref_key` field) loads and resolves.
- Full suite: `uv run pytest -v` green.
