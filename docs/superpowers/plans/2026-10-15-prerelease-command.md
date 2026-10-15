# Pre-Release Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-repo `pre-release` quality gate: stored on the repo row, auto-detected at add-time, editable in the Repos table, executed before MR-open in `run_release`, aborting the release on failure.

**Architecture:** `commands TEXT DEFAULT '{}'` JSON column on `repos` (v11 migration); pure `detect_prerelease_command(path)` in `repos.py`; `--pre-release` flags on `repo add/update` plumbed through `register_repo` → `register_repo_row` → `load_repos` → `/api/repos`; `GET /api/repos/detect` read-only endpoint for add-form autofill; Repos table click-to-edit cell + add-form field; gate step in `run_release` via existing `run_cmd` convention.

**Tech Stack:** Python (Typer CLI, stdlib sqlite3, no ORM), FastAPI web backend, React + TS frontend (Vite build, `tsc` typecheck).

**Spec:** `docs/superpowers/specs/2026-10-15-prerelease-command-design.md`

## Global Constraints

- Terminology is **Release** everywhere; the word Deploy MUST NOT appear in command names, flags, columns, buttons, or components.
- stdout carries ONLY data; every log/progress/confirmation line → stderr.
- Exit codes: `0` success, `1` general error, `2` usage/needs-human-input.
- Non-interactive default: confirmations only on a TTY; non-TTY or `--yes` acts/fails with an actionable message.
- Python files and React components/classes must not exceed 500 lines.
- CLI owns the logic; `web_args.py` `SPECS`/`BOOL_FLAGS`/`VAL_FLAGS` mirror it — `tests/test_webapp.py::test_specs_mirror_cli_flags` fails on drift.
- Parity checklist per feature: CLI flag → web arg passthrough → `API_CONTRACT.md` entry → frontend control → both test files.
- `CHANGELOG.md` entries <= 150 chars, append issue ID in brackets (`[no-issue]` here); gap-insert only, verify `git diff` shows additions alone.
- Commit + push on `main` only after tests pass; never auto-commit on `main` — wait, this repo's rule says commit/push on main automatically. Follow AGENTS.md: commit and push automatically unless asked otherwise.
- After work: re-run `./install.sh` so the daemon picks up new routes.

## Review Focus

- Gate command containing shell metacharacters (`;`, `&&`, quotes) executes exactly as the user typed it — no argv-splitting mangling.
- `commands` holding invalid JSON (hand-edited DB) reads back as no-gate, never a crash.
- Detect on a path with colliding markers (e.g. `package.json` + `pom.xml`) always picks Node, deterministically.
- `repo update NAME` with no flags still errors "nothing to update" (new flag joins the guard, doesn't bypass it).
- Release with gate set but repo path deleted/moved fails loud with the path in the message, never silently skips the gate.

---

### Task 1: Schema — `commands` column + migration + accessors

**Files:**
- Modify: `src/workagent/store_sqlite.py:18-23` (SCHEMA repos line), `:253-266` (add `_migrate_repos_commands` after `_migrate_repos_release`), `:501` (`init_db` chain)
- Modify: `src/workagent/store_trackers.py:126` (SELECT), `:131-136` (dict), `:200-204` (signature), `:235-250` (preserve + upsert)
- Test: `tests/test_cli_release.py` (append migration/accessor tests; mirror module names)

**Interfaces:**
- Consumes: existing `_migrate_repos_release(conn)` pattern, `register_repo_row`, `load_repos`.
- Produces: `commands` JSON str in DB; `load_repos()` dicts gain `"commands": dict`; `register_repo_row(..., commands: dict | None = None)` with None-preserve semantics.

- [ ] **Step 1: Write the failing migration test**

```python
def test_repos_commands_migration_backfills_empty_object(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    with sq.connect(db) as conn:
        conn.executescript("CREATE TABLE repos (key_ref TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL);")
        conn.execute("INSERT INTO repos (key_ref, path, name) VALUES ('r', '/x', 'r')")
    sq.init_db(db)
    with sq.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
        assert "commands" in cols
        val = conn.execute("SELECT commands FROM repos WHERE key_ref = 'r'").fetchone()[0]
        assert val == "{}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py::test_repos_commands_migration_backfills_empty_object -v`
Expected: FAIL (no `commands` column).

- [ ] **Step 3: Write minimal migration + SCHEMA change**

```python
def _migrate_repos_commands(conn) -> None:
    """Schema v11: repos gain commands JSON object ('{}' = no gates).

    ALTER TABLE backfills DEFAULT '{}' — existing rows get no gate.
    Idempotent: skipped when present.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(repos)")}
    if not cols:
        return
    if "commands" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN commands TEXT NOT NULL DEFAULT '{}'")
```

SCHEMA repos line gains `commands TEXT NOT NULL DEFAULT '{}'`; call `_migrate_repos_commands(conn)` in `init_db` after `_migrate_repos_release(conn)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py::test_repos_commands_migration_backfills_empty_object -v`
Expected: PASS.

- [ ] **Step 5: Write the failing accessor tests**

```python
def test_load_repos_carries_commands_dict(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import store_trackers as st
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    monkeypatch.setattr(st, "connect", lambda p: sq.connect(db))
    sq.init_db(db)
    st.register_repo_row(db, "r", "/x", "jira:A", remote="", tool="",
                         commands={"pre-release": "npm test"})
    repos = st.load_repos(db)
    assert repos["r"]["commands"] == {"pre-release": "npm test"}


def test_register_repo_row_none_preserves_commands(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import store_trackers as st
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    st.register_repo_row(db, "r", "/x", "jira:A", remote="", tool="",
                         commands={"pre-release": "npm test"})
    st.register_repo_row(db, "r", "/x", "jira:A", remote="", tool="")
    assert st.load_repos(db)["r"]["commands"] == {"pre-release": "npm test"}


def test_load_repos_bad_json_is_no_gate(tmp_path, monkeypatch):
    from workagent import store_sqlite as sq
    from workagent import store_trackers as st
    db = tmp_path / "state.db"
    monkeypatch.setattr(sq, "db_path", lambda: db)
    sq.init_db(db)
    with sq.connect(db) as conn:
        conn.execute("INSERT INTO repos (key_ref, path, name, commands) VALUES ('r', '/x', 'r', 'not-json{{{')")
    assert st.load_repos(db)["r"]["commands"] == {}
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "commands" -v`
Expected: FAIL (SELECT lacks `commands`, signature lacks the param).

- [ ] **Step 7: Write minimal accessor changes**

In `store_trackers.py`: SELECT gains `commands`; dict gains `"commands": _parse_commands(d.get("commands"))` with module-level helper:

```python
def _parse_commands(raw: object) -> dict:
    """commands JSON text → dict; invalid/empty → {} (no gate, never raise)."""
    import json
    if not raw or not isinstance(raw, str):
        return {}
    try:
        val = json.loads(raw)
    except Exception:
        return {}
    return val if isinstance(val, dict) else {}
```

`register_repo_row` gains `commands: dict | None = None`; read stored value (`SELECT ... commands ...`), `new_commands = stored if commands is None else (commands or {})`, serialize with `json.dumps(new_commands)` in the upsert (INSERT column list + `commands=excluded.commands`).

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "commands" -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/workagent/store_sqlite.py src/workagent/store_trackers.py tests/test_cli_release.py
git commit -m "repos commands column with migration and accessors [no-issue]"
```

### Task 2: Detection — `detect_prerelease_command(path)`

**Files:**
- Modify: `src/workagent/repos.py:220-256` (new function + wire into `register_repo`)
- Test: `tests/test_cli_release.py` (detection matrix)

**Interfaces:**
- Consumes: nothing (pure; `pathlib` + `json` stdlib only).
- Produces: `repos.detect_prerelease_command(path: str | Path) -> str | None`; `register_repo(..., pre_release: str | None = None)` — None → auto-detect, `""` → clear to null.

- [ ] **Step 1: Write the failing detection matrix test**

```python
import json
import pytest
from workagent import repos as _repos

CASES = [
    ("node-test", {"package.json": json.dumps({"scripts": {"test": "jest"}})}, "npm test"),
    ("node-build", {"package.json": json.dumps({"scripts": {"build": "tsc"}})}, "npm run build"),
    ("node-bare", {"package.json": json.dumps({"name": "x"})}, None),
    ("maven", {"pom.xml": "<project/>"}, "mvn clean; mvn verify"),
    ("gradle", {"build.gradle": ""}, "gradle clean test"),
    ("gradle-wrapper", {"build.gradle": "", "gradlew": "#!/bin/sh"}, "./gradlew clean test"),
    ("python-uv", {"pyproject.toml": "[project]", "uv.lock": ""}, "uv sync; uv run pytest"),
    ("python-pep517", {"pyproject.toml": "[project]"}, "pip install -e .; python -m pytest"),
    ("python-reqs", {"requirements.txt": "pytest"}, "pip install -r requirements.txt; python -m pytest"),
    ("empty", {}, None),
    ("collision", {"package.json": json.dumps({"scripts": {"test": "jest"}}), "pom.xml": "<project/>"}, "npm test"),
]

@pytest.mark.parametrize("name,files,expected", CASES)
def test_detect_prerelease_matrix(tmp_path, name, files, expected):
    d = tmp_path / name
    d.mkdir()
    for fname, content in files.items():
        (d / fname).write_text(content)
    if "gradlew" in files:
        import os
        os.chmod(d / "gradlew", 0o755)
    assert _repos.detect_prerelease_command(d) == expected


def test_detect_prerelease_unreadable_path():
    assert _repos.detect_prerelease_command("/nonexistent-path-xyz") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "detect_prerelease" -v`
Expected: FAIL (`detect_prerelease_command` not defined).

- [ ] **Step 3: Write minimal implementation in `repos.py`**

```python
def detect_prerelease_command(path) -> str | None:
    """Auto-detect a pre-release gate command for *path*; None when unknown.

    Pure function, never raises (unreadable path → None). Priority on
    collision: Node > Maven > Gradle > Python.
    """
    import json as _json
    import os as _os
    from pathlib import Path as _P
    try:
        root = _P(str(path)).expanduser()
        if not root.is_dir():
            return None
    except Exception:
        return None
    try:
        pkg = root / "package.json"
        if pkg.is_file():
            try:
                scripts = (_json.loads(pkg.read_text()) or {}).get("scripts") or {}
            except Exception:
                scripts = {}
            if scripts.get("test"):
                return "npm test"
            if scripts.get("build"):
                return "npm run build"
            return None
        if (root / "pom.xml").is_file():
            return "mvn clean; mvn verify"
        if ((root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file()
                or (root / "settings.gradle").is_file() or (root / "settings.gradle.kts").is_file()
                or (root / "gradlew").is_file()):
            gw = root / "gradlew"
            try:
                if gw.is_file() and _os.access(gw, _os.X_OK):
                    return "./gradlew clean test"
            except Exception:
                pass
            return "gradle clean test"
        if (root / "pyproject.toml").is_file():
            if (root / "uv.lock").is_file():
                return "uv sync; uv run pytest"
            return "pip install -e .; python -m pytest"
        if (root / "requirements.txt").is_file():
            return "pip install -r requirements.txt; python -m pytest"
    except Exception:
        return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "detect_prerelease" -v`
Expected: PASS.

- [ ] **Step 5: Wire into `register_repo` (auto-detect on None, clear on "")**

Add `pre_release: str | None = None` param; before the SQLite/config write:

```python
    if pre_release is None:
        try:
            pre_release = detect_prerelease_command(path)
        except Exception:
            pre_release = None
    gate = {} if not pre_release else {"pre-release": pre_release}
```

Pass `commands=gate` to `sq.register_repo_row(...)`; config.json fallback entry gains `"commands": gate`.

- [ ] **Step 6: Commit**

```bash
git add src/workagent/repos.py tests/test_cli_release.py
git commit -m "auto-detect pre-release command by project type [no-issue]"
```

### Task 3: CLI — `repo add/update --pre-release`, `repo list` column

**Files:**
- Modify: `src/workagent/cli_manage.py:36-61` (add flag + pass-through), `:97-104` (list column), `:107-151` (update flag + guard + writes)
- Test: `tests/test_cli_release.py` (CLI flag tests — use CliRunner against `repo_add`/`repo_update`, or subprocess `workagent repo add` with isolated config; follow the existing test file's runner pattern)

**Interfaces:**
- Consumes: `repos.register_repo(..., pre_release)`, `register_repo_row(..., commands)`.
- Produces: `--pre-release TEXT` on add/update; list shows `pre-release` column.

- [ ] **Step 1: Write the failing CLI tests**

```python
def test_repo_add_autodetects_prerelease(runner, tmp_repo_node):
    result = runner.invoke(repo_app, ["add", "--name", "n", "--path", str(tmp_repo_node), "--tracker", "jira:A", "--json"])
    assert result.exit_code == 0
    assert '"pre-release": "npm test"' in result.stdout or "npm test" in result.stdout


def test_repo_add_explicit_overrides_and_clear(runner, tmp_repo_node):
    assert runner.invoke(repo_app, ["add", "--name", "n", "--path", str(tmp_repo_node), "--tracker", "jira:A", "--pre-release", "make check"]).exit_code == 0
    assert runner.invoke(repo_app, ["update", "n", "--pre-release", ""]).exit_code == 0
    out = runner.invoke(repo_app, ["list", "--json"])
    assert "make check" not in out.stdout


def test_repo_update_no_flags_still_usage(runner):
    result = runner.invoke(repo_app, ["update", "somename"])
    assert result.exit_code == 2
```

(Adapt fixture/runner names to the file's existing conventions — read the top of `tests/test_cli_release.py` first and use its runner + isolation pattern verbatim.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "prelease or prerelease or pre_release" -v`
Expected: FAIL (unknown flag `--pre-release`).

- [ ] **Step 3: Write minimal CLI changes**

`repo_add`: add `pre_release: Optional[str] = typer.Option(None, "--pre-release", help="Pre-release gate command (default: auto-detect from project type; empty string clears).")`; pass `pre_release=pre_release` to `repos.register_repo(...)`.
`repo_update`: add same flag; guard becomes `if default_branch is None and release_branch is None and pre_release is None: _fail(...)`; SQLite branch writes the merged commands JSON (`SELECT commands`, merge `pre-release` key: None-preserve, `""`-clears, else set), config.json fallback mirrors; result dict gains `"pre-release"`.
`repo_list`: items gain `"pre-release": (v.get("commands") or {}).get("pre-release", "")`; header list gains `"pre-release"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "prelease or prerelease or pre_release" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_manage.py tests/test_cli_release.py
git commit -m "repo add/update --pre-release flag and list column [no-issue]"
```

### Task 4: Release gate — execute in `run_release`, abort on failure

**Files:**
- Modify: `src/workagent/cli_release.py:106-159` (gate step between plan/dry-run and MR-open)
- Test: `tests/test_cli_release.py` (gate tests, mocking `_open_mr`)

**Interfaces:**
- Consumes: `store.load_repos()[name]["commands"]`, `run_cmd` from `errors.py`.
- Produces: failing gate → `_fail("pre-release command failed: ...", EXIT_GENERAL)`; dry-run prints command without executing; JSON result gains `"pre_release"` (command string or `""`).

- [ ] **Step 1: Write the failing gate tests**

```python
def test_release_failing_gate_aborts_before_mr(monkeypatch, registry_with_gate):
    import workagent.cli_release as rel
    calls = []
    monkeypatch.setattr(rel, "_open_mr", lambda *a, **k: calls.append(a) or "http://mr/1")
    with pytest.raises(SystemExit) as e:
        rel.run_release("gated", yes=True, dry_run=False, json_output=True)
    assert e.value.code == 1
    assert calls == []


def test_release_no_gate_goes_to_mr(monkeypatch, registry_without_gate):
    import workagent.cli_release as rel
    monkeypatch.setattr(rel, "_open_mr", lambda *a, **k: "http://mr/1")
    monkeypatch.setattr(rel, "_set_ff_auto_merge", lambda *a, **k: None)
    monkeypatch.setattr(rel, "_merge_state", lambda *a, **k: {"state": "merged"})
    out = rel.run_release("plain", yes=True, dry_run=False, json_output=True)
    assert out["mr_url"] == "http://mr/1"


def test_release_dry_run_shows_gate_without_executing(monkeypatch, registry_with_gate, capsys):
    import workagent.cli_release as rel
    opened = []
    monkeypatch.setattr(rel, "_open_mr", lambda *a, **k: opened.append(a) or "http://mr/1")
    out = rel.run_release("gated", yes=True, dry_run=True, json_output=True)
    assert out["dry_run"] is True and opened == []
    assert "pre-release" in capsys.readouterr().err
```

(Fixtures: isolated registry with one repo row carrying `commands={"pre-release": "exit 1"}` vs `{}`; follow the file's existing isolation pattern.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "gate or dry_run" -v`
Expected: FAIL (no gate step; MR opens despite gate).

- [ ] **Step 3: Write minimal gate step**

Insert after the `dry_run` early-return block (`cli_release.py:140-142`), before the confirm prompt:

```python
    gate = ((registry.get(name) or {}).get("commands") or {}).get("pre-release") or ""
    plan["pre_release"] = gate
    if dry_run:
        eprint(f"dry-run: would open MR ...")
        if gate:
            eprint(f"dry-run: would run pre-release: {gate}")
        return {**plan, "dry_run": True, "merged": False, "mr_url": ""}
    if gate:
        from .errors import run_cmd as _run
        eprint(f"running pre-release for {name}: {gate}")
        try:
            _run("/bin/sh", "-c", gate, cwd=path, timeout=1800)
        except HarnessError as e:
            _fail(f"pre-release command failed for {name}: {e}", EXIT_GENERAL)
```

Note: gate runs with `sh -c` (single string, metacharacters preserved — Review Focus line 1); timeout 1800s for test suites; failure message carries the underlying reason. `plan` gains `pre_release` so `--json` shows it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli_release.py -k "gate or dry_run or release" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/cli_release.py tests/test_cli_release.py
git commit -m "run pre-release gate before release MR-open [no-issue]"
```

### Task 5: Web backend — flag mirror + detect endpoint

**Files:**
- Modify: `src/workagent/web_args.py:49-52` (BOOL unchanged), `:69-70` (VAL_FLAGS `repo add`/`repo update` gain `"--pre-release"`), `webapp.py:177-184` (`/api/repos` already spreads — verify `commands` flows, add explicit if spread is allow-listed), new `GET /api/repos/detect?path=`
- Test: `tests/test_webapp.py` (specs-mirror is automatic; add detect-endpoint test)

**Interfaces:**
- Consumes: `repos.detect_prerelease_command`, `load_repos`.
- Produces: `GET /api/repos/detect?path=<localpath>` → `{"pre-release": str | null}` always 200.

- [ ] **Step 1: Write the failing endpoint test**

```python
def test_repos_detect_endpoint(client, tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    r = client.get("/api/repos/detect", params={"path": str(tmp_path)})
    assert r.status_code == 200
    assert r.json() == {"pre-release": "mvn clean; mvn verify"}


def test_repos_detect_unknown_path_is_null(client):
    r = client.get("/api/repos/detect", params={"path": "/nonexistent-xyz"})
    assert r.status_code == 200
    assert r.json() == {"pre-release": None}
```

(Follow `tests/test_webapp.py`'s client-fixture convention.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_webapp.py -k "detect" -v`
Expected: FAIL (404 no route).

- [ ] **Step 3: Write minimal backend changes**

VAL_FLAGS: `"repo add": (..., "--pre-release")`, `"repo update": (..., "--pre-release")`. New endpoint next to `/api/repos` in `webapp.py`:

```python
    @app.get("/api/repos/detect")
    def repos_detect(path: str = "") -> dict:
        """Auto-detect the pre-release gate for a local path (read-only)."""
        from . import repos as _repos
        try:
            cmd = _repos.detect_prerelease_command(path)
        except Exception:
            cmd = None
        return {"pre-release": cmd}
```

If `/api/repos` spread is allow-listed, add `commands` explicitly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_webapp.py -k "detect or specs_mirror" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/workagent/web_args.py src/workagent/webapp.py tests/test_webapp.py
git commit -m "web mirror for pre-release flag plus detect endpoint [no-issue]"
```

### Task 6: Web frontend — add-form field + table edit cell + contract docs

**Files:**
- Modify: `web/src/pages/Repos.tsx` (add-form `pre-release` field with blur-autofill; table `Pre-release` column with click-to-edit; mobile card row)
- Modify: `web/src/lib/api.ts:80-88` (`Repo` gains `commands?: { "pre-release"?: string | null }`)
- Modify: `web/API_CONTRACT.md` (`/api/repos` field + `/api/repos/detect` section), `web/FEATURE_INVENTORY.md` (flag inventory)
- Test: `tests/test_webapp.py` (already covers flags); frontend: `npm run typecheck && npm run build`

**Interfaces:**
- Consumes: `GET /api/repos/detect`, `POST /api/runs {command: "repo", args: ["update", name, "--pre-release", v]}`.
- Produces: autofilled overridable field; inline-edited cell.

- [ ] **Step 1: Add `commands` to the `Repo` type**

```typescript
commands?: { "pre-release"?: string | null }
```

- [ ] **Step 2: Add-form field with blur autofill**

State `preRelease` + `preReleaseTouched` + `detecting`. Path input gains `onBlur`: if path non-empty and field untouched, `GET /api/repos/detect?path=` → fill on success, ignore failures silently (field stays user-editable). `handleAdd` appends `...(preRelease.trim() ? ["--pre-release", preRelease.trim()] : [])` and resets the field. Label: `Pre-release gate (optional)`; placeholder: `auto-detected on path blur — empty = no gate`.

- [ ] **Step 3: Table `Pre-release` column with click-to-edit**

New `<TableHead>Pre-release</TableHead>` after Branches (desktop table + mobile card). Cell: dotted-underline button showing `commands["pre-release"]` or muted `set gate`; click → existing confirm dialog pattern with an input extra (mirror `handleEditBranches`/`BranchEditFields` — add `initialPreRelease` + third ref); save dispatches `repo update NAME --pre-release <value>` (empty string clears). Failure toasts, value reverts (no optimistic write).

- [ ] **Step 4: Update contract docs**

`API_CONTRACT.md`: `commands` in `/api/repos` row shape + new `/api/repos/detect` subsection. `FEATURE_INVENTORY.md`: `--pre-release` under repo add/update.

- [ ] **Step 5: Typecheck + build**

Run: `cd web && npm run typecheck && npm run build`
Expected: clean `tsc`, successful `vite build`.

- [ ] **Step 6: Commit**

```bash
git add web/src/pages/Repos.tsx web/src/lib/api.ts web/API_CONTRACT.md web/FEATURE_INVENTORY.md
git commit -m "Repos page pre-release field and inline edit cell [no-issue]"
```

### Task 7: Full verification + changelog + install

**Files:**
- Modify: `CHANGELOG.md` (gap-insert under `## Unreleased`)
- Test: full suite + install

- [ ] **Step 1: Append changelog entry (gap insert, verify additive diff)**

```bash
# entry (<=150 chars):
- Repos gain a `pre-release` gate (auto-detected per project type, editable in Repos page) run before release MR-open; failure aborts [no-issue]
```

Verify: `git diff -- CHANGELOG.md` shows `+1 -0`.

- [ ] **Step 2: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 3: Reinstall and verify daemon**

Run: `./install.sh`
Expected: `doctor: ok`, new routes live.

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "changelog for pre-release gate [no-issue]"
git push
```
