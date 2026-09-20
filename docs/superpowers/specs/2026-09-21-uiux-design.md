# UI/UX Improvements — Design Spec

**Date:** 2026-09-21
**Issue:** ynsr/harness#4 — Missing Forms/Flows, UI/UX simplify
**Status:** Approved (in-chat section review)

## 1. Background

Links page uses inline forms; Dashboard and Links render sessions with
separate components; Launch lacks a harness-skip option and prefill; text
inputs should be searchable dropdowns; the TUI arrow-key picker accumulates
leading padding after navigation.

## 2. Goals

- Main content uses full width; sidebar unchanged.
- Links forms → buttons + modals; one shared `WorktreeDetail` view/edit
  component for Dashboard + Links.
- Launch: `--no-harness` skip for `start`; Sync/Review buttons prefill
  Launch with the selected worktree.
- Text boxes → searchable dropdowns unless impossible by design.
- Picker redraw leaves no padding artifact.

## 3. Non-goals

- Visual rebrand, theming, or sidebar changes.
- New backend endpoints except an optional branch-list endpoint for the
  Launch base-branch dropdown (fallback: free-text input stays).

## 4. Layout

- `web/src/App.tsx` + `web/src/index.css`: main content wrapper
  `max-w-none w-full`; inner content `flex-1 min-w-0`.
- Sidebar width and behavior unchanged.
- Keywords for later tasks (exact tokens): CSS classes `max-w-none`,
  `w-full`, `flex-1`, `min-w-0`; HTML tags `main`, `aside`; selector
  `div[role=main]`.

## 5. Shared components (build first)

- `web/src/components/WorktreeDetail.tsx`: view + edit modes via `mode`
  prop; props `{ entry, mode, onSave, onClose }`; used by Dashboard table
  rows (inline expand or modal) and Links list.
- `web/src/components/SearchableSelect.tsx`: filterable dropdown; props
  `{ value, options, onChange, placeholder }`; keyboard navigable;
  falls back to plain `Input` when options cannot be enumerated.
- Modal shell: reuse existing `useConfirm` pattern
  (`web/src/lib/confirm.tsx`); no new modal library.

## 6. Page changes

- `Links.tsx`: `TrackerMappingsCard`, `LinkSetCard`, `LinkRemoveCard`,
  `RegisterCard` → each becomes a button opening a modal with the same
  fields; submit via existing `useCreateRun`; `toast` + invalidate
  `queryKeys` on success.
- `Dashboard.tsx`: session table rows render `WorktreeDetail` in view mode;
  Sync/Review row buttons `navigate('/launch?mode=sync&ref=<key>')` (resp.
  `mode=review`).
- `Launch.tsx`: read `useSearchParams` → prefill `ref` + `mode`; add
  `noHarness` checkbox → appends `--no-harness`/`-N` to `start` argv
  (backend `command_argv` must accept and forward it); repo/harness/base
  inputs → `SearchableSelect` (repos from `useRepos`; base branches from
  new endpoint or free text).
- Unknown prefill `ref` → blank form + warning toast, never crash.

## 7. TUI picker (`src/harness/pick.py`)

- On each redraw, erase the full option block (all N lines) before drawing,
  and clear-then-draw on navigation — same UX as `git-wt cleanup`'s
  cleared-screen selection — so no leading-space padding accumulates.
- Keep behavior: non-TTY → `None`; empty options → `None`; q/Esc aborts.
- Regression test in `tests/test_pick.py`: simulate down/up cycles via the
  `read`/`stream` seams; assert output lines have no leading-space growth.

## 8. Verification

- `cd web && npm run build` (tsc) + eslint clean.
- Manual: open each Links modal and submit; Dashboard → Launch prefill
  round-trip for sync and review; dropdown filters options.
- `uv run pytest tests/test_pick.py -v` green; full suite green.
