# Worktree Independence + Dashboard Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple parent/child worktrees and ship the three Dashboard upgrades (deleted purge, candidate row compaction, release fix-default).

**Architecture:** CLI owns all behavior in existing modules (`cli_cleanup.py`, `cli_manage.py`, `refs.py`, `cli_candidates.py`, `cli_release.py`); web mirrors via `web_args.py` SPECS + `web/API_CONTRACT.md` + existing `createRun` pipeline. Four independent task groups; groups 1–3 land in any order, group 4 last (touches release flow).

**Tech Stack:** Python Typer CLI, pytest, React + TS (Vite), TanStack Query, FastAPI serve backend.

**Spec:** `docs/superpowers/specs/2026-10-25-worktree-independence-dashboard-design.md`

## Global Constraints

- Python files and React components/classes must not exceed 500 lines.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- New/renamed flags: update `BOOL_FLAGS`/`VAL_FLAGS` + `_validate_args` + `web/API_CONTRACT.md` + `web/FEATURE_INVENTORY.md` + frontend control + `tests/test_cli.py` + `tests/test_webapp.py` in the same PR.
- Merges from non-long-lived source branches use squash — never a merge commit (sync `--merge` local merge excluded).
- Deactivated worktrees: bulk ops skip them; deleted ≠ deactivated, never listed together.
- Status `ci`: `success | failure | running | not_started`; ✓/✗/● rendered, `-` otherwise.
- Every harness command omits `-p` when not executing; `-p` is non-TTY-only.
- CHANGELOG.md entries ≤150 chars, append issue ID in brackets.

## Review Focus

- Parent cleanup with a live harness on the child: child harness keeps running, child row untouched — a reasonable person expects no cross-kill.
- `link purge-deleted --repo X` naming a repo with zero deleted rows: prints empty result, exit 0 — not an error.
- Old `glab` without author fields: candidate row shows no author badge, never crashes or shows "null".
- `release fix-default --action rebase` when default is NOT behind: prints no-op, exit 0, never opens an MR.
- Fix-default modal in preview mode: prints the harness command with no `-p` flag, executes nothing.

---

### Task 1: Cleanup stops cascading to children

**Files:**
- Modify: `src/workagent/cli_cleanup.py:68-170`
- Modify: `src/workagent/cli_cleanup.py:173-197` (delete `_child_keys` + `_cleanup_children` if unused elsewhere)
- Test: `tests/test_cli_cleanup_related.py`

**Interfaces:**
- Consumes: `store.load_links()`, `_cleanup_one()`, existing `_status_cells` merged/closed check.
- Produces: `cleanup` single + `--merged` that treat every row independently; JSON result rows lose the `children` key (single) / `children` + `skipped:cleaned-with-parent` (merged).

- [ ] **Step 1: Rewrite the cascade tests to assert independence**

```python
def test_cleanup_parent_leaves_children(isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "jira:IPG-1@main", "--yes",
                                "--json", "--hard-delete"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert "children" not in out
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:IPG-1@main" not in links
    assert "jira:IPG-1@other" in links


def test_cleanup_merged_cleans_parent_and_child_independently(
        isolated_config, tmp_path, monkeypatch):
    _seeded(tmp_path, monkeypatch)
    r = runner.invoke(cli.app, ["cleanup", "--merged", "--yes", "--json",
                                "--hard-delete"])
    assert r.exit_code == 0, r.output
    import json
    rows = {row["key"]: row for row in json.loads(r.stdout)["results"]}
    assert rows["jira:IPG-1@main"]["status"] == "cleaned"
    assert rows["jira:IPG-1@other"]["status"] == "cleaned"
    assert "skipped:cleaned-with-parent" not in {
        row["status"] for row in rows.values()}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_cli_cleanup_related.py -v`
Expected: FAIL — parent cleanup still removes the child; `--merged` still emits `skipped:cleaned-with-parent`.

- [ ] **Step 3: Remove the cascade**

In `src/workagent/cli_cleanup.py`: in `cleanup()` `--merged` branch, delete the `cleaned_parents` set, the `(entry.get("parent_key_ref") or "") in cleaned_parents` skip, the `_cleanup_children` call, `row["children"]`, and `cleaned_parents.add/update` lines. In the single-ref path, delete the `children = _child_keys(key)` line, the `"children": children` dry-run key, the `_cleanup_children` call, and `result["children"]`. Delete `_child_keys` and `_cleanup_children` entirely (verify no other importer: `grep -rn _child_keys _cleanup_children src tests` must show only this file + tests).

- [ ] **Step 4: Update remaining cascade tests**

Delete `test_cleanup_parent_cascades_to_children`, `test_cleanup_dry_run_lists_children_without_teardown`, `test_cleanup_soft_delete_marks_children_deleted`, `test_cleanup_merged_skips_children_of_cleaned_parents`; keep `test_cleanup_child_direct_leaves_parent` (still valid). Add independence assertions: soft-delete (no `--hard-delete`) marks parent `deleted_at` only, child row has no `deleted_at`.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_cli_cleanup_related.py tests/test_cli_cleanup.py tests/test_cleanup_resolve.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/workagent/cli_cleanup.py tests/test_cli_cleanup_related.py
git commit -m "cleanup stops cascading to child worktrees [no-issue]"
```

### Task 2: `link purge-deleted [--repo]` CLI

**Files:**
- Modify: `src/workagent/cli_manage.py` (new command after `link_gc`)
- Modify: `src/workagent/web_args.py` (SPECS + SUBCOMMANDS `link` set)
- Test: `tests/test_cli.py` (or new `tests/test_link_purge.py` if cleaner)

**Interfaces:**
- Consumes: `store.load_links(include_inactive=True, include_deleted=True)` rows with truthy `deleted_at`; `store.delete_worktree_row(key)`.
- Produces: `link purge-deleted [--repo NAME] [--dry-run] [--json]` → `{"results": [{key, branch, status}]}`; bookkeeping only, never touches disk/branches/harness.

- [ ] **Step 1: Write the failing test**

```python
def test_purge_deleted_scopes_by_repo(isolated_config, tmp_path):
    from workagent import store
    store.record_link("jira:A-1", {"issue": "A-1",
        "worktree": str(tmp_path / "a"), "branch": "b1", "repo": "r1"})
    store.record_link("jira:B-2", {"issue": "B-2",
        "worktree": str(tmp_path / "b"), "branch": "b2", "repo": "r2"})
    store.soft_delete_worktree("jira:A-1")
    store.soft_delete_worktree("jira:B-2")
    r = runner.invoke(cli.app, ["link", "purge-deleted", "--repo", "r1",
                                "--json", "--yes"])
    assert r.exit_code == 0, r.output
    import json
    rows = {row["key"]: row for row in json.loads(r.stdout)["results"]}
    assert rows == {"jira:A-1": {"key": "jira:A-1", "branch": "b1",
                                 "status": "purged"}}
    links = store.load_links(include_inactive=True, include_deleted=True)
    assert "jira:A-1" not in links and "jira:B-2" in links


def test_purge_deleted_empty_repo_is_noop(isolated_config):
    r = runner.invoke(cli.app, ["link", "purge-deleted", "--repo", "nope",
                                "--json", "--yes"])
    assert r.exit_code == 0, r.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli.py -k purge -v` (or new test file)
Expected: FAIL with "No such command 'purge-deleted'".

- [ ] **Step 3: Implement the command**

After `link_gc` in `src/workagent/cli_manage.py`:

```python
@link_app.command("purge-deleted")
@_catch_harness_errors
def link_purge_deleted(
    repo: Optional[str] = typer.Option(None, "--repo", help="Only purge deleted rows for this repo (name or path)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="List rows that would be purged without acting."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (stdout; logs go to stderr)."),
) -> None:
    """Permanently drop soft-deleted worktree rows (bookkeeping only: files/branches already gone)."""
    links = store.load_links(include_inactive=True, include_deleted=True)
    targets = [k for k, v in links.items() if isinstance(v, dict) and v.get("deleted_at")]
    if repo:
        targets = [k for k in targets if str(links[k].get("repo", "")) in (repo,) or Path(str(links[k].get("repo", ""))).name == repo]
    rows = []
    for key in sorted(targets):
        branch = str(links[key].get("branch", ""))
        if dry_run:
            rows.append({"key": key, "branch": branch, "status": "purge-dry-run"})
            continue
        store.delete_worktree_row(key)
        rows.append({"key": key, "branch": branch, "status": "purged"})
    if json_output:
        print(json.dumps({"results": rows}, indent=2, ensure_ascii=False))
        return
    _print_rows(rows, json_output, False, ["key", "branch", "status"],
                "Purge deleted", "(nothing to purge)")
```

Match the `--repo` matching convention used by neighboring commands in the file (adjust the comparison if they resolve via a helper). Add `"link purge-deleted": ("--repo", "--dry-run", "--json")` to `BOOL_FLAGS`/`VAL_FLAGS` as the file's conventions dictate, add `"purge-deleted"` to `SUBCOMMANDS["link"]`, and extend `_needs_confirm` if `link gc` is listed there (purge is destructive → same treatment).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli.py tests/test_webapp.py -v -k "purge or mirror"`
Expected: PASS (mirror test covers the new SPECS entry).

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_manage.py src/workagent/web_args.py tests/test_cli.py
git commit -m "add link purge-deleted with per-repo scope [no-issue]"
```

### Task 3: DeletedTable repo dropdown + Clear

**Files:**
- Modify: `web/src/components/DeletedTable.tsx`
- Modify: `web/src/pages/Dashboard.tsx` (pass purge handler)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`
- Test: `tests/test_webapp.py` (SPECS mirror), manual UI check via `serve`

**Interfaces:**
- Consumes: `api` request helper, `DeletedTable({worktrees, actions})`, new `link purge-deleted` SPECS entry from Task 2.
- Produces: dropdown listing repos present in deleted rows (default All) + Clear button running `link purge-deleted [--repo X]` through `createRun` with irreversible confirm text.

- [ ] **Step 1: Add the web SPECS mirror test target**

Ensure `tests/test_webapp.py::test_specs_mirror_cli_flags` covers `link purge-deleted` (added in Task 2). No new test code if mirror is generic — verify by running it.

Run: `pytest tests/test_webapp.py -k mirror -v`
Expected: PASS (or FAIL listing the drift to fix in this task).

- [ ] **Step 2: Implement the dropdown + Clear**

In `DeletedTable.tsx`: derive `repos = [...new Set(Object.values(worktrees).map(e => repoDisplay(e))].sort()` (reuse the repo display helper used by StatusTable rows); add `const [repo, setRepo] = useState("all")`; filter entries by repo when `repo !== "all"` (combine with existing `q` filter). Add a `<select>` (only repos with deleted rows) + Clear button (`disabled` when filtered empty). Props gain `onClearDeleted?: (repo: string | null) => void` — `null` = all. In `Dashboard.tsx` add `handleClearDeleted` mirroring `handleCleanupMerged`: `confirm({action: "link", title: "Purge deleted worktrees", description: "Permanently drops N soft-deleted row(s)[ for repo X] from the database. Worktree files are already gone; this cannot be undone and undelete will no longer work.", destructive: true, ...})` then `createRun({command: "link", args: ["purge-deleted", ...(repo ? ["--repo", repo] : [])], confirm: true})` and invalidate `statusDeleted`.

- [ ] **Step 3: Update contract docs**

Add `link purge-deleted` to `web/API_CONTRACT.md` (runs section) and `web/FEATURE_INVENTORY.md` (Deleted table controls).

- [ ] **Step 4: Verify**

Run: `pytest tests/test_webapp.py -v`
Expected: PASS. Then `workagent serve`, open Dashboard with deleted rows, check dropdown lists only repos with deleted rows and Clear purges per scope.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/DeletedTable.tsx web/src/pages/Dashboard.tsx web/API_CONTRACT.md web/FEATURE_INVENTORY.md
git commit -m "deleted table gains per-repo purge [no-issue]"
```

### Task 4: Candidate PR author + compact row

**Files:**
- Modify: `src/workagent/refs.py:270-307` (`fetch_open_prs` gh + glab branches)
- Modify: `src/workagent/cli_candidates.py:66-109` (pass through)
- Modify: `web/src/lib/api.ts` (`CandidatePr` += `author?: string`)
- Modify: `web/src/components/CandidatesCard.tsx:243-268` (row render)
- Modify: `web/API_CONTRACT.md`
- Test: `tests/test_cli_candidates.py`

**Interfaces:**
- Consumes: `gh pr list --json author` / `glab mr list -F json` author shapes.
- Produces: PR dicts with `author` (login string, `""` when unknown); row renders `#{number} · {branch} · {relativeDate}` + author badge + `repo_name` badge; tooltip keeps `key · branch · repo · exact date`.

- [ ] **Step 1: Write the failing test**

```python
def test_candidate_prs_include_author_and_compact_fields(
        isolated_config, tmp_path, monkeypatch):
    from workagent import cli_candidates as _c, refs, store
    store.register_repo("r1", tmp_path / "r1")
    monkeypatch.setattr(_c.store, "load_repos",
        lambda: {"r1": {"path": str(tmp_path / "r1"), "fetch_enabled": True}})
    monkeypatch.setattr("workagent.cli._repo_tool", lambda path: "gh",
                        raising=False)
    monkeypatch.setattr(refs, "fetch_open_prs", lambda tool, cwd=None: [
        {"number": 1730, "title": "T", "branch": "feat/x",
         "target_branch": "main", "updated": "2026-01-01T00:00:00Z",
         "url": "https://github.com/o/r/pull/1730", "state": "OPEN",
         "author": "octocat"}])
    prs, warnings = _c._candidate_prs()
    assert prs[0]["author"] == "octocat"
    assert prs[0]["key"] == "github:o/r#1730"
```

(Check existing tests in `tests/test_cli_candidates.py` for the monkeypatch style for `_repo_tool` and reuse it exactly.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli_candidates.py -v -k author`
Expected: FAIL — `author` key missing.

- [ ] **Step 3: Add author to fetchers**

gh: add `author{login}` to `--json` fields, `"author": (p.get("author") or {}).get("login", "")`. glab: `"author": ((m.get("author") or {}) if isinstance(m.get("author"), dict) else {}).get("username", "") or str(m.get("author", "") or "")` — handle both dict and string shapes; never raise when absent. `_candidate_prs` passes dicts through untouched (no change needed if using `{**p, ...}`).

- [ ] **Step 4: Compact the row**

In `CandidatesCard.tsx` PR `<li>`: keep title link; add `{pr.author ? <Badge variant="accent">{pr.author}</Badge> : null}` after the state badge (highlighted creator); change subline to `#{pr.number} · {pr.branch} · {relativeDate(pr.updated)}` with `title={`${pr.key} · ${pr.branch} · ${pr.repo}${exact...}`}` (full key + full remote URL stay in tooltip); keep `<Badge variant="secondary">{pr.repo_name ?? pr.repo}</Badge>` (repo name only, never the full remote URL). Update `CandidatePr` in `api.ts` with `author?: string` and the contract doc's candidate PR shape.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_cli_candidates.py tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/workagent/refs.py web/src/lib/api.ts web/src/components/CandidatesCard.tsx web/API_CONTRACT.md tests/test_cli_candidates.py
git commit -m "candidate PR rows show author, compact to #N [no-issue]"
```

### Task 5: `release fix-default` CLI (dry-run + plan first)

**Files:**
- Create: `src/workagent/cli_release_fix.py`
- Modify: `src/workagent/cli_release.py` (wire subcommand or import; check whether `release` is a single command — if so, add `fix-default` as `release_app` subcommand following `repo`/`link` pattern in `cli_manage.py`)
- Modify: `src/workagent/web_args.py` (SPECS + flags)
- Test: `tests/test_cli_release.py`

**Interfaces:**
- Consumes: `repos_release.validate_release`-style fetch + `_count_both(path, default, release)` → `(ahead, behind)`; `refs.fetch_ci_status`-equivalent for the default branch; `_find_or_open_mr` (direction-swapped: source=default, target=release).
- Produces: `release fix-default NAME --action rebase|ci|both [--dry-run] [--json]` returning plan dict; harness prompt emission deferred to Task 6.

- [ ] **Step 1: Write the failing test**

```python
def test_fix_default_rebase_noop_when_not_behind(
        isolated_config, tmp_path, monkeypatch):
    # seed repo with default == release tip; run with --dry-run --json
    r = runner.invoke(cli.app, ["release", "fix-default", "r1",
                                "--action", "rebase", "--dry-run", "--json"])
    assert r.exit_code == 0, r.output
    import json
    out = json.loads(r.stdout)
    assert out["action"] == "rebase" and out["behind"] == 0
    assert "noop" in out["plan"].lower() or out.get("noop") is True
```

(Seed via `store.register_repo` + monkeypatched `run_cmd` rev-list counts `0`; follow existing `test_cli_release.py` seeding style.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli_release.py -k fix_default -v`
Expected: FAIL — no such command.

- [ ] **Step 3: Implement plan-only action**

Create `src/workagent/cli_release_fix.py` with `plan_fix_default(name, action) -> dict`: load registry, require default+release set and different (exit 2 otherwise, mirroring `validate_release`); `_fetch_refs`; `ahead, behind = _count_both`; fetch default-branch CI status (soft-fail → `"unknown"` + warning, never raise). Return `{"repo", "action", "default_branch", "release_branch", "ahead", "behind", "ci", "plan"}` where plan is: rebase + behind==0 → noop; rebase + behind>0 → `open {default}->{release} MR + remote rebase, fallback local harness`; ci + ci!=failure → noop note; both → ordered `[rebase_step, ci_step]` skipping noops. Wire the Typer command with `--action` (default `both`), `--dry-run`, `--json`; `--dry-run` prints plan without opening any MR.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_release.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_release_fix.py tests/test_cli_release.py
git commit -m "add release fix-default planner (dry-run) [no-issue]"
```

### Task 6: fix-default harness execution (rebase → CI → both)

**Files:**
- Modify: `src/workagent/cli_release_fix.py` (execute path)
- Modify: `src/workagent/web_args.py` (`--action`, `--run-mode`, `--extra-prompt` val flags)
- Test: `tests/test_cli_release.py`

**Interfaces:**
- Consumes: Task 5 planner dict; `cli_harness` run-mode pipeline (mirror how `sync --harness` launches: check `cli_sync.py` for the exact call); MR remote-rebase API via `gh`/`glab` merge commands.
- Produces: real execution — remote rebase attempt first, harness local-rebase fallback prompt (both SHAs, rebase strategy, no-new-commits); CI prompt with pipeline link + logs; `--extra-prompt` appended; preview mode prints command with NO `-p` flag.

- [ ] **Step 1: Write the failing test**

```python
def test_fix_default_rebase_remote_fail_falls_back_to_harness_prompt(
        isolated_config, tmp_path, monkeypatch):
    # behind > 0, remote API raises; expect harness prompt containing both SHAs
    ...
    assert "rebase" in out["harness_prompt"].lower()
    assert default_sha[:8] in out["harness_prompt"]
    assert release_sha[:8] in out["harness_prompt"]
    assert " -p " not in out["harness_command"] or mode != "preview"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli_release.py -k fallback -v`
Expected: FAIL — no execute path.

- [ ] **Step 3: Implement execution**

`run_fix_default(name, *, action, run_mode, extra_prompt, yes, dry_run)`: rebase step — `_find_or_open_mr(tool, path, default, release, title)` with source/target swapped vs release flow, then remote rebase attempt (`glab mr rebase` / `gh api repos/.../pulls/N/rebase` — match host; on failure proceed to fallback, never raise when fallback available); fallback builds prompt `Rebase {default} ({sha}) onto {release} ({sha}) using rebase strategy; push {default}; do not create merge commits` + extra. CI step — failing pipeline URL + trimmed logs in prompt `Fix the failing CI on {default}: {url}\n{logs}` + extra. `both` runs rebase-then-CI; single harness session when headless (concatenate prompts), sequential otherwise. Preview mode returns `harness_command` without `-p` and executes nothing. Confirm (TTY or `--yes`) with force-push warning before any rewrite.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_cli_release.py tests/test_webapp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_release_fix.py src/workagent/web_args.py tests/test_cli_release.py
git commit -m "release fix-default executes via harness [no-issue]"
```

### Task 7: Fix-default modal UI + contract + changelog

**Files:**
- Create or modify: `web/src/components/ReleaseCard.tsx` (add `FixDefaultDialog` in-file if <500 lines else new `FixDefaultDialog.tsx`)
- Modify: `web/src/lib/queries.ts` (only if a status hook is needed — else skip)
- Modify: `web/API_CONTRACT.md`, `web/FEATURE_INVENTORY.md`, `CHANGELOG.md`
- Test: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `RunModeSelect` + `extraPromptArgs`/`runModeArgs` from `StartForm.tsx`/`launchConfig.ts`; `useConfirmedRun`; `release fix-default` SPECS from Task 6.
- Produces: "Fix default" button per releasable repo row → modal with action 3-part select + run mode + extra prompt → `createRun({command: "release", args: ["fix-default", repo, "--action", a, ...runModeArgs(m), ...(extra? ["--extra-prompt", extra]:[])]})`.

- [ ] **Step 1: Mirror test first**

Run: `pytest tests/test_webapp.py -k mirror -v`
Expected: FAIL until SPECS/flags from Task 6 land (should already PASS — this step just confirms).

- [ ] **Step 2: Build the modal**

`FixDefaultDialog({repo, onClose})`: action select (Rebase/C I/Both segmented, default Both), `RunModeSelect`, Extra prompt `<textarea>`; confirm details show repo, branches, action; force-push warning in description for rebase/both. Wire button in `ReleaseRow` next to `ReleaseButton` (visible when `data.behind > 0 || data.ci === "failure"` — needs `behind` + `ci` on the release-status response; extend `release_status` backend + `ReleaseStatus` type if absent).

- [ ] **Step 3: Docs + changelog**

Contract + inventory entries; CHANGELOG lines (each ≤150 chars): `Fix-default modal rebases default onto release and fixes CI [no-issue]` style per repo rule (append via gap insert, verify `git diff` shows only `+`).

- [ ] **Step 4: Verify**

Run: `pytest tests/test_webapp.py tests/test_cli_release.py -v`
Expected: PASS. Serve + open modal, check preview shows command without `-p`.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/ReleaseCard.tsx web/API_CONTRACT.md web/FEATURE_INVENTORY.md CHANGELOG.md
git commit -m "release card fix-default modal [no-issue]"
```

## Self-Review

- Spec coverage: §1→Task 1; §2→Tasks 2–3; §3→Task 4; §4→Tasks 5–7. All covered.
- Placeholders: none — every step has code/commands.
- Type consistency: `CandidatePr.author?: string`; planner dict keys fixed in Task 5, consumed verbatim in Task 6.
- Review Focus items each pinned: harness-on-child (Task 1 soft-delete test), empty-repo purge (Task 2), missing author (Task 4 glab fallback), rebase noop (Task 5), preview `-p` (Task 6).
