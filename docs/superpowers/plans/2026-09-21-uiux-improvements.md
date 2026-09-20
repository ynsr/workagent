# UI/UX Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full-width layout, Links modals, shared WorktreeDetail, Launch prefill + --no-harness, searchable dropdowns, picker redraw fix.

**Architecture:** Shared web components first (`WorktreeDetail`, `SearchableSelect`), then page-by-page application; TUI picker fix is independent and lands anytime.

**Tech Stack:** React + TS + Tailwind (web/), Python picker (CLI), pytest + tsc + eslint.

**Spec:** `docs/superpowers/specs/2026-09-21-uiux-design.md`

## Global Constraints

- stdout carries ONLY data; every log/progress/confirmation line → stderr (CLI side).
- Web build stays green: `cd web && npm run build` (tsc) and eslint clean.
- Full suite `uv run pytest -v` green.
- Reuse existing `useConfirm` modal pattern and `useCreateRun`/`toast`/invalidate flow; no new modal library.

## Review Focus

- Launch opened with an unknown `?ref=` shows a blank form plus warning toast, never a crash.
- `SearchableSelect` with an empty options list still allows submitting the raw typed value where the backend accepts free text.
- Links modal closed mid-run-submit never fires the command twice.
- Picker on a non-TTY returns None without touching the terminal.
- Picker given an empty options list returns None without writing escape codes.

---

### Task 1: Picker redraw fix

**Files:**
- Modify: `src/harness/pick.py:64-76,130-141` (`_draw`, `_erase`, redraw loop)
- Test: `tests/test_pick.py` (append)

**Interfaces:**
- Consumes: nothing new.
- Produces: redraw erases the full option block before each draw; exported names unchanged.

- [ ] **Step 1: Write the failing test**

```python
def test_navigation_leaves_no_padding():
    """Up/down cycles must not accumulate leading spaces (issue #4)."""
    from harness import pick as P
    import io
    # down, up, enter
    keys = ["\x1b[B", "\x1b[A", "\r"]
    it = iter(keys)
    stream = io.StringIO()
    got = P.pick_index("label", ["aaa", "bbb", "ccc"],
                       read=lambda n: next(it), stream=stream)
    assert got == 0
    for line in stream.getvalue().splitlines():
        stripped = line.lstrip("\x1b[2K")
        assert stripped == stripped.lstrip(" "), f"padding artifact: {line!r}"
```

Check `tests/test_pick.py` for the exact escape sequences the existing tests feed `read` (align `\x1b[B` with the repo's convention) before finalizing.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pick.py::test_navigation_leaves_no_padding -v`
Expected: FAIL with "padding artifact".

- [ ] **Step 3: Write minimal implementation**

In `_draw`, prefix each redraw with a full block erase: move cursor up N lines, erase each line, then draw (mirror the `git-wt cleanup` cleared-screen UX within the option block only, not the whole terminal):

```python
def _draw(stream, options, idx: int) -> None:
    for i, opt in enumerate(options):
        stream.write(_option_line(opt, i == idx))
    stream.flush()
```

Change the navigation redraw in `pick_index` from `stream.write(_CURSOR_UP * len(options))` + `_draw` to a `_redraw(stream, options, idx)` helper that erases every line of the block first:

```python
def _redraw(stream, options, idx: int) -> None:
    stream.write(_CURSOR_UP * len(options))
    for _ in options:
        stream.write(_ERASE_LINE + "\n")
    stream.write(_CURSOR_UP * len(options))
    _draw(stream, options, idx)
```

Also make `_option_line` strip any caller-supplied leading whitespace from names so padded labels can never recur: `name = name.strip()` — NO, that changes displayed labels; instead assert no leading spaces are added by the renderer itself (keep labels verbatim).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pick.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/harness/pick.py tests/test_pick.py
git commit -m "fix: erase picker block on redraw to stop padding"
```

### Task 2: Shared web components

**Files:**
- Create: `web/src/components/WorktreeDetail.tsx`, `web/src/components/SearchableSelect.tsx`
- Test: `cd web && npm run build` + eslint on the two files

**Interfaces:**
- Consumes: `@/lib/api` `SessionMap` entry type (rename to `WorktreeMap` here if the backend already switched; else alias locally).
- Produces: `WorktreeDetail({ entry, mode, onSave, onClose })`, `SearchableSelect({ value, options, onChange, placeholder })` — Tasks 3-4 import these exact names/props.

- [ ] **Step 1: Write the failing check**

Create both files with the prop signatures above, each rendering a minimal stub (`WorktreeDetail` shows `entry.branch`; `SearchableSelect` renders a native `select` over `options`). Import them in a scratch route? No — keep it simple: the check is `npm run build` failing on missing exports until the real components exist. Start by writing a temporary import in `Dashboard.tsx`? NO — instead write the real components directly (they are new files; TDD-by-test is impractical for UI stubs, so verification is tsc + eslint + visual).

- [ ] **Step 2: Run check to verify it fails**

Run: `cd web && npm run build`
Expected: FAIL on the new files' type errors (fix iteratively).

- [ ] **Step 3: Write minimal implementation**

`SearchableSelect.tsx`:

```tsx
import { useMemo, useState } from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export function SearchableSelect({ value, options, onChange, placeholder }: {
  value: string
  options: string[]
  onChange: (v: string) => void
  placeholder?: string
}) {
  const [q, setQ] = useState("")
  const filtered = useMemo(
    () => options.filter((o) => o.toLowerCase().includes(q.toLowerCase())),
    [options, q],
  )
  return (
    <div>
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder ?? "Type to filter…"} />
      <div className="max-h-40 overflow-auto">
        {filtered.map((o) => (
          <button key={o} type="button" onClick={() => onChange(o)}
            className={cn("block w-full text-left", o === value && "font-bold")}>
            {o}
          </button>
        ))}
      </div>
    </div>
  )
}
```

`WorktreeDetail.tsx`: card showing `entry.branch`, `entry.worktree`, `entry.pr_url`, `entry.repo` with view mode text and edit mode `Input`s bound to a draft + Save/Cancel buttons calling `onSave(draft)` / `onClose()`. Follow the existing `StatusTable.tsx` field names exactly (read that file first; adjust prop access to match).

- [ ] **Step 4: Run check to verify it passes**

Run: `cd web && npm run build && npx eslint src/components/WorktreeDetail.tsx src/components/SearchableSelect.tsx`
Expected: PASS (build succeeds, no lint errors).

- [ ] **Step 5: Commit**

```bash
git add web/src/components/WorktreeDetail.tsx web/src/components/SearchableSelect.tsx
git commit -m "feat: add shared WorktreeDetail and SearchableSelect"
```

### Task 3: Layout + Links modals + Dashboard rows

**Files:**
- Modify: `web/src/App.tsx`, `web/src/index.css`, `web/src/pages/Links.tsx`, `web/src/pages/Dashboard.tsx`
- Test: build + eslint + manual click-through

**Interfaces:**
- Consumes: `WorktreeDetail`, `SearchableSelect` from Task 2; `useCreateRun`, `toast`, `queryKeys` existing patterns.
- Produces: full-width main; Links cards as modal buttons; Dashboard rows use `WorktreeDetail` with Sync/Review → `/launch?mode=X&ref=<key>`.

- [ ] **Step 1: Write the failing check**

Run: `cd web && grep -rn "max-w-" src/App.tsx src/index.css | head`
Expected: current constrained width (e.g. `max-w-7xl`) present — the thing to remove.

- [ ] **Step 2: Change layout**

In `App.tsx` main content wrapper replace the constraining class with `max-w-none w-full`; inner content gets `flex-1 min-w-0`. Sidebar classes untouched.

- [ ] **Step 3: Convert Links cards to modals**

For each of `TrackerMappingsCard`, `LinkSetCard`, `LinkRemoveCard`, `RegisterCard` in `Links.tsx`: keep the card's fields, move them into a modal opened by a button (follow `useConfirm` dialog pattern from `@/lib/confirm`); submit via the existing `LinkRunButtons`/`useCreateRun` flow; `toast` + invalidate on success. Replace free-text repo/key inputs with `SearchableSelect` where options are enumerable (repos from `useRepos`); keep plain `Input` where not possible by design.

- [ ] **Step 4: Dashboard rows + nav**

Render `WorktreeDetail mode="view"` in table rows (or expand); row Sync/Review buttons call `navigate('/launch?mode=sync&ref=' + encodeURIComponent(key))` (resp. `mode=review`).

- [ ] **Step 5: Verify and commit**

Run: `cd web && npm run build && npx eslint src/pages/Links.tsx src/pages/Dashboard.tsx src/App.tsx`
Expected: PASS. Manual: open each modal, submit once; Dashboard → Launch prefill shows the key.

```bash
git add web/src/App.tsx web/src/index.css web/src/pages/Links.tsx web/src/pages/Dashboard.tsx
git commit -m "feat: full-width layout, Links modals, shared worktree rows"
```

### Task 4: Launch prefill + --no-harness + dropdowns

**Files:**
- Modify: `web/src/pages/Launch.tsx`, `src/harness/webapp.py` (`command_argv` for start), `src/harness/cli.py` (`start --no-harness` flag if missing)
- Test: build + `uv run pytest tests/test_webapp.py tests/test_cli.py -k "launch or start or no_harness"`

**Interfaces:**
- Consumes: Task 3 nav contract (`/launch?mode=X&ref=<key>`).
- Produces: Launch prefills ref/mode from search params; `start` accepts `-N/--no-harness` end to end (CLI + web run).

- [ ] **Step 1: Check current start flags**

Run: `grep -n "no_harness\|no-harness" src/harness/cli.py | head`
Expected: `review` has it; `start` may not — confirm before adding.

- [ ] **Step 2: Add flag + backend passthrough**

If `start` lacks `--no-harness`/`-N`: add `no_harness: bool = typer.Option(False, "-N", "--no-harness", help="Skip launching the harness...")`, thread it through `_run_harness` exactly like `review` does. In `webapp.py` `command_argv`, accept and forward `--no-harness` for `start` runs.

- [ ] **Step 3: Prefill + dropdowns in Launch.tsx**

```tsx
import { useSearchParams } from "react-router-dom"
// inside Launch():
const [params] = useSearchParams()
const [mode, setMode] = useState<Mode>(params.get("mode") === "review" ? "review" : "start")
const [form, setForm] = useState<LaunchForm>({ ...INITIAL, ref: params.get("ref") ?? "" })
```

Add `noHarness` checkbox (start mode only) appending `--no-harness` in `buildArgs()`. Replace repo/harness/base `Input`s with `SearchableSelect` (repos from `useRepos()` names; harness fixed list `["omp"]`; base free-text `Input` stays unless a branch endpoint exists — document the choice in the commit message).

- [ ] **Step 4: Verify and commit**

Run: `cd web && npm run build && npx eslint src/pages/Launch.tsx && cd .. && uv run pytest tests/test_webapp.py -q`
Expected: PASS.

```bash
git add web/src/pages/Launch.tsx src/harness/webapp.py src/harness/cli.py tests/
git commit -m "feat: launch prefill, start --no-harness, searchable dropdowns"
```
