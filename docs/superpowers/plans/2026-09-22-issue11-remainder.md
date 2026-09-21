# Issue #11 Remainder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the four approved remainder items: 1h Launch issue cache, mandatory repo tracker, tracker dropdown + auto-ref, candidate one-click actions.

**Architecture:** SQLite-backed cache table with per-source TTL rows; CLI-first changes (`candidates --reset-cache`, `repo add --tracker` required, `repo list` tracker column) with thin web layers on top (query params, dropdown, action buttons through the existing run pipeline).

**Tech Stack:** Python (Typer CLI, stdlib sqlite3), FastAPI webapp, React + TanStack Query + SearchableSelect.

**Spec:** `docs/superpowers/specs/2026-09-22-issue11-remainder-design.md`

## Global Constraints

- Exit codes: 0 success, 1 general error, 2 usage/needs-human-input.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Human-first: Rich tables by default; `--csv`/`--json` opt in for scripting/agents.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- `list_my_issues()` never raises: per-source failure contributes `[]` plus a warning.
- User content in Rich output is `rich.markup.escape`d.
- No new top-level commands, no new pages, no new API endpoints (query params only).
- Do NOT touch CHANGELOG.md / AGENTS.md / README.md until the docs task owns them.

## Review Focus

- Stale cache served past 1h TTL returns issues the user already closed — check `fetched_at` freshness per source, not per table.
- `repo add` without `--tracker` failing closed breaks existing scripts — expect a clear exit-2 message naming the flag.
- Tracker dropdown free-text entry bypassing normalization stores a non-canonical id — expect `normalize_id` on submit.
- Candidate Start button firing `start` without `--yes` on non-TTY hangs on a prompt — expect the confirm-modal/`--yes` pipeline, never a bare launch.
- Register action keyed `config` collides with other config runs in the run registry — expect a unique `register:<path>` key.

---

## File Structure

- `src/harness/store_sqlite.py` — add `issue_cache` table + `get_issue_cache`/`set_issue_cache`/`clear_issue_cache` (Task 1).
- `src/harness/trackers.py` — wrap `_jira_my_issues`/`_gh_my_issues` results with cache read/write, `force` param (Task 1).
- `src/harness/cli.py` — `candidates --reset-cache` flag; `repo add` requires `--tracker`; `repo list` tracker column (Tasks 1–2).
- `src/harness/webapp.py` — `?force=true` on `/api/issues` + `/api/candidates`; `tracker` per item on `/api/repos`; fix `register` SPECS key to `register:<path>` (Tasks 1, 2, 4).
- `web/src/lib/queries.ts` — `staleTime` 1h on `useCandidates`/`useIssues`; force refetch seam (Task 1).
- `web/src/pages/Repos.tsx` — required tracker input + tracker column (Task 2).
- `web/src/pages/Links.tsx` — LinkSet tracker dropdown + URL auto-ref (Task 3).
- `web/src/components/CandidatesCard.tsx` — Start/Review/Register buttons via `useCreateRun` + confirm (Task 4).
- `tests/test_store_sqlite.py`, `tests/test_trackers.py`, `tests/test_cli.py`, `tests/test_webapp.py` — per-task tests.
- Docs task: CHANGELOG.md Unreleased entry + `AGENTS.md` GitLab scoping paragraph.

## Task 1: Launch issue cache (1h + --reset-cache)

**Files:**
- Modify: `src/harness/store_sqlite.py` (add table + 3 functions)
- Modify: `src/harness/trackers.py` (`list_my_issues` gains `force: bool = False`)
- Modify: `src/harness/cli.py` (`candidates --reset-cache`)
- Modify: `src/harness/webapp.py` (`?force=true` on `/api/issues`, `/api/candidates`)
- Modify: `web/src/lib/queries.ts` (staleTime 1h)
- Test: `tests/test_store_sqlite.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `connect(path)` from `store_sqlite.py`; `_jira_my_issues(warn)`, `_gh_my_issues(warn)` from `trackers.py`.
- Produces: `get_issue_cache(path, source) -> tuple[list[dict], str | None]` (rows, fetched_at ISO or None); `set_issue_cache(path, source, rows) -> None`; `clear_issue_cache(path) -> None`. `list_my_issues(warnings=None, force=False)`.

- [ ] **Step 1: Write the failing test for cache roundtrip**

```python
def test_issue_cache_roundtrip(tmp_path):
    from harness import store_sqlite as sq
    db = tmp_path / "state.db"
    sq.init_db(db)
    assert sq.get_issue_cache(db, "jira") == ([], None)
    rows = [{"key": "IPG-1", "title": "t", "url": "u", "status": "To Do", "created": "2026-09-01"}]
    sq.set_issue_cache(db, "jira", rows)
    got, fetched_at = sq.get_issue_cache(db, "jira")
    assert got == rows
    assert fetched_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_sqlite.py::test_issue_cache_roundtrip -v`
Expected: FAIL with "has no attribute 'get_issue_cache'"

- [ ] **Step 3: Write minimal implementation**

```python
def get_issue_cache(path: Path, source: str) -> tuple[list, str | None]:
    """Cached issue rows for *source* + fetched_at ISO, or ([], None)."""
    import json
    init_db(path)
    with connect(path) as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM issue_cache WHERE source = ?",
            (source,)).fetchone()
    if row is None:
        return [], None
    return json.loads(row[0]), row[1]


def set_issue_cache(path: Path, source: str, rows: list[dict]) -> None:
    import json
    from datetime import datetime, timezone
    init_db(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO issue_cache (source, payload, fetched_at)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET payload=excluded.payload,"
            " fetched_at=excluded.fetched_at",
            (source, json.dumps(rows),
             datetime.now(timezone.utc).isoformat()))


def clear_issue_cache(path: Path) -> None:
    init_db(path)
    with connect(path) as conn:
        conn.execute("DELETE FROM issue_cache")
```

Add to SCHEMA (in `store_sqlite.py`, after the `runs` table):

```sql
CREATE TABLE IF NOT EXISTS issue_cache (
  source TEXT PRIMARY KEY, payload TEXT NOT NULL DEFAULT '[]',
  fetched_at TEXT NOT NULL DEFAULT '');
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_sqlite.py::test_issue_cache_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for TTL + force**

```python
def test_my_issues_uses_cache_within_ttl(monkeypatch, tmp_path):
    from harness import trackers
    from harness import store_sqlite as sq
    import harness.store as store
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    rows = [{"key": "IPG-1", "title": "t", "url": "u", "status": "To Do", "created": "2026-09-01"}]
    sq.set_issue_cache(sq.db_path(), "jira", rows)
    monkeypatch.setattr(trackers, "_jira_my_issues",
                        lambda warn: (_ for _ in ()).throw(AssertionError("must use cache")))
    monkeypatch.setattr(trackers, "_gh_my_issues", lambda warn: [])
    got = trackers.list_my_issues([])
    assert [r["key"] for r in got] == ["IPG-1"]
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_trackers.py::test_my_issues_uses_cache_within_ttl -v`
Expected: FAIL (calls the stub and raises AssertionError)

- [ ] **Step 7: Implement cache read/write in trackers.py**

```python
_CACHE_TTL_SECONDS = 3600

def _cached_source(source: str, fetch, warn: list, force: bool) -> list[dict]:
    from datetime import datetime, timezone
    from . import store_sqlite as sq
    db = sq.db_path()
    if not force and db.exists():
        try:
            rows, fetched_at = sq.get_issue_cache(db, source)
            if rows and fetched_at:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(fetched_at)).total_seconds()
                if age < _CACHE_TTL_SECONDS:
                    return rows
        except Exception:
            pass
    rows = fetch(warn)
    try:
        if db.exists():
            sq.set_issue_cache(db, source, rows)
    except Exception:
        pass
    return rows
```

Change `list_my_issues` signature to `list_my_issues(warnings=None, force=False)` and replace `rows = _jira_my_issues(warn) + _gh_my_issues(warn)` with per-source `_cached_source("jira", _jira_my_issues, warn, force)` + `_cached_source("github", _gh_my_issues, warn, force)`. Pre-cutover (no state.db): skip cache silently, fetch live. Cache read/write failures never raise — fall through to live fetch.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_trackers.py tests/test_store_sqlite.py -q`
Expected: PASS

- [ ] **Step 9: Write the failing test for --reset-cache**

```python
def test_candidates_reset_cache_clears(isolated_config, monkeypatch):
    from harness import store_sqlite as sq
    import harness.store as store
    db = sq.db_path()
    sq.init_db(db)
    sq.set_issue_cache(db, "jira", [{"key": "IPG-1"}])
    monkeypatch.setattr(cli.trackers, "list_my_issues", lambda *a, **k: [])
    monkeypatch.setattr(cli.refs, "fetch_open_prs", lambda *a, **k: [])
    r = _invoke("candidates", "--reset-cache", "--json")
    assert r.exit_code == 0, r.output
    assert sq.get_issue_cache(db, "jira") == ([], None)
```

- [ ] **Step 10: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_candidates_reset_cache_clears -v`
Expected: FAIL with "No such option: --reset-cache"

- [ ] **Step 11: Implement --reset-cache + web force params**

In `cli.py` `candidates_cmd`, add `reset_cache: bool = typer.Option(False, "--reset-cache", help="Clear the cached issue rows and re-fetch live.")`. At the top of the command body: `if reset_cache: sq.clear_issue_cache(sq.db_path())` then call `_candidates(force=reset_cache)`; `_candidates()` gains `force: bool = False` and passes it to `trackers.list_my_issues(warnings_list, force=force)`. In `webapp.py`, `my_issues` and `candidates` gain `force: bool = False` query param, forwarded to `list_my_issues` / `_candidates`. In `web/src/lib/queries.ts`, set `staleTime: 3600_000` on `useCandidates`/`useIssues`; Refresh buttons call `refetch()` (React Query cache) — document that force-fetch needs the `?force=true` query arg on `api.candidates`/`api.issues`.

- [ ] **Step 12: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q -k "candidates or reset_cache"`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add src/harness/store_sqlite.py src/harness/trackers.py src/harness/cli.py src/harness/webapp.py web/src/lib/queries.ts tests/test_store_sqlite.py tests/test_trackers.py tests/test_cli.py
git commit -m "feat: 1h issue cache with candidates --reset-cache"
```

## Task 2: Repos tracker mandatory + list column

**Files:**
- Modify: `src/harness/cli.py` (`repo_add`, `repo_list`)
- Modify: `src/harness/webapp.py` (`/api/repos` tracker per item)
- Modify: `web/src/pages/Repos.tsx` (required input + column)
- Test: `tests/test_cli.py`, `tests/test_webapp.py`

**Interfaces:**
- Consumes: `trackers.normalize_id(raw) -> str`; `store.load_config()`.
- Produces: `repo list --json` items gain `tracker: str` (first matching tid or `""`); `/api/repos` items gain the same.

- [ ] **Step 1: Write the failing test for required tracker**

```python
def test_repo_add_requires_tracker(isolated_config, tmp_path):
    r = _invoke("repo", "add", "--name", "p", "--path", str(tmp_path))
    assert r.exit_code == 2
    assert "--tracker" in r.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_repo_add_requires_tracker -v`
Expected: FAIL (exit 0 today)

- [ ] **Step 3: Implement required tracker + list column**

In `repo_add`: `if not tracker: _fail("tracker is required: pass --tracker IPG|github:O/R|GitLab URL", EXIT_USAGE)` before `register_repo`. In `repo_list`: build a reverse map `{norm_repo_path: tid}` from `cfg.get("trackers", {})` and add `"tracker"` per item (first match, `""` when none); add `"tracker"` to the Rich columns list. In `webapp.py repos_list`: same reverse-lookup, include `tracker` per item.

- [ ] **Step 4: Write the failing test for list column**

```python
def test_repo_list_shows_tracker(isolated_config, tmp_path):
    _invoke("repo", "add", "--name", "p", "--path", str(tmp_path), "--tracker", "IPG")
    r = _invoke("repo", "list", "--json")
    assert r.exit_code == 0, r.output
    import json
    items = json.loads(r.output)
    assert items[0]["tracker"] == "jira:IPG"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q -k "repo_add or repo_list"`
Expected: PASS

- [ ] **Step 6: Web Repos page required input + column**

In `web/src/pages/Repos.tsx`: mark the tracker `<Input>` required (blocking submit with empty tracker + inline error text), add a Tracker `<TableHead>`/cell reusing `repo.tracker`. Verify `GET /api/repos` type in `web/src/lib/api.ts` includes `tracker: string`.

- [ ] **Step 7: Run web gates**

Run: `cd web && npx tsc --noEmit && npm run build 2>&1 | tail -1 && npx eslint . 2>&1 | tail -1`
Expected: no errors, `✓ built`, no issues

- [ ] **Step 8: Commit**

```bash
git add src/harness/cli.py src/harness/webapp.py web/src/pages/Repos.tsx web/src/lib/api.ts tests/test_cli.py tests/test_webapp.py
git commit -m "feat: repo add requires --tracker, list shows tracker column"
```

## Task 3: Tracker dropdown + auto-ref (minimal)

**Files:**
- Modify: `web/src/pages/Links.tsx` (LinkSet dialog)
- Test: manual component check via `tsc` + build (no test runner in web/)
- Docs: `AGENTS.md` GitLab scoping paragraph (fold into docs task)

**Interfaces:**
- Consumes: `useLinks()` data `links.trackers` (Record<string, {repos}>); existing `SearchableSelect` with `allowCustom`.
- Produces: LinkSet dialog with tracker dropdown + URL auto-fill.

- [ ] **Step 1: Replace Tracker Input with SearchableSelect**

In `LinkSetDialog` (`web/src/pages/Links.tsx` around line 263): replace the Tracker `<Input>` with `<SearchableSelect value={tracker} options={Object.keys(trackers)} onChange={setTracker} allowCustom placeholder="jira:IPG or github:OWNER/REPO" />` where `trackers = links?.trackers ?? {}` from `useLinks()`. On submit, pass the value through the backend `link set` run (backend already calls `normalize_id`; no CLI change needed).

- [ ] **Step 2: Auto-derive tracker from pasted URLs**

Add a `useEffect` on `tracker` input value: when it looks like a URL (starts with `http`), derive the canonical id client-side with the same rules as `trackers.tracker_id` (jira `PREFIX-123` → `jira:PREFIX`; github.com URL path `o/r` → `github:o/r`; else host-scoped `gitlab:<host>/<path>`). If derivation succeeds, set the dropdown value to it. Unparseable → keep free text. Keep the derivation function local to Links.tsx (no shared util — single call site).

- [ ] **Step 3: Run web gates**

Run: `cd web && npx tsc --noEmit && npm run build 2>&1 | tail -1 && npx eslint . 2>&1 | tail -1`
Expected: no errors, `✓ built`, no issues

- [ ] **Step 4: Commit**

```bash
git add web/src/pages/Links.tsx
git commit -m "feat: tracker dropdown with URL auto-ref in LinkSet dialog"
```

## Task 4: Candidate one-click actions + register key fix

**Files:**
- Modify: `src/harness/webapp.py` (SPECS `register` key)
- Modify: `web/src/components/CandidatesCard.tsx` (buttons)
- Test: `tests/test_webapp.py` (register key uniqueness)

**Interfaces:**
- Consumes: `useCreateRun()` + `useConfirm()` (same shape as Links `useLinkSubmit`); `POST /api/runs` with `command`/`args`/`confirm`.
- Produces: Start/Review/Register buttons per row; `register` runs keyed `register:<path>`.

- [ ] **Step 1: Write the failing test for register run key**

```python
def test_register_run_key_unique_per_path(client):
    r1 = client.post("/api/runs", json={"command": "register", "args": ["/tmp/a"], "confirm": False})
    r2 = client.post("/api/runs", json={"command": "register", "args": ["/tmp/b"], "confirm": False})
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["run_id"] != r2.json()["run_id"]
```

(Adjust to the existing `test_webapp.py` client fixture name; check how other `/api/runs` tests post.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_webapp.py::test_register_run_key_unique_per_path -v`
Expected: FAIL (both keyed `config`, second returns 409 or same id)

- [ ] **Step 3: Fix register SPECS key**

In `webapp.py` SPECS, change `"register": {"confirm": False, "force": True, "key": "config"}` to a per-path key: `"key": "register"` prefix combined with the target path at run-creation time (follow the existing `open:<ref>` pattern — check how `open` builds its key from args and mirror it for `register`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_webapp.py::test_register_run_key_unique_per_path -v`
Expected: PASS

- [ ] **Step 5: Add action buttons to CandidatesCard**

In `web/src/components/CandidatesCard.tsx`: per-row buttons — PR/MR rows: **Review** (`{command: "review", args: [pr.url]}`); issue rows: **Start** (`{command: "start", args: [issue.key]}`); worktree rows: **Register** (`{command: "register", args: [w.path]}`). Each goes through `useCreateRun().mutateAsync` + confirm modal (copy the `useLinkSubmit` pattern from Links.tsx: toast success with View-run action, invalidate `links` + `statusAll` queries). Keep the existing copy-ref button. Buttons are explicit clicks only — no auto-fire.

- [ ] **Step 6: Run web gates**

Run: `cd web && npx tsc --noEmit && npm run build 2>&1 | tail -1 && npx eslint . 2>&1 | tail -1`
Expected: no errors, `✓ built`, no issues

- [ ] **Step 7: Commit**

```bash
git add src/harness/webapp.py web/src/components/CandidatesCard.tsx tests/test_webapp.py
git commit -m "feat: candidate Start/Review/Register actions, unique register run keys"
```

## Task 5: Docs + final verification

**Files:**
- Modify: `CHANGELOG.md` (Unreleased entry)
- Modify: `AGENTS.md` (GitLab scoping paragraph, ~3 lines)
- Test: full suite + web build + eslint

- [ ] **Step 1: CHANGELOG entry**

Under Unreleased, add: issue-cache table + `candidates --reset-cache`; `repo add --tracker` required + tracker column; LinkSet tracker dropdown + URL auto-ref; candidate Start/Review/Register buttons; register run-key fix.

- [ ] **Step 2: AGENTS.md GitLab scoping paragraph**

In the upstream-tracker section, add: canonical ids are host-scoped (`gitlab:<host>/<group>/<repo>`); a bare `server/projectx` is not portable across hosts — always persist the full host-scoped id (this answers the `gitlab:git.jibit.cloud/server/projectx` scoping question).

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q`
Expected: all pass. Run: `cd web && npx tsc --noEmit && npm run build 2>&1 | tail -1 && npx eslint .`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md AGENTS.md
git commit -m "docs: issue 11 changelog and GitLab tracker scoping"
```
