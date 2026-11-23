# 04 — Minimalism rules (content and interaction)

You asked for "the minimalistic feel" of Agent Orchestrator (AO), as in its agent/model/effort picker. This is a **different layer from visual style**. Colors and fonts alone will not produce it. It comes from *how much the screen asks of you*: interaction design plus content design. These rules are what to follow when adding a control or a screen. Each rule names its source: **[AO doc]** is stated in AO's `DESIGN.md`; **[observed]** comes from the screenshots; **[proposal]** is my extension.

## What the screenshots show

| | AO picker | workagent form (before) |
| --- | --- | --- |
| Controls | **One** popover: search, harness icons, model list, one "Effort" row | Three separate controls: harness segmented buttons, Model select, Effort select |
| Labels | None on the trigger; the selected value *is* the label | A label plus a `?` icon on every field, plus a helper sentence ("AI harness used for this run (defaults to Settings).") |
| Defaults | "Default" is a value you can ignore; effort is collapsed to one row | "Default (agy)" is an extra segment you must read |
| Surfaces | One elevated surface, dividers only, no per-option boxes | Boxed native selects, a bordered segmented bar |
| Choice size | You only decide what you care about; everything else is preset | Every field is visible and equally loud |

## The rules

1. **Show the decision, hide the plumbing.** [observed][proposal] A control shows the current value, not a label plus a description. Defaults are not options to read; they are the preselected value. Advanced fields (effort, model) are one quiet row or a collapsed section.
2. **One control per decision.** [AO doc: "one decision at a time"] If harness, model and effort are one decision ("which agent runs this"), they are one picker, not three fields.
3. **No helper text that restates the control.** [AO doc] A label like "Harness" needs no sentence after it. Keep text only if it changes a decision, explains a failure, or gives a required next step. Move flag names and long explanations into a tooltip.
4. **Progressive disclosure.** [proposal] Show the common path; reveal the rest on demand (a chevron row, a "More options" fold). If most runs use defaults, defaults must cost zero clicks.
5. **Fewer words.** [AO doc] Sentence case, verb + object ("Send answer", "Run flow"), no trailing ellipsis, no "Please", no percentages.
6. **Structure from spacing, not boxes.** [AO doc] No border on every field. A filled secondary surface marks an input; dividers separate rows of one list. A card is only for an object with its own actions.
7. **Color means something.** [AO doc] Gray by default. Hue appears only for status (working, needs you, review, ready, merged, failed) and focus. A purple border on a segmented bar is decoration.
8. **Icons replace words only when unambiguous**, and always carry an accessible name and a one-phrase tooltip. [AO doc] Prefer the text if it fits.
9. **State is one glyph.** [AO doc] Not a bordered pill with a color tint and a word. Glyph shape differs per state; the label sits beside it in muted text.
10. **Nothing moves.** [AO doc] Hover actions appear in a reserved slot, no layout shift on refresh, no scale on press.
11. **Disabled needs a reason.** [AO doc] If a control cannot work, say why in its tooltip; do not hide it silently.
12. **Empty and error states are short and local.** [AO doc] One line next to the thing that failed, not a banner.

## Quick test

Before adding a field, ask: *Can the user succeed without ever seeing this?* If yes, collapse it. Count visible words on the form and remove any that repeat another word on screen.

## Applying it to the harness, model and effort picker  [proposal]

Target: one trigger showing `agy · Claude Sonnet 5.5 · Medium`; clicking opens a single elevated surface: harness icons on top, a search field, a divided model list, and one trailing `Effort  Default >` row. Implementation sketch: Radix Popover (already a dependency) plus the `Pressable` row, the divided-list pattern from `QuestionCard`, and the existing `useHarnessOptions` data. **Not built yet**: it replaces `HarnessSelect`, `ModelSelect` and `EffortSelect` in about 15 call sites and needs a separate review. The interim restyle (borderless secondary-surface selects, inline helper text moved to tooltips) already applies rules 3, 6 and 7.
