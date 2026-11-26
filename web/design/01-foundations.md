# 01 — Global foundations (the public layer)

These are conventions AO **inherits from the wider ecosystem** rather than invents. They are the safe baseline: if you swap React for Angular or drop Tailwind, keep these. Everything AO-specific is in [02-ao-signature.md](02-ao-signature.md).

## 1. Stack AO sits on

| Concern | What AO uses | Why it matters for you |
| --- | --- | --- |
| Components | **shadcn/ui** pattern on **Radix** primitives (`@radix-ui/react-slot`, dialog, dropdown, tooltip, select) | Accessible behavior (focus trap, roving focus, ARIA) comes free. You own the code. |
| Styling | **Tailwind CSS v4** with a CSS-variable theme (`@theme` bridge, `@utility`) | Tokens live in CSS variables, so the theme is framework-independent. |
| Variants | **class-variance-authority** (`cva`) | Variants are named by *meaning* (`primary`, `destructive`), not appearance. |
| Icons | **Lucide** | One stroke style, 14–16px default. |
| Fonts | **Geist** + **Geist Mono** (variable, via `@fontsource-variable`) | Legible at dense sizes. System faces on macOS. |
| Color space | **OKLCH** with `color-mix(in oklch, …)` | Perceptually even lightness; hover/active states are *computed* from the foreground, not hand-picked. |
| Theming | `data-theme="light"` on `:root`; dark is the default (AO also aliases `:root.dark`/`.dark`) | Theme switch is one attribute; named palettes layer on top. workagent uses the shadcn `.dark` class instead. |

workagent already uses this stack (React 18, Tailwind v4, shadcn/ui, Radix, cva, Lucide, OKLCH); its fonts differ (Inter + JetBrains Mono). If a consumer is not React/Tailwind, the portable parts are: CSS variables (`design/tokens.css`), Radix-style behavior contracts (focus, ARIA, keyboard), and `cva`-style "variant = meaning".

## 2. The shadcn token vocabulary

AO's base roles are the standard shadcn set. Use these names; they are widely understood and tool-friendly:

`--background`, `--foreground`, `--card`, `--popover`, `--primary`, `--secondary`, `--muted`, `--muted-foreground`, `--accent`, `--destructive`, `--border`, `--input`, `--ring`, `--radius`, plus `--sidebar-*`.

AO then adds a **semantic layer** (`--color-bg-primary`, `--color-text-muted`, `--color-status-*`, `--color-interactive-hover`…) that *points at* those roles. Components consume the semantic layer; themes only change the base roles.

## 3. Global conventions applied

- **4px spacing grid.** Dense rhythm 4/6/8/10/12/14/16/18; 24/32/40 only for real section breaks.
- **Hit targets.** Compact icon control 20–24px, standard control 28–32px, row ≥32px (36px for high-frequency/imprecise targets).
- **Radius scale derived from one variable** (`--radius` × 0.4/0.6/0.8/1/1.4) so nested corners stay coherent.
- **Nested-radius rule.** Inner radius = outer radius − inset (AO: window 18px − 6px inset = 12px panel).
- **Elevation only for transient layers** (menus, popovers, dialogs, tooltips). Permanent content gets no shadow.
- **Focus is visible.** `:focus-visible` ring (2px, offset 2px), never removed for aesthetics. Use `:focus-visible`, not `:focus-within`, so mouse clicks don't leave sticky highlights.
- **Color is never the only signal.** Pair with shape, icon, label or position (WCAG 1.4.1).
- **Contrast.** Test real token pairs in both themes (WCAG AA 4.5:1 for small text). AO adds a *separate lighter accent shade for small text* because its button fill is too dark for it. The same trick applies to any brand color.
- **Reduced motion.** `prefers-reduced-motion` removes nonessential transforms and makes transitions immediate.
- **Compositor-only animation.** Looping animations touch only `transform`/`opacity`. Anything else repaints every frame on the main thread and delays typing.
- **Lifecycle states are first-class.** Design arrive / idle / begin / active / finish / fail / interrupt, not only the happy-path screenshot.
- **i18n-ready copy.** Strings go through a translator; no concatenated sentences.
- **Tabular figures** for timestamps, counts, diffs, anything aligned in columns (`font-variant-numeric: tabular-nums`).

## 4. Global "good product UI" conventions AO also follows

- Labels precede inputs; placeholder is an example, never the label.
- Validation sits next to the field with words *and* semantic color.
- Dialogs interrupt only for real decisions and restore focus to the trigger on close.
- Menus: single elevated surface, consistent item height, dividers between meaningful groups only, close after a single-choice action.
- Icon-only controls get an accessible name and, when not obvious, a short tooltip.
- Empty/loading/error/permission states are explicit and local; no full-page skeleton for a small update.
- Destructive actions use destructive color and confirm when hard to reverse.

## 5. What this layer is *not*

It does not say what the product feels like. Two apps can satisfy every line above and look nothing alike. The feel is in [02-ao-signature.md](02-ao-signature.md).
