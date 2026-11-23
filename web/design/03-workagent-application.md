# 03 — Applying the language to workagent

Everything marked **[proposal]** is a mapping from AO's rules onto workagent's concepts, not something AO states. Facts in section 0 were checked against `web/` at this commit (2026-11-23); verify again before large migrations.

## 0. Current state of workagent's web UI (audited)

| Area | Today (`web/`) | Gap vs AO language |
| --- | --- | --- |
| Stack | React 18, Vite, Tailwind v4 (`@theme inline`), shadcn/ui on Radix, cva, Lucide, sonner, TanStack Query | None: same stack as AO. Tokens map 1:1 onto shadcn names |
| Theme | Custom "indigo slate" OKLCH palette in `src/index.css`; light default, `.dark` class set by `lib/theme.tsx` (light/dark/system) | AO is graphite near-neutral, dark-first, `data-theme="light"`. Indigo `--primary` is a brand-like accent that AO reserves for status/focus |
| Fonts | Inter Variable + JetBrains Mono Variable | AO: Geist / Geist Mono. Keep current fonts unless the team chooses to switch; the type scale applies either way |
| Radius | `--radius: 0.65rem` | AO `0.625rem`; negligible |
| Button | stock shadcn (`default/destructive/outline/secondary/ghost/link`, h-9/h-8/h-10) | No `quiet`/`destructive-ghost`/`accent`, no `xs` 24px, no hover pill; controls are 32-40px, AO is 24-32px |
| Status | `components/StateBadge.tsx`: outlined, tinted pill badges (`border-sky-500/40 bg-sky-500/10`...) with raw Tailwind palette colors | Violates "one glyph in a fixed slot" and "semantic tokens, no one-off colors"; no `--color-status-*` |
| Row actions | `ActionIcon` in `StatusActions.tsx`: ghost `size-11` (44px) icon buttons, `title` tooltip | 44px targets are far larger than AO's 28px; native `title` instead of a Tooltip primitive |
| Ask-tool | `QuestionCard.tsx`: option buttons in `flex-wrap` pills + free text | AO: options as a shared divided list, "Type your own answer" last row |
| Confirm | `lib/confirm.tsx`: bordered amber warning box | AO: errors/warnings close to the action, no permanent outlined boxes |
| Chips | `rounded-full border` chips in `StatusTableMain.tsx` | AO: pills only for avatars, dots, intentional chips |
| Casing | `uppercase` used in `CommandPalette`, `ProdtestPlanViewer`, `WorktreeFeedbackTimeline` | AO bans ALL CAPS labels |
| Raw buttons | about 80 `<button` occurrences in `src/**/*.tsx` (includes tests and `components/ui/`) | AO: only `Button`/`Pressable` outside `ui/`; count non-test, non-`ui/` hits before enforcing a lint rule |
| Hard-coded colors | `lib/syntaxHighlight.tsx` (violet/emerald/amber/sky/orange) | Acceptable for code highlighting; document as an exception |
| Tooltip/Dialog | Radix Dialog/AlertDialog/Popover present; no `Tooltip`, `Pressable`, `CloseButton` | Add the missing primitives |
| Motion | Reduced-motion rule already in `index.css`; 150ms ease-out transitions in places | Aligned |

## 1. Adopt / adapt / skip

| AO element | Verdict | Note |
| --- | --- | --- |
| Tokens, OKLCH surfaces, hairlines, radius, spacing, type scale | **Adopt** | Drop in `tokens.css` |
| Variant-by-meaning `Button` + `Pressable`, no raw `<button>` | **Adopt** | Enforce with a lint rule |
| Growing hover pill, bevelled filled buttons | **Adopt** | Plain-CSS version in `tokens.css` |
| Shared lists with dividers, borderless tiles | **Adopt** | StatusTable rows are exactly this case |
| Status as a glyph in a fixed slot | **Adopt + adapt** | State mapping below |
| Sentence case, no helper-text duplication, user-job naming | **Adopt** | Also applies to CLI output and Telegram/`mm` text |
| Motion rules (≤150ms, transform/opacity, reduced motion) | **Adopt** | |
| "Worked for <time>" | **Adapt** | "Ran for <time>" on each completed action run |
| Board with lanes (Working / Needs you / In review / Ready to merge) | **Optional** | A *group-by* view over the same data; don't replace StatusTable |
| Brand blue `#338ad3`, logo, mascot, app icon | **Skip** | Trademark/branding; pick workagent's own accent if wanted |
| macOS titlebar/traffic lights, window chrome, native fonts switch | **Skip** | Desktop-only; web keeps its current fonts (Inter, JetBrains Mono) or Geist |
| Resize grips, glass overlay, Cmd+K | **Skip for now** | Add Cmd+K later; it's the cheapest high-value import |
| Update row, quota UI, terminal ANSI palette | **Skip** | Unless you embed live logs; then copy the ANSI palette separately |

## 2. State → visual mapping  [proposal]

One fixed status slot per worktree/issue row. Precedence (first match wins), following AO's glyph rule:

| # | workagent fact | Glyph (shape) | Token | Label (tooltip / detail) |
| --- | --- | --- | --- | --- |
| 1 | Agent **Ask-tool question pending** | Red alert icon | `--color-status-needs-you` | "Needs your answer" |
| 2 | A run is active (Start, Review, Fix comments, Generate/Run prod plan) | Spinner | `--color-status-working` | "Working" + action name |
| 3 | Review/CI/check pass in progress on the MR/PR | PR glyph | `--color-status-validating` | "Validating" |
| 4 | MR/PR open, awaiting human review or changes requested | PR glyph (orange alert if a person is next) | `--color-status-in-review` | "In review" / "Changes requested" |
| 5 | MR/PR approved and mergeable | PR glyph | `--color-status-ready` | "Ready to merge" |
| 6 | Merged / released | Merge glyph, dimmed | `--color-status-merged` | "Merged" / "Released" |
| 7 | Run failed, CI failing, prod-test failure | Red alert icon | `--color-status-exited` | Reason in tooltip |
| 8 | Nothing happening | Hollow ring (seen) / filled dot (changed since last viewed) | muted | — |

Rules carried over from AO:
- **One glyph per row.** No stacked chips like "In review · CI failing · 2 comments".
- **Shapes differ**, so color never carries meaning alone.
- **Spend color on Working and Needs you**; unread stays grey.
- **Uncertainty is a state.** If a run's liveness can't be confirmed (process gone, session file stale), show a neutral "Unknown" glyph, not "Failed" or "Done".
- Prod-test severity ≥ high is shown by glyph shape + the word in the detail, not by a louder color.

## 3. StatusTable  [proposal]

- **One shared surface** with hairline row dividers; no per-row border, no card gaps.
- Row height 32–36px, 13px label text, branch/path in mono, muted; timestamps/counts tabular.
- Title gets the weight; repo/issue key and branch are secondary; **one** derived status line at most.
- **Row actions live in a reserved trailing slot**, revealed on hover **and** `:focus-visible`/`:focus-within`, fade only (`opacity`, 120ms). The row's measured width never changes and the slot never covers the title or status glyph. *Check the current `DedicatedOverlay`/`ActionIcon` group in `StatusActions.tsx`: an overlay that paints over row content on hover is the "moving controls on hover" anti-pattern.*
- Buttons: `quiet` + `icon-sm` (28px) with a one-phrase tooltip; keyboard reachable.
- Clicking the row opens the detail; clicking an action must not also navigate (stop propagation).

### Which actions appear  [proposal, from the workflow]

Show only **valid next actions** for the item's state; if one is expected but unavailable, show it disabled with the reason in its tooltip.

| Action | Valid on | Variant | Confirm? |
| --- | --- | --- | --- |
| Start | Issues only | `quiet` (row) / `primary` (detail, if it's *the* next step) | No |
| Generate prod test plan | After Start, before Review | `quiet` | No |
| Review | Open MR/PR | `quiet` | No |
| Fix comments | MR/PR with unresolved comments | `quiet` | No |
| Cleanup | Merged | `destructive-ghost` | **Yes**: deletes remote + local branch/worktree |
| Release | Develop ready to promote | `secondary` in row; `primary` in detail | **Yes**: triggers release CI on master |
| Run prod test plan | After release / prod deploy | `quiet` | No |

**One primary per focused region.** In the detail view, the single most likely next action is `primary`; all others are `secondary`/`ghost`. Rows never contain a filled button.

## 4. Ask-tool questions (agent needs you)  [proposal]

The "Needs you" state is AO's most important signal, and the Ask-tool is its direct analogue.

- The row shows the red alert glyph; the detail shows the question in the main surface, not in a toast.
- **Options are a shared list**, one elevated surface, consistent row height, dividers between rows, a clear selected/focus state, with *"Type your own answer"* as the last row expanding to an input. Never a stack of bordered option cards.
- Primary action: **Send answer** (`primary`), disabled with a reason until an option/text exists.
- **Preserve user work on interruption.** A typed draft survives navigation, refresh and a dropped connection.
- **Truthful state:** if the underlying run can't actually receive an inline answer (e.g. print-mode `omp -p` cannot pause for it, or the run has ended), say so in words and show what *will* happen (e.g. "Answering resumes the session"). Don't render an interactive control that can't deliver. This depends on the open print-mode vs ACP decision.
- After answering: the row returns to Working; the answered question collapses into the run's fold.

## 5. Runs and results

- Each completed run opens with **"Ran for <time>"** (fold with chevron if it has steps). The time is not repeated elsewhere.
- Live label fades once when the step changes; text updates in place; nothing in view jumps when a run settles.
- Prod-test results: a divided list, one row per check, status glyph + title + one detail line. **Extract and show the key log facts**, because logs expire (3-month retention) and the UI is the record.
- Failure copy sits next to the failed action with the next step; reserve a full-width alert only for a blocking, whole-flow failure.

## 6. Notifications (Mattermost, Telegram, CLI)  [proposal]

AO's visual rules don't apply to text, but the *voice* does: sentence case; state first, then object, then link; one line where possible; no helper prose restating the obvious; no percentages. Whether to use emoji in chat messages is a judgment call outside AO's rules (AO bans emoji only in its working UI). If used, keep one per message, as a status marker.

## 7. Adoption plan (smallest safe steps)

1. Port `design/tokens.css` values into `src/index.css` (keep the shadcn variable names and the `.dark` class; do not import the reference file as-is). Decide dark-first vs. current light default.
2. Keep Inter + JetBrains Mono, or self-host Geist + Geist Mono (OFL); set `font-variant-numeric: tabular-nums` where columns align.
3. Extend the existing shadcn `Button` with `quiet`, `destructive-ghost`, `accent` and `xs`; add `Pressable`, `StatusGlyph`, `Tooltip`, `CloseButton` (Dialog/Select/Popover already exist).
4. Migrate **StatusTable** first (replace `StateBadge` pills with a status glyph, shrink `ActionIcon` to 28px), then `QuestionCard`, then run/result views.
5. Add guardrails: lint/grep for raw `<button>`, raw hex/`rgb()`, `uppercase` class, arbitrary `rounded-[…]`.
6. Run the QA checklist in `DESIGN.md` in dark + light, narrow width, long branch names, loading/error/empty.

## 8. Open decisions

- Whether workagent adopts AO's neutral graphite palette or keeps its indigo-slate brand (the stack makes either cheap).
- Dark-first (AO) versus the current light default with `system` option.
- Font: keep Inter/JetBrains Mono or move to Geist.
- Whether the Kanban-style grouped view is wanted (`WorktreeBoard.tsx` exists already; compare it with the AO lane rules before extending).
- Whether Ask-tool answers can be delivered inline for every run type (ACP runs: yes, via `QuestionCard`; print-mode runs: no).
