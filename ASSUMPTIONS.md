# Offline-work assumptions (review on return)

User offline; instructed: no questions, log assumptions here, push after each issue.
Repo `/home/bs/projects/personal/harness`, branch `main`.

## Global
- A1: Sequential implementation by me, not parallel agents — all three issues touch
  `web/src/components/StatusTable.tsx` + dashboard pages; parallel edits would conflict.
- A2: No read-only pre-push reviews (user explicitly delegated; reviews resume on return).
- A3: Web-only changes still get full `pytest` + `vite build` per issue before push.
- A4: Assumption entries appended per issue; this file is scratch (not CHANGELOG).

## Issue #17 — Web App Dashboard Improvements
Sub-items:
1. Absolute PR/MR URL link — assume: make the existing PR/MR cell label a hyperlink
   to the absolute URL (target=_blank, rel=noreferrer). No new column.
2. PR/MR summary cached 3 days — assume: `pr_detail.title` already cached via
   `pr_cache`, only TTL may need extension to 3d; reuse existing cache entry, no new table.
   If backend stores no summary, add `fetch_pr_info` best-effort title into same cache entry.
3. Button to list worktree sessions in Sessions page — assume: per-row link from
   dashboard worktree key → `/sessions?worktree=<ref>` (or Sessions page filter);
   no new backend route, filter client-side on existing session list data.
4. Most-expensive remote calls + TTLs under dashboard — assume: static informational
   panel (hardcoded call list + TTL constants from `cli.py`/`trackers.py`), not live
   instrumentation. Live timing would need backend timing plumbing — out of scope.
5. Hide Actions by default, hover-reveal pushed right — assume: CSS-only change in
   `StatusTable` RowActions (opacity-0 group-hover:opacity-100, focus-visible fallback
   for keyboard/touch). Keep icon buttons as-is, no redesign to dropdown menu.
- #17.2 as built: `pr_detail.title` already served; kept 3h/30min TTLs (issue asked 3d — your call).
- #17.4 cost panel is static text from code constants, not measured timings.
- #17.5 hover-hide only on `(hover:hover)`; touch/keyboard always see actions.
- Sessions filter is exact `worktree_ref === key` via `?worktree=`.

## Issue #18 — done as implemented
- Title: branch after first `/`, first 50 chars, `-`→space, Title Case; full ref in `title=` tooltip.
- Worktree filter is a searchable dropdown of known `worktree_ref`s (+ free `?q=` text filter); exact-match on select.
- Kind column maps initiator_command start/review/sync → Start (task)/Review/Sync; others raw.
