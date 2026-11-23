# 05 — Dependency audit: Agent Orchestrator (AO) vs workagent web

Compared `frontend/package.json` in AO (`faa1f180`, dev) with `web/package.json` in workagent (this branch, 2026-11-23). Scope is **UI/UX-relevant** packages only. Electron, Sentry, PostHog, WorkOS, SQLite, Playwright, codemirror/lexical, pdf, mermaid, katex, xterm, i18n and update tooling are product features of a desktop app, not design language, and are excluded.

## 1. Is the request valid?

**Yes, with one qualification.**

- **Valid:** workagent and AO solve the same problem (supervising many parallel agent runs) for the same reader (an engineer scanning state all day). Both already share the same stack: Tailwind v4, shadcn/ui on Radix, cva, `clsx` + `tailwind-merge`, Lucide, TanStack Query, `react-markdown` + `remark-gfm`, `tw-animate-css`. AO's calm feel is mostly rules (see `04-minimalism.md`), not libraries, so adopting it is cheap and low-risk. The gap before this work was workagent's heavier look (bordered pills, 44px icons, boxed selects), not missing technology.
- **Qualification:** copy the *rules and tokens*, not the *identity*. AO's name, logo, mascot and brand blue are off-limits (Apache-2.0 grants no trademark rights). And do not copy AO's *desktop* mechanics (titlebar, traffic lights, native font switch, resize grips) into a web app. Minimalism also has a cost: hiding advanced fields behind progressive disclosure must not hide truthful state or destructive actions (AO's own top priority).
- **Also note:** AO says it is mid-migration and some of its screens are "legacy debt". Only AO's approved reference surfaces are a standard.

## 2. Packages AO has that workagent lacks

| AO package | Purpose in AO | Impact on design language | Recommendation for workagent |
| --- | --- | --- | --- |
| `@fontsource-variable/geist`, `geist-mono` | Bundled UI and code fonts | Gives AO its crisp, dense-text look. Small effect compared with spacing and borders | **Optional.** Keep Inter/JetBrains Mono (decision logged). Switching is a two-line change; do it only if you want pixel parity |
| `cmdk` | Command palette (Cmd+K) with keyboard selection | Part of the "fast, keyboard-first" feel; also the home of AO's glass surface | **Not needed.** workagent already has its own `CommandPalette.tsx`; adopt `cmdk` only if the custom one proves hard to maintain |
| `motion` | Spring and cross-fade animation (shell, multi-step flows, "quiet reveal") | Smooth height springs in the Add Project flow; settle transitions | **Skip for now.** CSS transitions cover the `04` rules (120-150ms, transform/opacity). ~50KB cost; adopt only if you build multi-step flows needing height springs |
| `radix-ui` (monolith) | One package re-exporting all Radix primitives (ScrollArea, RadioGroup, ...) | None directly; it is packaging | **Not needed.** workagent uses the per-primitive `@radix-ui/react-*` packages. Mixing both is worse |
| `@radix-ui/react-accordion`, `react-tabs` | Disclosure and tabs primitives | Progressive disclosure (rule 4) and tab semantics (`role=tab`, `aria-selected`) | **Add when needed**: accordion for the "More options" fold in the picker; tabs only if hand-rolled tabs (`RepoTabs`, `TabbedPage`) lack ARIA. Verify those first |
| `react-resizable-panels` | Sidebar and inspector resizing | Shell layout flexibility (hover-only 2px grip) | **Skip.** workagent has no sidebar/inspector shell; documented as "skip" in `03` |
| `@dnd-kit/*` | Drag-and-drop kanban cards, queued messages | Board interaction | **Skip.** workagent's `WorktreeBoard` has no drag requirement |
| `@mynaui/icons-react` | AO's app-wide icon set (alongside Lucide) | Slightly different icon style | **Skip.** One icon set only (rule 8 of `02`); keep Lucide |
| `@react-symbols/icons` | File-type icons in the file tree | File tree only | **Skip** |
| `zustand` | Client state (theme toggle, UI state) | None | **Skip.** React context and TanStack Query already cover it |
| `react-i18next` / `i18next` | All strings go through a translator | Copy discipline (no concatenated sentences) | **Skip.** Not a design need; keep the copy rules |
| `@tanstack/react-router`, `@tanstack/react-virtual` | Routing, list virtualization | None | **Skip.** `react-router-dom` stays. Add `react-virtual` only if the status or runs tables grow past a few thousand rows |
| `github-markdown-css`, `rehype-*` (slug, autolink, github-alerts, sanitize, raw), `katex`, `mermaid`, `dompurify` | Rich markdown in chat/PR bodies | Content rendering, not chrome | **Skip** unless run transcripts need GitHub-style markdown. If markdown containing raw HTML is ever rendered, add `rehype-sanitize` |
| `@radix-ui/react-tooltip` | Tooltip (AO wraps it in `ui/tooltip.tsx`) | One-phrase tooltips for icon-only controls | **Already added** in this PR |

## 3. Pieces AO has that are not packages

These carry most of the "feel" and need no dependency: the `PILL` hover (done in `button.tsx`), status glyph slot (done), semantic status tokens (done), `Pressable` (done), divided-list pattern (done in `QuestionCard`), `contrast-color()` label picking and bevel (CSS only, not adopted yet), and the `Kbd` and `Skeleton` primitives (`skeleton.tsx` exists).

## 4. Dependencies workagent no longer needs after the design language

**None are removable today.** Verified by import counts in `web/src`:

| Package | Used by | Verdict |
| --- | --- | --- |
| `sonner` | 42 files via `toast` | Keep. AO also avoids heavy alert banners; toasts fit rule 12 |
| `@radix-ui/react-checkbox` | `ui/checkbox.tsx` (4 importers) | Keep |
| `@radix-ui/react-select` | `ui/select.tsx` (3 importers) | Keep. Most forms use native `<select>` (restyled); if the planned picker replaces them, re-check, then this may become removable |
| `@radix-ui/react-popover` | `ui/popover.tsx` (6 importers) | Keep. The harness/model/effort picker will build on it |
| `@radix-ui/react-alert-dialog`, `react-dialog`, `react-label` | confirm, drawers, forms | Keep |
| `class-variance-authority`, `clsx`, `tailwind-merge` | button, badge, `cn()` | Keep (AO uses the same trio) |
| `tw-animate-css` | `index.css` | Keep (AO imports it too) |
| `react-markdown`, `remark-gfm` | run transcripts, plan viewer | Keep |
| `lucide-react` | 61 files | Keep. AO is on `^1.17`, workagent on `0.525`: icons used (`LoaderCircle`, `GitMerge`, ...) exist in both; upgrade is optional |

**Code-level, not package-level, cleanups unlocked by the redesign:** `components/ui/badge.tsx` is now unused by status display but still has 19 importers; unused `ui/alert.tsx` has been deleted and `FieldHelp`/`CheckRow` have been migrated to `Tooltip`. Follow-up work is tracked in [#181](https://github.com/ynsr/workagent/issues/181) (combined harness/model/effort picker) and [#182](https://github.com/ynsr/workagent/issues/182) (`cmdk`, `motion`, `react-accordion`, `react-tabs`, `dnd-kit`, `@mynaui/icons-react` migration).

## 5. Version drift worth knowing (not required)

AO runs React 19, Vite 8, Vitest 4, Lucide 1.x, Tailwind 4.3; workagent runs React 18, Vite 7, Vitest 3, Lucide 0.525, Tailwind 4.1. Nothing in the design rules needs the newer versions. Upgrading React for design reasons would be unjustified.

## 6. Bottom line

- To reach AO's feel you need **no new libraries beyond the tooltip already added**. The rest is token, variant and copy discipline.
- Add `@radix-ui/react-accordion` when you build progressive-disclosure sections; consider Geist only for exact parity.
- Do not add `motion`, `zustand`, `cmdk`, `dnd-kit`, `radix-ui` monolith, or a second icon set unless intentionally scoped — note: [#182](https://github.com/ynsr/workagent/issues/182) tracks a user-requested follow-up to adopt `cmdk`, `motion`, `react-accordion`, `react-tabs`, `dnd-kit`, and `@mynaui/icons-react`.
- No existing dependency becomes obsolete; revisit `react-select` after the picker lands.
