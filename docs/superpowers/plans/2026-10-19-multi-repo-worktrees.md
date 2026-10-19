# Multi-repo worktrees Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `start <issue>` fans out one child worktree per `--related-repo`, linked via `parent_key_ref`, shown in the prompt and sub-grouped in the Dashboard, cascade-cleaned, with a conditional headless MR suffix.

**Architecture:** Schema-first: add two worktree columns with the ALTER-TABLE migration template, thread them through the store row↔dict mapping, then build start/cleanup/prompt on top; web arg passthrough + StartForm multi-select + StatusTable grouping last. Each task is independently testable and committable.

**Tech Stack:** Python (Typer CLI, sqlite3, FastAPI webapp), pytest, React + TypeScript (Vite, Tailwind, Radix via `components/ui`), `tsc -b`, `npm run build`.

**Spec:** `docs/superpowers/specs/2026-10-19-multi-repo-worktrees-design.md`

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Python files and React components/classes must not exceed 500 lines.
- New `web/src` UI control lives in its own component file, never inside `StartForm.tsx` (308 lines).
- Never key worktrees/branches on branch name alone; the `UNIQUE (repo_key, branch)` composite holds.
- Every sync must end with a push; reviews/prompt changes never alter merge policy.
- CHANGELOG entries ≤ 150 chars, `[no-issue]` suffix, gap-insert under `## Unreleased` (never range-replace).
- Parity checklist per feature: CLI flag → web arg passthrough → API_CONTRACT.md entry → frontend control → both test files.
- TDD: write the failing test first, watch it fail, minimal implementation, watch it pass, commit.

## Review Focus

- Stale `state.db` opened after upgrade gets the new columns with correct defaults, or `_INIT_CACHE` skips the migration forever.
- `save_links_rows` full-snapshot writes never purge child rows (filtered-snapshot deletes).
- Re-running `start` with the same `--related-repo` set adopts children instead of UNIQUE-crashing.
- `cleanup --merged` never processes the same child twice (stale snapshot + cascade).
- Child with unresolvable/missing parent still renders somewhere (never silently dropped from the table).

---

### Task 1: Store schema — `related_repos` + `parent_key_ref` columns and migration

**Files:**
- Modify: `src/workagent/store_sqlite.py:30-37` (SCHEMA worktrees block), `:208-219` (after `_migrate_worktrees_deleted_at`), `:472-476` (`_schema_current` worktrees probe), `:514-535` (`init_db` chain)
- Modify: `src/workagent/store_links.py:39-45` (read overlay), `:66-68` (payload exclusion set), `:90-105` (upsert)
- Test: `tests/test_store_related_repos.py` (new file)

**Interfaces:**
- Consumes: `_migrate_worktrees_deleted_at` template (`store_sqlite.py:208-218`); `load_links_rows`/`save_links_rows` (`store_links.py:15-112`).
- Produces: `related_repos: list[str]` (repo keys) and `parent_key_ref: str` (`''` = top-level) present in every `load_links()` entry dict; migration function `_migrate_worktrees_related(conn) -> None`.

- [ ] **Step 1: Write the failing migration + roundtrip tests**

```python
"""Parent/child worktree columns: migration defaults + row roundtrip."""
import sqlite3
from workagent import store
from workagent.store_sqlite import _schema_current, init_db, db_path


def _raw_cols(path):
    conn = sqlite3.connect(str(path))
    try:
        return {r[1]: r[2] for r in conn.execute("PRAGMA table_info(worktrees)")}
    finally:
        conn.close()


def test_migration_adds_columns_with_defaults(isolated_config, tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE worktrees (ref_key TEXT PRIMARY KEY NOT NULL,"
                 " path TEXT UNIQUE NOT NULL, branch TEXT NOT NULL,"
                 " repo_key TEXT NOT NULL, issue_url TEXT NOT NULL DEFAULT '',"
                 " pr_url TEXT NOT NULL DEFAULT '', added_at TEXT NOT NULL,"
                 " payload TEXT NOT NULL DEFAULT '{}',"
                 " active INTEGER NOT NULL DEFAULT 1,"
                 " deleted_at TEXT NOT NULL DEFAULT '',"
                 " UNIQUE (repo_key, branch))")
    conn.execute("INSERT INTO worktrees (ref_key, path, branch, repo_key, added_at)"
                 " VALUES ('jira:IPG-1', '/tmp/wt', 'b', 'r', '2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    assert _schema_current(db) is False
    init_db(db)
    cols = _raw_cols(db)
    assert cols["related_repos"] == "TEXT"
    assert cols["parent_key_ref"] == "TEXT"
    assert _schema_current(db) is True


def test_roundtrip_parent_and_child(isolated_config):
    store.record_link("jira:IPG-1@main", {"issue": "IPG-1", "worktree": "/tmp/wt1",
                                          "branch": "feat/x", "repo": "/tmp/main",
                                          "related_repos": ["/tmp/other"]})
    store.record_link("jira:IPG-1@other", {"issue": "IPG-1", "worktree": "/tmp/wt2",
                                           "branch": "feat/x", "repo": "/tmp/other",
                                           "parent_key_ref": "jira:IPG-1@main"})
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert links["jira:IPG-1@main"]["related_repos"] == ["/tmp/other"]
    assert links["jira:IPG-1@main"]["parent_key_ref"] == ""
    assert links["jira:IPG-1@other"]["parent_key_ref"] == "jira:IPG-1@main"
    assert links["jira:IPG-1@other"]["related_repos"] == []
```

Check the `isolated_config` fixture name/shape in `tests/` conftest before finalizing (adapt paths: the real `db_path()` helper may differ — use whatever existing store tests use to point at a tmp DB). Also verify how existing migration tests simulate a legacy DB; mirror that file's approach.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_store_related_repos.py -v`
Expected: FAIL — `related_repos`/`parent_key_ref` unknown (migration missing; upsert drops them into `payload` instead of columns).

- [ ] **Step 3: Minimal implementation**
  1. `store_sqlite.py` SCHEMA worktrees block: append `related_repos TEXT NOT NULL DEFAULT '[]',` and `parent_key_ref TEXT NOT NULL DEFAULT ''` before the `UNIQUE` line.
  2. After `_migrate_worktrees_deleted_at` add:
```python
def _migrate_worktrees_related(conn) -> None:
    """Worktrees gains related_repos (JSON repo-key list, parent rows) and
    parent_key_ref (child → parent ref_key, '' = top-level).

    ALTER TABLE is enough (NOT NULL DEFAULTs, so old rows read back as
    top-level childless). Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worktrees)")}
    if not cols:
        return
    if "related_repos" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN related_repos TEXT NOT NULL DEFAULT '[]'")
    if "parent_key_ref" not in cols:
        conn.execute("ALTER TABLE worktrees ADD COLUMN parent_key_ref TEXT NOT NULL DEFAULT ''")
```
  3. `init_db` chain: call `_migrate_worktrees_related(conn)` after `_migrate_worktrees_deleted_at(conn)`.
  4. `_schema_current`: after the `deleted_at` probe add `if "related_repos" not in wcols: return False` and `if "parent_key_ref" not in wcols: return False`.
  5. `store_links.load_links_rows` overlay: add `"related_repos": json.loads(r.get("related_repos") or "[]")` and `"parent_key_ref": r.get("parent_key_ref", "") or ""`. (`json` already imported inside the function.)
  6. `save_links_rows`: add both keys to the `extra` exclusion tuple; extend the INSERT column list + VALUES with `related_repos, parent_key_ref`; extend the `ON CONFLICT` update with `related_repos=excluded.related_repos, parent_key_ref=excluded.parent_key_ref` — but guard each with `has_related = "related_repos" in e` / `has_parent = "parent_key_ref" in e` exactly like `has_active`/`has_deleted`, so a re-save that doesn't carry them never resets stored values (critical: `record_link` merges dicts, but any caller passing a partial dict must not wipe the linkage). VALUES params: `json.dumps(e.get("related_repos", []))`, `str(e.get("parent_key_ref", "") or "")`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store_related_repos.py tests/test_webapp_core.py -q`
Expected: PASS (new tests + existing status/link tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_links.py tests/test_store_related_repos.py
git commit -m "store: worktrees related_repos + parent_key_ref columns with migration"
```

### Task 2: Start prompt — conditional MR suffix + `related_paths` param

**Files:**
- Modify: `src/workagent/backend.py:150-155` (`prompt_for_issue`), `src/workagent/cli_start.py:225-226` (headless suffix)
- Test: extend `tests/test_cli_start*.py` if a prompt test file exists, else append to the nearest start/prompt test module (check `tests/` for `prompt_for_issue` coverage first)

**Interfaces:**
- Consumes: `_push_target_lines(worktree, branch)` (`backend.py:143-147`).
- Produces: `prompt_for_issue(title, body, issue_ref, worktree="", branch="", related_paths=None)` where `related_paths: list[tuple[str, str]] | None`; new headless suffix string.

- [ ] **Step 1: Write the failing prompt tests**

```python
def test_prompt_for_issue_lists_related_paths():
    from workagent import backend
    p = backend.prompt_for_issue("T", "B", "IPG-1", worktree="/tmp/wt", branch="b",
                                 related_paths=[("other", "/tmp/wt2")])
    assert "Related repo worktrees" in p
    assert "- other: /tmp/wt2" in p


def test_prompt_for_issue_no_related_section_when_empty():
    from workagent import backend
    p = backend.prompt_for_issue("T", "B", "IPG-1", worktree="/tmp/wt", branch="b")
    assert "Related repo" not in p
```

And a CLI-level test asserting the headless suffix wording (adapt to the file's existing `_invoke`/runner helper):
```python
def test_start_headless_suffix_conditional_on_changes(...):
    # start --dry-run? No — suffix is appended in the non-dry-run path before
    # _run_harness. Patch _run_harness to capture the prompt, or invoke the
    # suffix-building code path the file's tests already use; assert:
    # "if there are any code changes" in prompt and
    # "open a PR/MR to the default branch" in prompt and
    # "do not commit" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest <chosen test file> -q -k "related or suffix or conditional"`
Expected: FAIL — `related_paths` unknown kwarg; suffix still says "create an MR/PR".

- [ ] **Step 3: Minimal implementation**
  1. `backend.prompt_for_issue`: add `related_paths: list[tuple[str, str]] | None = None` param; after `_push_target_lines`, if non-empty append:
```python
    if related_paths:
        lines = "\n".join(f"- {name}: {path}" for name, path in related_paths)
        prompt += ("\n\nRelated repo worktrees (apply cross-repo code changes"
                   f" here too):\n{lines}")
```
  2. `cli_start.py:226` → `prompt += ("\n\nWhen done: if there are any code changes, commit, push to this branch, and open a PR/MR to the default branch. If there are no code changes, do not commit — just report that.")`. PR-mode suffix (`:263-264`) untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest <chosen test file> -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/backend.py src/workagent/cli_start.py <test file>
git commit -m "start prompt: conditional MR suffix + related repo paths"
```

### Task 3: `start --related-repo` — child worktree fan-out

**Files:**
- Modify: `src/workagent/cli_start.py:46-63` (flag), `:188-199` (dry-run), `:200-236` (create + record + prompt + result)
- Test: `tests/test_cli_start_related.py` (new file)

**Interfaces:**
- Consumes: Task 1 columns via `store.record_link`; `repos.resolve_repo`, `repos.default_branch`, `repos.repo_name`, `repos.register_repo`; `gitwt.start_worktree`; `worktrees.recorded_key`; Task 2 `prompt_for_issue(..., related_paths=...)`.
- Produces: child link rows (`parent_key_ref` set), parent row `related_repos` (repo keys), `result["related_repos"]` (child worktree paths) for `--json`.

- [ ] **Step 1: Write the failing fan-out tests**

Mirror the mocking style of existing `test_cli_start*.py` (patch `gitwt.start_worktree`, `refs.fetch_issue`, `trackers.resolve_repo_for_ref`, `_run_harness`; use `isolated_config` + tmp repos). Cases:
```python
def test_start_creates_child_per_related_repo(...):
    # start ISSUE --repo main --related-repo other1 --related-repo other2
    # assert: 3 link rows; children have parent_key_ref == parent key;
    # parent row related_repos == [resolved other1 key, resolved other2 key];
    # captured prompt contains both child paths; children share parent branch.


def test_start_rerun_adopts_children(...):
    # run once, run again with same flags → still 3 rows, no HarnessError/UNIQUE failure.


def test_start_related_same_as_main_is_usage_error(...):
    # --related-repo <main> → exit code 2.


def test_start_pr_mode_rejects_related_repo(...):
    # start <pr-url> --related-repo x → exit code 2.


def test_start_dry_run_plans_children_without_creating(...):
    # --dry-run --related-repo other → result has planned children, zero new rows.
```

Child key derivation: use `refs.issue_key(parsed, <child repo name>)` exactly as the parent does at `cli_start.py:163` (`key = refs.issue_key(parsed, Path(r).name)`) — i.e. pass the child checkout's dir name. GitHub (`gh`/`glab` parsed) keys already embed the repo; only compute suffixed keys the same way the parent path does.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_start_related.py -q`
Expected: FAIL — `--related-repo` unknown option.

- [ ] **Step 3: Minimal implementation**
  1. Flag after `extra_prompt` (`cli_start.py:61`): `related_repo: Optional[list[str]] = typer.Option(None, "--related-repo", help="Related repo for a child worktree (repeatable; main repo excluded).")`.
  2. Thread through to `_start_from_pr` only to reject: PR-mode + non-empty `related_repo` → `_fail("--related-repo is issue-mode only…", EXIT_USAGE)`.
  3. Issue-mode, after the `register_repo` try/except (`:219-222`), before `prompt_for_issue` (`:223`):
```python
    related_infos: list[tuple[str, str]] = []  # (repo_name, worktree_path)
    if related_repo:
        if key and links.get(key, {}).get("parent_key_ref"):
            _fail(f"{key} is a child worktree — it cannot take --related-repo.", EXIT_USAGE)
        seen: set[str] = set()
        for rel in related_repo:
            child_repo = repos.resolve_repo(rel, Path.cwd(), depth=depth)
            if _same_repo_path(str(child_repo), str(r)):
                _fail(f"--related-repo {rel} is the main repo — pick a different repo.", EXIT_USAGE)
            rkey = str(child_repo)
            if rkey in seen:
                continue
            seen.add(rkey)
            child_default = repos.default_branch(child_repo)
            cwt = gitwt.start_worktree(child_repo, issue=issue_id, slug=slug, base=child_default)
            cpath, cbranch = cwt.get("worktree_path", ""), cwt.get("branch", "")
            cprior = worktrees.recorded_key(cpath, cbranch, store.load_links(include_inactive=True), repo=str(child_repo))
            ckey = cprior if isinstance(cprior, str) else refs.issue_key(parsed, Path(child_repo).name)
            store.record_link(ckey, {"issue": ref, "worktree": cpath, "branch": cbranch,
                                     "repo": str(child_repo), "parent_key_ref": key,
                                     **({"issue_url": link_url} if link_url and link_url.startswith("http") else {})})
            try:
                repos.register_repo(repos.repo_name(child_repo), child_repo)
            except HarnessError:
                pass
            related_infos.append((repos.repo_name(child_repo), cpath))
        store.record_link(key, {"related_repos": sorted(seen)})
```
  Use the file's existing `_same_repo_path` helper (`:100-101`, `:123` usage) for the main-repo comparison. `links` must be in scope — the parent flow already loaded it for the reuse check (`:119-137` region); if not in scope at `:222`, load `store.load_links(include_inactive=True)` once before the loop.
  4. Prompt call: `prompt_for_issue(..., related_paths=related_infos or None)`.
  5. Result dict: add `"related_repos": [p for _, p in related_infos]`.
  6. Dry-run (`:188-199`): add `"related_repos": list(dict.fromkeys(related_repo or []))` planned values without creating anything.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_start_related.py tests/test_cli_start*.py -q` (use the real start-test filenames)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_start.py tests/test_cli_start_related.py
git commit -m "start --related-repo fans out child worktrees with parent linkage"
```

### Task 4: Cleanup cascade parent → children

**Files:**
- Modify: `src/workagent/cli_cleanup.py:72-109` (`--merged` loop), `:301-465` (`_cleanup_one`), `:339-342` (dry-run plan — verify exact lines during implementation)
- Test: `tests/test_cli_cleanup_related.py` (new file)

**Interfaces:**
- Consumes: Task 1 columns (read `parent_key_ref`/`related_repos` from entry dicts); `_cleanup_one` recursion (depth ≤ 1 by the spec's single-level invariant).
- Produces: `result["children"]: list[dict]` on parent cleanup results; `"skipped:cleaned-with-parent"` rows in `--merged`.

- [ ] **Step 1: Write the failing cascade tests**

Mirror existing cleanup test mocking (`gitwt.cleanup_worktree`, `_merge_pr`, `_resolve_branch_pr`, `store.*`). Cases:
```python
def test_cleanup_single_cascades_to_children_soft(...):
    # parent + 2 children (children without PRs) → cleanup parent (default soft):
    # all three rows stamped deleted_at; result has children: [cleaned, cleaned].


def test_cleanup_single_cascades_child_pr_merge(...):
    # child with open PR → child merges first (squash rules), then soft-deletes.


def test_cleanup_hard_cascades_full_teardown(...):
    # --hard-delete → children torn down + rows dropped (mock gitwt/run_cmd).


def test_cleanup_dry_run_plans_children(...):
    # --dry-run output mentions both child keys, zero rows touched.


def test_cleanup_merged_skips_cascaded_child(...):
    # parent merged + child merged → --merged yields parent cleaned + child skipped:cleaned-with-parent (exactly one teardown of the child).
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_cleanup_related.py -q`
Expected: FAIL — children untouched, no `children` key, child processed twice under `--merged`.

- [ ] **Step 3: Minimal implementation**
  1. `_cleanup_one`, soft-delete branch: after `store.soft_delete_worktree(key)` (`:435`) and before `return` (`:439-440`), insert:
```python
        child_results = _cleanup_children(key, force, yes, dry_run, json_output, squash, merge, hard_delete)
```
and add `"children": child_results` to the returned dict.
  2. Hard-delete branch: same insert after `store.delete_worktree_row(key)` (`:456`), before `return` (`:461-462`).
  3. New helper in `cli_cleanup.py`:
```python
def _cleanup_children(parent_key, force, yes, dry_run, json_output, squash, merge, hard_delete) -> list:
    links = store.load_links(include_inactive=True)
    kids = sorted(k for k, e in links.items() if e.get("parent_key_ref") == parent_key)
    return [_cleanup_one(k, dict(links[k]), force, yes, dry_run, json_output,
                         squash=squash, merge=merge, hard_delete=hard_delete) for k in kids]
```
  No `related_repos` lookup needed — children are found by `parent_key_ref` scan, which also covers rows whose parent `related_repos` list drifted.
  4. Dry-run: if the dry-run plan branch returns before teardown, list planned children there too (read the exact lines during implementation; mirror the plan dict shape with `"children": [{"key": k, ...plan...}]`).
  5. `--merged` loop: maintain `cleaned: set[str]`; before processing a row, `if key in cleaned or (entry.get("parent_key_ref") or "") in cleaned:` append `{"key": key, "branch": branch, "status": "skipped:cleaned-with-parent"}` and continue; after `_cleanup_one`, add `key` plus every `c.get("key")` in `res.get("children", [])` to `cleaned`. Note: `cleaned` keys must only be added on `"status": "cleaned"` results — gate on `res.get("status") == "cleaned"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_cleanup_related.py tests/test_cli_cleanup*.py -q` (real cleanup test filenames)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_cleanup.py tests/test_cli_cleanup_related.py
git commit -m "cleanup cascades parent worktree to child worktrees"
```

### Task 5: Web passthrough + StartForm multi-select

**Files:**
- Modify: `src/workagent/web_args.py:66` (`VAL_FLAGS["start"]`)
- Create: `web/src/components/RelatedReposSelect.tsx` (new multi-select control, < 500 lines by construction)
- Modify: `web/src/lib/launchConfig.ts:48-75` (`LaunchForm` + `INITIAL`), `web/src/components/StartForm.tsx:154-160` (options memo), `:202-218` (repo field block), `:258-286` (`buildStartArgs`, `startFlagList`), `web/src/lib/launchPrefs.ts` (check persistence shape during implementation)
- Modify: `web/API_CONTRACT.md` (`start` args section)
- Test: `tests/test_webapp.py` mirror test (existing) + a `_validate_args` repeat-flags test (new, in the webapp test module that covers `_validate_args`)

**Interfaces:**
- Consumes: Task 3 `--related-repo`; existing `useRepos` repo names; `loadLaunchPrefs` shape.
- Produces: repeated `--related-repo <name>` argv entries from `buildStartArgs`; validated passthrough of repeats in `_validate_args`.

- [ ] **Step 1: Write the failing web tests**

```python
def test_validate_args_start_accepts_repeated_related_repo():
    from workagent.web_args import _validate_args
    args = ["IPG-1", "--repo", "main", "--related-repo", "other1", "--related-repo", "other2"]
    _validate_args("start", args)  # must not raise; returns argv carrying both pairs
```
Check the real `_validate_args` return/raise contract in `web_args.py:120-159` first and adapt (it may raise `HTTPException(400)` on bad args — assert no raise and both pairs preserved in order).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp*.py -q -k "related or mirror"`
Expected: FAIL — `bad_arg` on `--related-repo` (not in `VAL_FLAGS`) and/or the mirror test flags drift.

- [ ] **Step 3: Minimal implementation**
  1. `web_args.py:66`: append `"--related-repo"` to `VAL_FLAGS["start"]`. Verify the repo-ambiguity block (`:181-214`) iterates flag/value pairs generically so repeats pass; fix minimally if it assumes single `--repo` (only touch what the failing test proves).
  2. `launchConfig.ts`: `LaunchForm` gains `relatedRepos: string[]`; `INITIAL` gains `relatedRepos: []`.
  3. `RelatedReposSelect.tsx`: props `{ id, options: string[], value: string[], onChange: (v: string[]) => void }`; button showing `N selected`/`Select related repos…` + popover checkbox list; toggling updates without closing. Exclude the main repo in the parent (pass already-filtered options). Style with existing `ui/` primitives (`button`, `checkbox`, `popover` if present — check `components/ui/` first and use whatever exists; plain `div` + Tailwind otherwise).
  4. `StartForm.tsx`: after the repo field block, add the Related repos field (`FieldHelp` label "Related repos", flag `--related-repo`, description "Optional extra repos: one child worktree per repo, grouped under this one."). Options = `options` memo minus `form.repo`. `buildStartArgs`: `for (const r of form.relatedRepos) args.push("--related-repo", r)`. `startFlagList`: append `` `--related-repo ${r}` `` per entry (or `related: none`). Check `launchPrefs.ts` persistence shape — if it stores the whole start slice, `relatedRepos` persists automatically; if it allowlists fields, add it.
  5. `API_CONTRACT.md`: document `--related-repo` (repeatable) under the start/run-args section.

- [ ] **Step 4: Run tests + typecheck to verify they pass**

Run: `pytest tests/test_webapp*.py -q` and `npm run typecheck` (in `web/`)
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py web/src/components/RelatedReposSelect.tsx web/src/components/StartForm.tsx web/src/lib/launchConfig.ts web/src/lib/launchPrefs.ts web/API_CONTRACT.md tests/test_webapp*.py
git commit -m "web start form multi-selects related repos, passthrough flag"
```
(Only add files that actually changed; drop `launchPrefs.ts` from the command if untouched.)

### Task 6: StatusTable sub-grouping + contract/type updates

**Files:**
- Modify: `web/src/lib/api.ts` (`WorktreeEntry`), `web/src/components/StatusTableMain.tsx` (grouping between `keys` memo and render loops), `web/API_CONTRACT.md` (`/api/status` entry shape)
- Test: `tests/test_webapp_core.py` — extend `test_api_status_carries_active_and_deleted_at`-style coverage with a `related_repos`/`parent_key_ref` status test

**Interfaces:**
- Consumes: Task 1 store columns flowing through `_enrich_entry`'s `{**entry, ...}` spread (`cli_status.py:346`) — no endpoint change needed.
- Produces: grouped row order (parent immediately followed by its children, indented).

- [ ] **Step 1: Write the failing status test**

```python
def test_api_status_carries_related_and_parent(client):
    store.record_link("jira:IPG-20@main", {"issue": "IPG-20", "worktree": "/tmp/wt20",
                                           "branch": "b", "repo": "/tmp/main",
                                           "related_repos": ["/tmp/o"]})
    store.record_link("jira:IPG-20@other", {"issue": "IPG-20", "worktree": "/tmp/wt21",
                                            "branch": "b", "repo": "/tmp/o",
                                            "parent_key_ref": "jira:IPG-20@main"})
    body = client.get("/api/status", params={"include_inactive": "true"}).json()
    assert body["jira:IPG-20@main"]["related_repos"] == ["/tmp/o"]
    assert body["jira:IPG-20@other"]["parent_key_ref"] == "jira:IPG-20@main"
```
(Adapt the `client` fixture import to the module's existing pattern.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webapp_core.py -q -k "related_and_parent"`
Expected: FAIL — keys missing from entries (store mapping not yet threading them; if Task 1 is done this passes already — then this test is a characterization lock, still keep it).

- [ ] **Step 3: Minimal implementation**
  1. `api.ts` `WorktreeEntry`: add `related_repos?: string[]` and `parent_key_ref?: string` with doc comments (do NOT drop `added_at` — it was accidentally deleted once before; verify `git diff` shows only additions).
  2. `StatusTableMain.tsx`: after the `keys` memo, build `orderedKeys`: top-level keys (`!entry.parent_key_ref`) in existing order; after each parent, its children (`entry.parent_key_ref === parentKey`) sorted `added_at` desc; children whose parent key is absent render at the end as top-level. Replace both `keys.map` loops (desktop + mobile) with `orderedKeys.map`; render child rows with an indent (e.g. `pl-6` + a `└` marker or left border) and pass through to the same row/cell components unchanged. Select-all checkbox scope follows `orderedKeys` (same set, new order).
  3. `API_CONTRACT.md` `/api/status` example + prose: document `related_repos` (repo keys, parent rows) and `parent_key_ref` (child → parent key, `""` top-level).

- [ ] **Step 4: Run tests + typecheck + build to verify they pass**

Run: `pytest tests/test_webapp_core.py -q`, `npm run typecheck`, `npm run build` (in `web/`)
Expected: PASS, clean, build ok.

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/components/StatusTableMain.tsx web/API_CONTRACT.md tests/test_webapp_core.py
git commit -m "dashboard groups child worktrees under their parent row"
```

### Task 7: Full verification, changelog, push, reinstall

**Files:**
- Modify: `CHANGELOG.md` (gap-insert under `## Unreleased`)

**Interfaces:**
- Consumes: Tasks 1-6.
- Produces: pushed `main` + reinstalled CLI.

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q` (repo root per AGENTS.md: `cd /home/bs/projects/personal/harness` — verify the actual test root during implementation; AGENTS.md says harness, repo-rules say workagent — use the directory containing `tests/`)
Expected: all PASS.

- [ ] **Step 2: Run frontend checks**

Run: `npm run typecheck` and `npm run build` (in `web/`)
Expected: clean + build ok.

- [ ] **Step 3: Live smoke**

Run: `workagent status --json` against the real DB (or a scratch `start --dry-run` with `--related-repo`) to confirm the CLI accepts the flag and the payload carries the new fields.
Expected: exit 0, fields present.

- [ ] **Step 4: Changelog + push + reinstall**

```bash
# gap-insert under ## Unreleased (PUT >2 style), ≤150 chars, [no-issue] suffix:
# - `start --related-repo` fans out child worktrees per repo (prompt paths, dashboard grouping, cascade cleanup) [no-issue]
git add CHANGELOG.md
git diff -- CHANGELOG.md  # MUST show only additions (+), zero deletions
git push  # on main per repo rules (auto-commit/push on main)
./install.sh 2>&1 | tail -3
```
Also `git diff --stat` review: every touched file must be in the parity checklist (CLI → web arg → contract → frontend → tests).

- [ ] **Step 5: Commit** (if changelog not yet committed in Step 4 — combine: single commit for changelog, then push, then reinstall)
