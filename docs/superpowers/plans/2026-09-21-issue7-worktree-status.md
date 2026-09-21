# Issue #7 — Worktree Status, Bugs & Web UX Wave — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the picker/PR-column bugs, add cached CI pipeline status to CLI+Web, and ship the web UX wave (open-folder, issue dropdown, renames, search+Added Date, bulk buttons, invalid-worktree highlight, candidates).

**Architecture:** All state flows through the existing seams: `pr_cache.json` (branch-keyed) gains additive CI fields with independent invalidation; `_status_cells` remains the single PR/CI source of truth feeding CLI rows and `_enrich_entry`→webapp; tracker listing is a new `trackers.list_my_issues` (jira-cli `request` raw API + `gh search issues`) consumed by `/api/issues`, `/api/candidates`, and the `harness candidates` CLI; frontend changes are additive fields + label renames + one breaking JSON key (`/api/links` `sessions`→`worktrees`).

**Tech Stack:** Python/Typer/Rich (CLI), FastAPI (webapp), React+TS+TanStack Query (web), gh/glab/jira-cli via `errors.run_cmd`.

**Spec:** `docs/superpowers/specs/2026-09-21-issue7-worktree-status-design.md` (committed `e721a78`). Read it first; this plan argues from it.

## Global Constraints

- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- stdout data-only; every log/progress line → stderr (`eprint`).
- Confirmations only on TTY or explicit `--yes`.
- `uv run pytest -q` green after every task; `cd web && npm run build` + `npx eslint .` clean after every frontend-touching task.
- Never push; never commit on a detached head; never create nested git repos.
- User content rendered in Rich output must be `rich.markup.escape`d.
- Shell completion stays offline (completions never touch the network).
- Work in an isolated worktree (`.worktrees/<branch>` branched from main) — create at execution start via using-git-worktrees; main checkout must stay clean.
- Tests: adapt sketches to existing fixtures in `tests/` (`HARNESS_CONFIG_DIR` via monkeypatch/setenv, monkeypatched `run_cmd`). New tests must fail before the fix and pass after.

## Review Focus

1. **Empty/one-option picker boundary** — redraw with 0/1 options must not corrupt the screen; expected: clean render, enter returns 0/None. Pinned by Task 1 (`test_picker_zero_and_single_options`).
2. **Cache entries with missing fields** — a pr_cache/links entry lacking `branch`/`repo`/`worktree` must never crash status; expected: `-` cells. Pinned by Task 2 (`test_status_cells_partial_entry`).
3. **CI fetch failure** — host CLI timeout/garbage output must yield `ci: null` and a stderr warning, never an exception out of `_status_cells`. Pinned by Task 3 (`test_ci_fetch_failure_is_soft`).
4. **jira-cli absent for /api/issues** — missing binary → HTTP 200 `{"issues": [], "warning": ...}`, dropdown falls back to free text. Pinned by Task 5 (`test_api_issues_missing_cli`).
5. **Legacy links without `added_at`** — sort must place them last without KeyError; `record_link` merge must never bump an existing `added_at`. Pinned by Task 5 (`test_record_link_added_at_setdefault`, `test_sort_missing_added_at_last` in Task 7).
6. **Rename completeness** — no user-visible "Session(s)" label remains in `web/src`; pinned by Task 6 grep step (`grep -rn "session" web/src --include=*.tsx --include=*.ts` reviewed for leftovers; identifiers `Session*` renamed).

---

### Task 1: Picker raw-mode line terminators + j/k

**Files:**
- Modify: `src/harness/pick.py` (`_option_line` :57-62, `_redraw` :78-84, `_erase` :70-75, `_read_key` :36-55, `pick_index` :104-149, label draw inside `pick_index` ~:126)
- Test: `tests/test_pick.py`

**Interfaces:**
- Consumes: existing `pick`/`pick_index(label, options, *, read=None, stream=None)` — unchanged signature.
- Produces: same public API; every stream line now ends `\r\n`; `j`/`k` navigate. Callers (worktrees.pick_worktree, trackers, cli) unchanged.

- [ ] **Step 1: Write failing tests**

```python
def _keys(*ks: str) -> Callable[[int], str]:
    it = iter(ks)
    return lambda n: next(it)

def test_option_lines_end_crlf():
    s = io.StringIO()
    pick_index("pick:", ["a", "b"], read=_keys("\r"), stream=s)
    out = s.getvalue()
    for line in out.split("\n"):
        if line and "\x1b[2K" in line:  # every option/erase line
            assert line.endswith("\r"), f"bare LF line: {line!r}"

def test_jk_navigation():
    assert pick_index("pick:", ["a", "b", "c"], read=_keys("j", "j", "\r"), stream=io.StringIO()) == 2
    assert pick_index("pick:", ["a", "b", "c"], read=_keys("j", "k", "\r"), stream=io.StringIO()) == 0

def test_picker_zero_and_single_options():
    assert pick_index("pick:", ["only"], read=_keys("\r"), stream=io.StringIO()) == 0
    assert pick_index("pick:", [], read=_keys("\r"), stream=io.StringIO()) is None
```

- [ ] **Step 2: Run — expect FAIL** (`uv run pytest tests/test_pick.py -q` → terminator and j/k assertions fail).

- [ ] **Step 3: Implement**

- `_option_line`: return `f"{_ERASE_LINE}{marker} {name}{desc}\r\n"`.
- `_redraw`: erase pass writes `_ERASE_LINE + "\r\n"`; also add one final `_CURSOR_UP`-safe `\r` — exact body:
  ```python
  def _redraw(stream, options, idx):
      stream.write(_CURSOR_UP * len(options))
      for _ in options:
          stream.write(_ERASE_LINE + "\r\n")
      stream.write(_CURSOR_UP * len(options))
      _draw(stream, options, idx)
  ```
- `_erase`: after the existing UP×N + (EL + cursor-down)×N block, append `stream.write("\r")` so the post-picker prompt starts at column 0.
- `pick_index` initial label write: `stream.write(label + "\r\n")` instead of `"\n"`.
- `_read_key`: map `"j"` → down, `"k"` → up (same return values as arrows).
- Guard: `pick_index` with empty `options` → return None before drawing.

- [ ] **Step 4: Run — expect PASS** (full `uv run pytest tests/test_pick.py -q`).

- [ ] **Step 5: Commit** `fix: picker raw-mode CR/LF terminators + j/k navigation (issue #7)`

### Task 2: Recorded-PR-wins + negative TTL + store cleanup

**Files:**
- Modify: `src/harness/cli.py` (`_cache_fresh` :947-955, `_status_cells` :979-1039, `_fmt_pr` :928-931)
- Modify: `src/harness/store.py` (delete duplicate `get_cached_pr_status` :150-151, delete unused `save_pr_cache` :116-117)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `store.load_links()` entries with optional `pr_url` (existing); `refs.parse_ref`.
- Produces: module const `_NEGATIVE_TTL_SECONDS = 30 * 60`; `_recorded_pr(url: str) -> dict | None` (parses PR/MR URL → `{"number": int, "state": "", "url": url}` via `refs._GITHUB_PR`/`_GITLAB_MR` regexes — or `refs.parse_ref` + `issue_key`; return None for unparseable); `_status_cells` unchanged signature; cells gain nothing new (pr cell filled from recorded URL when cache empty).

- [ ] **Step 1: Failing tests**

```python
def test_status_cells_recorded_pr_wins(monkeypatch, tmp_cfg):
    # entry has pr_url; branch query returns nothing (cached negative or failed lookup)
    monkeypatch.setattr(cli, "_query_pr", lambda repo, branch: (_ raises HarnessError))
    # simpler: cached negative entry
    store.cache_pr_status("feat/x", None, tool=None, base_branch="main",
                          branch_tip="t", base_tip="b", behind=0, ahead=0)
    entry = {"worktree": "/nonexistent", "branch": "feat/x", "repo": "/r",
             "pr_url": "https://git.jibit.cloud/g/p/-/merge_requests/1706"}
    cells = cli._status_cells(entry)
    assert cells["pr"] == "MR #1706" or "1706" in cells["pr"]
    assert cells["pr_data"]["url"].endswith("/merge_requests/1706")

def test_negative_cache_short_ttl(monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    cached = {"checked_at": old, "pr": None, "branch_tip": "t", "base_tip": "b"}
    assert cli._cache_fresh(cached) is False          # negative: 30min TTL
    cached_pos = {**cached, "pr": {"number": 1, "state": "OPEN"}}
    assert cli._cache_fresh(cached_pos) is True       # positive: 3h TTL

def test_status_cells_partial_entry():
    cells = cli._status_cells({"worktree": "", "branch": "", "repo": ""})
    assert cells["pr"] == "-" and cells["commits"] == "-"
```

(Adapt fixture names to existing conftest; `_git_tip`/`repos.ahead_behind` monkeypatched to avoid git.)

- [ ] **Step 2: Run — FAIL** (`AttributeError: _NEGATIVE_TTL_SECONDS`, recorded-pr not seeded).

- [ ] **Step 3: Implement**
  - `_cache_fresh(cached)`: `ttl = _STATUS_TTL_SECONDS if cached.get("pr") else _NEGATIVE_TTL_SECONDS`; compare age against that.
  - `_fmt_pr`: stateless variant — `f"PR #{p['number']} ({p['state']})"` when state else `f"PR #{p['number']}"`; for glab URLs prefix `MR` instead of `PR` (decide by `"merge_requests" in url` or `pr.get("tool")` — keep it simple: if `state == ""` and url contains `merge_requests` → `MR #n`).
  - `_status_cells`: at both return paths AND the final return, before returning: `if not cells["pr_data"] and entry.get("pr_url"): cells["pr_data"] = _recorded_pr(entry["pr_url"]); cells["pr"] = _fmt_pr(cells["pr_data"])`. Implement `_recorded_pr` with `refs._GITLAB_MR`/`refs._GITHUB_PR` (import inside function to avoid cycle — cli already imports refs).
  - store.py: delete the duplicate `get_cached_pr_status` (second def) and `save_pr_cache` (verify zero callers first: `grep -rn save_pr_cache src tests`).
- [ ] **Step 4: PASS** — full `uv run pytest tests/test_cli.py tests/test_store.py -q`.
- [ ] **Step 5: Commit** `fix: recorded PR URL wins in status + 30min negative PR cache TTL (issue #7)`

### Task 3: CI status — fetch, cache, CLI column, web field

**Files:**
- Modify: `src/harness/refs.py` (new `fetch_ci_status`)
- Modify: `src/harness/store.py` (`cache_pr_status` gains `ci`, `ci_checked_at`, `ci_sha` params — additive, default None)
- Modify: `src/harness/cli.py` (`_status_cells` CI block, `_CI_TTL_SECONDS = 600`, `_fmt_ci`, `_session_rows` +1 column, `_colorize_session`, `_enrich_entry` + `"ci"`, `_session_detail` include `ci`)
- Modify: `web/src/lib/api.ts` (`WorktreeEntry.ci?: string`), `web/src/components/StateBadge.tsx` (new `CiBadge`), `web/src/components/StatusTable.tsx` + `WorktreeDetail.tsx` (badge)
- Test: `tests/test_refs.py` or `tests/test_cli.py`, `tests/test_webapp.py`

**Interfaces:**
- Produces: `refs.fetch_ci_status(tool: str, pr_url: str, cwd: str) -> str | None` — returns `"success"|"failure"|"running"|"not_started"`, None on error; raises nothing (catches HarnessError internally, warns to stderr). store: `cache_pr_status(..., ci: str | None = None)` writes `ci`/`ci_checked_at`/`ci_sha` keys only when ci is not None (additive — existing callers unchanged). `_status_cells` cells gain `"ci": str | None` (None when no PR or fetch failed).

- [ ] **Step 1: Failing tests** (monkeypatch `refs.run_cmd`)

```python
def test_fetch_ci_gh_rollup(monkeypatch):
    calls = []
    def fake_run(cmd, *args, **kw):
        calls.append((cmd, args))
        return json.dumps({"statusCheckRollup": [
            {"conclusion": "SUCCESS"}, {"conclusion": "SUCCESS"}]})
    monkeypatch.setattr(refs, "run_cmd", fake_run)
    assert refs.fetch_ci_status("gh", "https://github.com/o/r/pull/9", "/repo") == "success"
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps({"statusCheckRollup": [{"conclusion": "FAILURE"}]}))
    assert refs.fetch_ci_status("gh", "https://github.com/o/r/pull/9", "/repo") == "failure"
    # empty rollup -> not_started; pending -> running
def test_fetch_ci_glab_pipeline(monkeypatch):
    monkeypatch.setattr(refs, "run_cmd", lambda *a, **k: json.dumps([{"id": 2, "status": "success"}]))
    assert refs.fetch_ci_status("glab", "https://git.jibit.cloud/g/p/-/merge_requests/7", "/repo") == "success"
def test_ci_fetch_failure_is_soft(monkeypatch):
    def boom(*a, **k): raise HarnessError("timeout")
    monkeypatch.setattr(refs, "run_cmd", boom)
    assert refs.fetch_ci_status("gh", "https://github.com/o/r/pull/9", "/repo") is None
def test_status_cells_ci_cache(monkeypatch, tmp_cfg):
    # branch_tip == ci_sha and ci_checked_at fresh -> no second fetch
    fetches = []
    monkeypatch.setattr(refs, "fetch_ci_status", lambda *a: fetches.append(a) or "success")
    ... assert len(fetches) == 1 across two _status_cells calls with same tips
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement**
  - `refs.fetch_ci_status`:
    - gh: `run_cmd("gh", "pr", "view", pr_url, "--json", "statusCheckRollup")` → rollup list; map conclusions: `FAILURE|ACTION_REQUIRED|TIMED_OUT|STARTUP_FAILURE|CANCELLED` → failure; `SUCCESS|NEUTRAL|SKIPPED` → success; `IN_PROGRESS|QUEUED|PENDING|STARTING` → running; any failure beats any running beats success; empty list → not_started.
    - glab: parse MR URL → host, path, iid (regex `refs._GITLAB_MR`); `run_cmd("glab", "api", f"projects/{quote(path, safe='')}/merge_requests/{iid}/pipelines", "--hostname", host)`; if that errors, retry without `--hostname`; JSON list → latest by `id` → `success|passed` → success, `failed` → failure, `running|pending|created|waiting_for_resource|preparing` → running, `canceled` → failure, `skipped|manual` → not_started; empty → not_started.
    - Wrap both in try/except HarnessError → `eprint(f"warning: ci lookup failed for {pr_url}: {e}")` → return None.
  - `cache_pr_status`: new optional params; entry keys written only when provided.
  - `_status_cells`: after `pr_data` resolves (any path), `ci` logic:
    ```python
    def _ci_cell(pr_data, branch_tip, refresh):
        if not pr_data or not pr_data.get("url"):
            return None
        # reuse from cache handled by caller (cached entry keys ci/ci_sha/ci_checked_at)
        ci = refs.fetch_ci_status(tool_used_or_detected, pr_data["url"], repo)
        return ci
    ```
    Concretely: keep the existing cache-reuse block; when reusing a fresh PR entry and `ci_sha == branch_tip` and `ci_checked_at` within `_CI_TTL_SECONDS` and `refresh` false → `cells["ci"] = cached.get("ci")`; otherwise fetch once, and persist via a new `store.cache_ci_status(branch, ci, sha)` (locked, reads-modifies the branch entry — reuse `_locked` pattern; separate small helper keeps `cache_pr_status` untouched). `_CI_TTL_SECONDS = 600` in cli.py.
  - `_session_rows`: `row["ci"] = cells["ci"] or ""`; columns gain `"ci"` after `"pr"`. `_colorize_session`: `_CI_SYMBOLS = {"success": "✓", "failure": "✗", "running": "●"}` → display symbol or `"-"`; color via `_CI_STYLES` (green/red/cyan) mirroring `_PR_STYLES`; escape not needed (values are ours).
  - `_enrich_entry`: `"ci": cells["ci"]`; `_session_detail`: include `ci` + keep `pr_detail`.
  - Frontend: `api.ts` add `ci?: string` to `SessionEntry` (Task 6 renames it — use the name that will exist post-rename to avoid churn: implement as `ci?: string` on the type; if Task 6 lands later, alias is fine); `CiBadge` in StateBadge.tsx mapping `success`→green ✓, `failure`→red ✗, `running`→blue ●, `not_started`/undefined→gray "–"; render in StatusTable new column (header "CI") + WorktreeDetail row. **Run `cd web && npm run build` + eslint.**
- [ ] **Step 4: PASS** (pytest + web build + eslint).
- [ ] **Step 5: Commit** `feat: cached CI pipeline status in status table + web (issue #7)`

### Task 4: `harness open`, `cleanup --merged`, webapp allowlists

**Files:**
- Modify: `src/harness/cli.py` (`cleanup` :598-666 refactored into `_cleanup_one(key, entry, force, yes, dry_run, json_output) -> dict`; new `open_cmd`; `--merged` flag)
- Modify: `src/harness/webapp.py` (SPECS/BOOL_FLAGS/target keys)
- Test: `tests/test_cli.py`, `tests/test_webapp.py`

**Interfaces:**
- Produces: `harness open <ref>` (no flags; opens linked worktree dir via OS opener — Linux `xdg-open`, macOS `open`, Windows `explorer`; refuses missing/invalid with exit 1; prints the path on stdout). `harness cleanup [--merged]` — ref omitted with `--merged`: loops merged/closed-PR links, skips entries with a live harness (`store.active_harness(key)`) or unparseable PR, prints summary rows `[{key, branch, status}]` (`cleaned`/`skipped:<reason>`); requires `--yes` on CLI when `--merged` (else exit 2); ref given with `--merged` → exit 2. cli exports `_cleanup_one` for reuse. webapp: `SPECS["open"]`, `SPECS["cleanup"]["key"]` → `"cleanup:all"` when `--merged` present; `BOOL_FLAGS["review"] += ("--all", "--sequential", "--fix")`, `BOOL_FLAGS["cleanup"] += ("--merged",)`, `BOOL_FLAGS["open"] = ()`; `SPECS["review"]["key"]` → lambda: `"review:all" if "--all" in args else "review"`.

- [ ] **Step 1: Failing tests**

```python
def test_open_resolves_and_opens(monkeypatch, tmp_cfg, fake_link):
    opened = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda argv, **kw: opened.append(argv) or DummyProc())
    rc = ... invoke open_cmd with ref → key of fake_link with worktree=<real tmp dir>
    assert opened[0][0] in ("xdg-open", "open", "explorer")

def test_open_refuses_invalid(tmp_cfg):
    → exit 1, message names the key

def test_cleanup_merged_loop(monkeypatch, tmp_cfg):
    two links: one PR state MERGED, one OPEN (monkeypatch cli._status_cells)
    monkeypatch cli.gitwt.cleanup_worktree → record calls
    result = invoke cleanup --merged --yes --json
    assert cleaned==[merged_key], open key untouched

def test_cleanup_merged_requires_yes_noninteractive(...)
def test_webapp_review_all_allowed():
    _validate_args("review", ["--all"])  # no raise
def test_webapp_open_allowed_and_target():
    assert _target_for("open", ["jira:X-1"]) == "open:jira:X-1"
def test_webapp_cleanup_merged_target():
    assert _target_for("cleanup", ["--merged"]) == "cleanup:all"
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement**
  - Extract `_cleanup_one` from the `cleanup` body (:613-666 minus arg parsing/confirm): takes resolved key+entry+flags, performs `gitwt.cleanup_worktree` + `_close_issue` + `_close_pr` + link drop, returns result dict. `cleanup` command: new `merged: bool = typer.Option(False, "--merged", ...)`; `ref` becomes `Optional[str]`; validation: `if merged and ref: _fail("--merged takes no ref", EXIT_USAGE)`; `if not ref and not merged: _fail("ref required", EXIT_USAGE)`; `if merged and not yes and not dry_run: _fail("--merged requires --yes (non-interactive)", EXIT_USAGE)`. Loop: `for key, entry in links.items():` → `state = (cli._status_cells(dict(entry), refresh_pr=True).get("pr_data") or {}).get("state", "")`; `if state not in ("MERGED", "CLOSED"): skip`. Guard: `store.active_harness(key)` → `skipped:live-harness`. Invalid worktree (`not worktrees.is_valid_worktree`) → `skipped:invalid` (never rmtree anything that isn't a live worktree). Collect rows; `_print_result({"results": rows}, json_output)`; Rich table otherwise.
  - `open_cmd` as per Interfaces (use `subprocess.Popen([opener, wt], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)`; opener by `sys.platform`: `darwin`→`open`, `win32`→`explorer`, else `xdg-open`; wrap FileNotFoundError → `_fail(f"no opener available ({opener})", EXIT_GENERAL)`; print `wt` on stdout).
  - webapp edits per Interfaces. `open` must NOT be added to the confirm→`--yes` tail list (:435-437).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat: harness open + cleanup --merged + webapp review --all allowlist (issue #7)`

### Task 5: Issue/candidate listing — trackers, endpoints, CLI

**Files:**
- Modify: `src/harness/trackers.py` (new `list_my_issues`)
- Modify: `src/harness/refs.py` (new `fetch_open_prs(tool, cwd)`)
- Modify: `src/harness/webapp.py` (GET `/api/issues`, GET `/api/candidates`)
- Modify: `src/harness/cli.py` (new `candidates` command; `_repo_tool` reuse)
- Test: `tests/test_trackers.py`, `tests/test_webapp.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `trackers.list_my_issues() -> list[dict]` — rows `{key, title, url, status, created}`; jira: `run_cmd("jira-cli", "request", "GET", f"search?jql={quote(jql)}&maxResults=100&fields=key,summary,status,created")` with `jql = 'reporter = currentUser() AND status in ("To-Do", "In Progress") AND created >= -2m ORDER BY created DESC'`; parse `.issues[].fields.status.name` etc. gh: `run_cmd("gh", "search", "issues", "--author=@me", "--state=open", "--limit", "100", "--json", "repository,number,title,updatedAt,url")` → key `github:<repo>#<n>`. glab issues intentionally omitted (Jira covers the work tracker; GitLab surfaces via PR/MR candidates). Any CLI failure → that source contributes `[]` and a warning is collected (never raises).
  - `refs.fetch_open_prs(tool: str, cwd: str) -> list[dict]` — gh `gh pr list --state open --limit 100 --json number,title,headRefName,updatedAt,url`; glab `glab mr list --state opened -F json --per-page 100`; normalized `{number, title, branch, updated, url, state:"OPEN"}`; raises HarnessError (caller decides).
  - `webapp`: `GET /api/issues` → `{"issues": [...], "warning": str | None}`; `GET /api/candidates` → `{"prs": [...], "issues": [...], "warnings": [...]}` (prs exclude any URL already in `store.load_links()` values' `pr_url`; issues filtered to `created >= now-7d`).
  - `harness candidates [--json] [--csv]` — two Rich tables: "Unlinked PR/MRs" (`url, title, repo, updated`) and "Recent issues (reported by me, last 7 days)" (`key, title, status, created`).

- [ ] **Step 1: Failing tests** (monkeypatch `trackers.run_cmd`/`refs.run_cmd`/`cli._repo_tool`)

```python
def test_list_my_issues_jira_request(monkeypatch):
    monkeypatch.setattr(trackers, "run_cmd", lambda *a: json.dumps({"issues": [
        {"key": "IPG-981", "fields": {"summary": "T", "status": {"name": "In Progress"},
                                       "created": "2026-09-20T10:00:00.000+0000"}}]}))
    rows = trackers.list_my_issues()
    assert rows[0]["key"] == "jira:IPG-981" and rows[0]["status"] == "In Progress"

def test_list_my_issues_gh_search(monkeypatch): ...
def test_list_my_issues_cli_missing(monkeypatch):
    def boom(*a): raise HarnessError("not found")
    monkeypatch.setattr(trackers, "run_cmd", boom)
    assert trackers.list_my_issues() == []

def test_api_issues_missing_cli(monkeypatch):
    # TestClient: 200 + warning field (Review Focus #4)
def test_candidates_exclude_linked(monkeypatch, tmp_cfg):
    linked pr_url == one of the open PR urls → excluded
def test_candidates_issue_window(monkeypatch):
    issue created 10d ago → excluded; 2d ago → included
def test_candidates_cli_json(...)  # two sections, machine-readable
```

- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** per Interfaces. Date filter: parse `created` ISO-8601 (jira format `+0000` → handle with `datetime.fromisoformat` after replacing `+0000`→`+00:00`); window `timedelta(days=7)`. `/api/candidates` iterates `store.load_config().get("repos", {})` paths with `Path.exists()` guard + per-repo try/except. CLI tables: reuse `_print_rows` twice (escape title text with `rich.markup.escape`).
- [ ] **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat: list my issues + candidates CLI/web endpoint (issue #7)`

### Task 6: Renames — labels, JSON key, TS identifiers, confirmDesc

**Files:**
- Modify: `web/src/lib/api.ts` (`SessionEntry`→`WorktreeEntry`, `SessionMap`→`WorktreeMap`, `SessionDetail`→`WorktreeDetail`, `Links.sessions`→`Links.worktrees`, `RunCommand` union += `"open"`)
- Modify: `web/src/components/StatusTable.tsx` (header "Session"→"Worktree", prop names `sessions`→`worktrees`, `sessionKey`→`key`), `web/src/pages/{Dashboard,Links,Launch}.tsx`, `web/src/pages/settings.ts`, `web/src/components/confirm.tsx`, `web/src/components/NotFound.tsx`, `web/src/components/AppLayout.tsx`, `web/src/components/WorktreeDetail.tsx`
- Modify: `src/harness/webapp.py` (`/api/links` key `"sessions"` → `"worktrees"` :410)
- Modify: `web/API_CONTRACT.md`
- Test: `tests/test_webapp.py` (existing `/api/links` test updated; type rename is tsc-verified)

**Interfaces:**
- Produces: JSON contract `{"trackers": {...}, "worktrees": {...}}`; TS types `WorktreeEntry/WorktreeMap/WorktreeDetail`; Launch COPY entries gain explicit `confirmDesc: string` field; the `copy.title === "Sync a session"` comparison (:179-180) is replaced by a structured field (e.g. `copy.confirm === false` marker or `confirmDesc` presence) so future label edits can't silently change confirm text.

- [ ] **Step 1: Failing test** — update the existing `/api/links` webapp test to expect `"worktrees"` key; add Launch-prefill contract test reading `links.worktrees`.
- [ ] **Step 2: FAIL.**
- [ ] **Step 3: Implement** — rename in order: (1) webapp key; (2) api.ts types + Links field; (3) mechanical TS identifier rename (IDE-wide or sed across `web/src`); (4) label sweep: every user-visible string "Session"/"Sessions"/"Linked sessions" → "Worktree"/"Worktrees"/"Linked worktrees"; (5) refactor `copy.title === "Sync a session"` → explicit `confirmDesc` per COPY entry; (6) verify with `grep -rniE "session" web/src --include=*.tsx --include=*.ts` — permitted leftovers: none in user-visible strings; identifiers must be gone (alias comment in WorktreeDetail.tsx:14-18 updated). **tsc build + eslint must pass.**
- [ ] **Step 4: PASS** (`uv run pytest tests/test_webapp.py -q`, `cd web && npm run build`).
- [ ] **Step 5: Commit** `refactor: rename sessions→worktrees in web UI and /api/links key (issue #7, breaking)`

### Task 7: Frontend features — search, Added Date, CI badge, open button, bulk buttons, invalid highlight, dropdown, candidates card

**Files:**
- Modify: `web/src/components/StatusTable.tsx` (search input via URL-param pattern from `Runs.tsx:27-51`; "Added" column; default sort `added_at` desc, missing → oldest; invalid-row highlight + Delete action; open-folder action replaces copy)
- Modify: `web/src/pages/Dashboard.tsx`, `web/src/pages/Links.tsx` (Review all / Cleanup merged buttons mirroring `handleSyncAll` :94-143; candidates card on Links)
- Modify: `web/src/components/SearchableSelect.tsx` (async mode: optional `fetchOptions?: () => Promise<{options: string[]; warning?: string}>`, loading/error states; `allowCustom` preserved)
- Modify: `web/src/pages/Launch.tsx` (Issue Ref box → async SearchableSelect fed by `GET /api/issues`; prefill `?ref=` unchanged; `links.worktrees` per Task 6)
- Create: `web/src/components/CandidatesCard.tsx` (two tabs: Unlinked PR/MRs, Recent issues; fetch on mount + refresh button; copy-ref button per row)
- Modify: `web/src/lib/api.ts` (`added_at`, `wt_valid`, `ci` fields — already partly in Task 3/6; `issues()`, `candidates()` client fns)
- Test: `tests/test_webapp.py` (endpoint-level only; UI verified by build + smoke)

**Interfaces:**
- Consumes: Task 3 `ci`, Task 4 open command + cleanup --merged, Task 5 `/api/issues` + `/api/candidates`, Task 6 types.
- Produces: nothing downstream (terminal UI task).

- [ ] **Step 1: api client + types** — `api.ts`: `issues(): Promise<{issues: IssueRow[]; warning?: string}>`, `candidates(): Promise<Candidates>`, types for both; `WorktreeEntry` gains `added_at?: string`, `wt_valid?: boolean`.
- [ ] **Step 2: StatusTable** — search: `useSearchParams`-backed `?q=` filter across key/branch/pr/worktree (case-insensitive substring), clearable chip (copy Runs.tsx pattern); Added column (`added_at` → locale date; missing → "—"); default sort: `[...entries].sort((a,b) => (b.added_at ?? "").localeCompare(a.added_at ?? ""))` (missing sorts last — Review Focus #5); invalid highlight: `wt_valid === false` → row red tint + "invalid" badge; actions: replace `onCopyPath` with `onOpenWorktree(key)` → `createRun({command:"open", args:[key], confirm:false})` (+ toast+navigate per runCreated pattern), disabled with tooltip when `network_exposed`; Delete action on invalid rows → existing cleanup confirm (force variant).
- [ ] **Step 3: Bulk buttons** — Dashboard + Links header: "Review all" → `createRun({command:"review", args:["--all"], confirm:true})`; "Cleanup merged" → `createRun({command:"cleanup", args:["--merged"], confirm:true})` with confirm dialog listing consequences (cleanup closes issues/PRs).
- [ ] **Step 4: SearchableSelect async + Launch** — async mode per Interfaces; Launch: fetch on open, client-side filter, loading spinner, warning banner (from API `warning`), keep free-text (`allowCustom`), selection writes the issue key into the ref field; `?ref=` prefill bypasses.
- [ ] **Step 5: CandidatesCard on Links** — mount fetch, refresh button, two tabs, per-row copy-ref; empty states.
- [ ] **Step 6: Verify** — `cd web && npm run build` + `npx eslint .` clean; `uv run pytest tests/test_webapp.py -q` green.
- [ ] **Step 7: Commit** `feat: web UX wave — search, added date, CI badge, open, bulk buttons, dropdown, candidates (issue #7)`

### Task 8: Docs + whole-feature verification

**Files:**
- Modify: `CHANGELOG.md` (Unreleased bullets), `AGENTS.md` (commands table: `open`, `candidates`, `cleanup --merged`, CI column, rename note), `web/API_CONTRACT.md` (Task 3-7 surface), `README.md` (only if it enumerates commands)

**Interfaces:** none (terminal).

- [ ] **Step 1: Docs** — CHANGELOG: picker fix; recorded-PR fix; CI status; open; candidates; renames (breaking `/api/links`); search/added_at; bulk buttons; invalid-worktree highlight. API_CONTRACT.md: new endpoints, new fields, breaking key rename, new run commands/flags. AGENTS.md: architecture rows for new CLI surface.
- [ ] **Step 2: Full verification** — `uv run pytest -q` (whole suite); `cd web && npm run build` + eslint; main checkout clean (`git -C <main> status --short` → empty).
- [ ] **Step 3: Real-process smokes** (in worktree, `HARNESS_CONFIG_DIR` isolated):
  - Picker: `script -qec "printf '\033[B\033[B\r' | harness cd <ambiguous-2-match-substring>"` or a pty-driven `harness cd` — confirm no staircase (or rely on terminator tests if no second worktree fixture is practical; report which was done).
  - `harness candidates --json` against a registered repo (or confirm graceful empty output with warnings when no host CLI).
  - `harness status` — CI column present, `-` for PR-less rows.
- [ ] **Step 4: Commit** `docs: issue #7 — CI status, open, candidates, renames, web UX wave`

## Self-Review

- Spec coverage: G1→T1, G2→T2, G3→T3, G4→T4+T7, G5→T5+T7, G6→T6, G7→T5(store)+T7, G8→T4+T7, G9→T5+T7, store cleanup→T2, contract docs→T6/T8. ✓
- Placeholders: test sketches marked "adapt to fixtures"; all implementation steps carry concrete code/decisions. ✓
- Type consistency: `fetch_ci_status`/`list_my_issues`/`fetch_open_prs`/`_cleanup_one`/`_recorded_pr` used with the same names across tasks; web types defined in T3/T5/T6 before T7 consumes. ✓
- Review Focus: all six pinned (T1, T2, T3, T5, T5+T7, T6). ✓
