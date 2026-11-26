---
name: workagent-design-system
description: "Use for any workagent webapp UI, layout, theme, component, motion, or copy change. Read before proposing or implementing visual work. Derived from Agent Orchestrator's design language; start here, then follow the links."
---

# Design system — workagent

workagent is a place to **delegate work to coding agents and supervise it**: issues, worktrees, MRs/PRs, reviews, releases, production tests. Its UI borrows the design language of Orchestrator.inc's *Agent Orchestrator* (AO), which solves the same problem: keeping many parallel agent sessions legible.

This is a product UI, not a marketing site. It should feel calm under all-day use, reward scanning, and make "what is running, what needs me, what is safe to ship" obvious in seconds.

## How these files fit together

| File | Layer | Read it when |
| --- | --- | --- |
| [design/01-foundations.md](design/01-foundations.md) | **Global / public** — stack and conventions shared with the wider ecosystem (shadcn/ui, Radix, Tailwind, Lucide, variable fonts, OKLCH, 4px grid, WCAG) | You need the "boring correct" baseline, or want to swap framework |
| [design/02-ao-signature.md](design/02-ao-signature.md) | **AO-specific** — the opinions that make AO feel like AO | You are deciding how something should look/behave |
| [design/03-workagent-application.md](design/03-workagent-application.md) | **workagent** — what to adopt, adapt, or skip; state→visual mapping | You are touching StatusTable, the action buttons, Ask-tool questions, notifications |
| [design/04-minimalism.md](design/04-minimalism.md) | **Minimalism** — content and interaction rules (what to show, hide, and say) behind AO's calm feel | You are adding a field, a form or a screen |
| [design/05-dependency-audit.md](design/05-dependency-audit.md) | **Dependencies** — AO packages workagent lacks, purpose/impact, and what is not needed | You are deciding whether to add or remove a UI library |
| [design/tokens.css](design/tokens.css) | **Machine-readable** — drop-in CSS variables (dark + light) and a few primitives | You are writing CSS or a component |

**Precedence when rules conflict:** `04` applies alongside `02`; `03` (workagent decisions) > `02` (AO signature) > `01` (global baseline). A workagent decision may only override an AO rule with a one-line entry in the decisions log below.

## One-paragraph summary

A **dark-first, near-neutral, token-led precision instrument**. Charcoal surfaces (never pure black), 1px translucent hairlines used sparingly, modest radius, compact density (13–14px text, 28–32px controls), **color reserved for meaning** (status, focus, the one primary action), sentence case everywhere, no decoration, and motion that is functional, short (120–150ms) and only on `transform`/`opacity`. Status is shown with a fixed glyph slot, never a rainbow of badges. Actions appear on hover/focus without moving layout. Truthful state beats polish.

## Non-negotiable hierarchy

When requirements compete, keep them in this order:

1. Truthful operational state and safe actions.
2. The user's active task and next useful action.
3. Context for the active item (logs, diff, MR/PR, review, agent question).
4. Fast scanning across many items.
5. A coherent visual system and refined motion.

Never use polish to obscure a state change, a destructive action, a failure, or uncertainty.

## Provenance and licensing

- **Source:** `OrchestratorInc/agent-orchestrator`, branch `dev`, commit `faa1f18019176165a3119ff190edd4de7fc72bac` (verified 2026-11-23; the earlier draft cited a hash that does not exist in the repo). The repo ships nightlies; re-diff before trusting this after a few weeks:
  ```sh
  git diff faa1f180..origin/dev -- DESIGN.md frontend/src/styles/tokens.css \
    frontend/src/renderer/components/ui/button.tsx frontend/src/renderer/components/ui/button.css
  ```
- **What was read:** AO's own `DESIGN.md`, `frontend/src/styles/tokens.css`, `ui/button.tsx` + `button.css`, the shimmer/glass utilities in `renderer/styles.css`. **Not** run or screenshotted. Visual feel that is not in code or its docs is interpretation, and is marked as such.
- **Code license:** Apache-2.0. Design *tokens and patterns* are fine to reuse; this directory paraphrases them with attribution.
- **Do not copy:** AO's name, logo, the "Ruto" mascot, app icon, or any `assets/brand` files. Apache-2.0 §6 grants no trademark rights. Fonts (Geist), icons (Lucide) and primitives (shadcn/ui, Radix) have their own permissive licenses, so check them when you bundle.
- AO itself states it is mid-migration. Only rules from its *approved reference surfaces* were extracted; legacy surfaces were ignored.

## Design QA checklist (before calling a UI change done)

1. Does the reader see the active task and the next useful action before secondary metadata?
2. Is every color semantic and every status truthful?
3. Did we reuse existing tokens and primitives (no one-off hex, radius, spacing)?
4. Is every word sentence case, necessary, and non-duplicated?
5. Did we start with no borders/shadows and add back only structural ones?
6. Are peer items a shared list with dividers rather than spaced, individually outlined cards?
7. Does focus work without hover? Is reduced motion coherent? Do icon-only buttons have a label and tooltip?
8. Does it hold in dark and light, narrow width, long branch/issue names, and loading/error/empty states?
9. Does anything move on hover, or shift when data refreshes? (It must not.)

**Review outcomes:** *Blocker* (misleading state, inaccessible/unsafe action, new one-off primitive) · *Must fix* (explicit rule broken: heavy borders, gradient, permanent shadow, ALL CAPS, duplicated copy) · *Follow-up* (legacy surface to migrate later).

## Where this lives

`web/DESIGN.md` (this file) and `web/design/` sit next to the frontend they govern, beside `web/API_CONTRACT.md` and `web/FEATURE_INVENTORY.md`. `web/design/tokens.css` is a **reference** and is not imported by the app; the live theme is `web/src/index.css`. Migration steps are in [design/03-workagent-application.md](design/03-workagent-application.md).

## Decisions log (workagent)

| Date | Decision | Rationale |
| --- | --- | --- |
| 2026-11-23 | Adopt AO's design language as the base; keep its token names for diffability | Same problem domain (supervising parallel agent work); renaming would make upstream diffs unreadable |
| 2026-11-23 | Brand accent is **not** adopted | AO's blue is Orchestrator branding; workagent uses the neutral `primary` as its accent and needs its own brand color if it wants one |
| 2026-11-23 | `tokens.css` is a reference, not imported; the live theme stays in `web/src/index.css` until migration | workagent already ships its own shadcn palette (indigo slate), `.dark` class toggling and Inter/JetBrains Mono; swapping wholesale is a separate, reviewable change |
| 2026-11-23 | Docs live in `web/DESIGN.md` + `web/design/` | Colocated with frontend contract docs |
| 2026-11-23 | Keep the indigo-slate palette, Inter/JetBrains Mono, light default with `system` option | Reversible and low-risk; palette swap is cosmetic and can follow separately. Hue is still limited to status and focus per `04` |
| 2026-11-23 | Added `quiet`, `destructive-ghost`, `accent`, `xs` button variants/sizes; `icon-sm` is now 28px; ghost uses the growing pill; `Tooltip` (new dependency `@radix-ui/react-tooltip`) and `Pressable` added | Parity checklist step 3; 44px row icons and native `title` tooltips replaced |
| 2026-11-23 | Status badges replaced by `StatusGlyph`/`StatusLabel` with `--status-*` tokens | One glyph, no tinted pills, no raw Tailwind palette colors |
