# 02 — AO signature (what makes it feel like Agent Orchestrator)

Opinions specific to AO. Each rule is tagged:
- **[doc]** stated in AO's `DESIGN.md`
- **[code]** observed in source (`tokens.css`, `button.tsx/css`, `styles.css`)
- **[interp]** my interpretation; not stated by AO

Layering: [01-foundations.md](01-foundations.md) is the shared baseline; this file is the personality on top.

## 1. Visual thesis

**Dark-first precision instrument.** [doc] Low-glare, near-neutral control surface; calm type; disciplined color; dense but breathable. "More a thoughtful desktop tool than a generic SaaS dashboard."

- **Decoration: minimal.** [doc] Hierarchy comes from type, alignment, spacing, surface tone, and only necessary 1px boundaries.
- **Material: layered charcoal, never pure black.** [doc][code] Base graphite is `oklch(0.21 0.002 250)`, a near-neutral with a trace of blue-gray. Cards step up ~4 lightness points, popovers ~9.
- **Color: rare, semantic, stateful.** [doc] A colored element must answer "what does this mean?"
- **Shape: modest radius, not a field of pills.** [doc] Pills only for avatars, dots, and intentional chips.
- **Density: compact by default.** [doc] 13–14px text, 28–32px controls, 36px topbar.
- **Personality: quiet competence.** [doc] No gradients, glass, glow, stock imagery, textures or emoji in the working UI (glass is allowed only on three transient surfaces).

## 2. The signature moves

### 2.1 Hairlines, not borders  [doc][code]
`--border` is white at **7% alpha** in dark (black 8% in light). Borders are *translucent overlays*, so they never read as opaque gray strokes on any surface. Rule: remove borders first, then add back only structural ones (shell panes, modal edge, adjacent rows in a shared list, lane boundaries, input hit area when contrast needs it).

### 2.2 Shared lists with dividers instead of cards  [doc]
Peer objects that are *chosen or scanned* (projects, settings sections, dropdown options, source choices) live in **one container with subtle dividers**, not spaced, individually bordered cards. Cards are allowed only when each object carries a distinct actionable payload (a board session card is the one named exception).

Settings groups: one card, **no outline**, filled background groups it, hairline dividers between items, rows fit content with **10px vertical padding**, 16px text inset.

### 2.3 The growing hover pill  [doc][code] — the single most recognizable interaction
Ghost-family controls (`ghost`, `quiet`, `destructive-ghost`) and nav rows do not change their own background on hover. A `::before` pill, **inset 4px and invisible at rest, grows to the element's edge** (`inset 4px → 0`, `opacity 0 → 1`, `--duration-normal` 150ms, `--ease-out`) on hover or `:focus-visible`.
- Entry animates; **exit is instant**.
- "On" states (`aria-pressed`, `data-state="open"`, `data-active="true"`) keep the pill full-size with the stronger fill.
- Host background stays transparent, so the pill is the only fill.
- Reduced motion: pill is simply at full inset, no transition.
- Never scale or shift a control on press. Pressing only deepens the fill.

### 2.4 Bevelled filled buttons  [doc][code]
Filled buttons are not flat: a **12% white inset highlight on top, 8% black inset shade on the bottom, a 13%-darker edge, and a barely-there 1% outer shadow**. Hover darkens the face by 7%; pressed/open by 14%. No glow, no lift, no movement. Label color is chosen automatically via `contrast-color()` with a 22% lighter bias, computed from the *resting* fill, so hover/press never flip the label.

### 2.5 Variant = meaning, not look  [doc][code]
One `Button` for every action; **a raw `<button>` is a lint-level mistake**. Rows, cards, tabs, disclosure toggles use a separate `Pressable`. Variants:

| Variant | Meaning |
| --- | --- |
| `primary` | The **one** main action in a region |
| `accent` | A decision the turn is *waiting on* (approve, open link) |
| `secondary` | Quiet filled surface |
| `ghost` | Transparent, foreground text, hover pill |
| `quiet` | Ghost that rests muted, brightens on hover; toolbar/row actions |
| `destructive` | Danger fill, irreversible |
| `destructive-ghost` | Remove/delete/kill; red-tinted pill |
| `link` | Inline text action |
| `outline` | Contrast fallback only |

Sizes: `xs` 24 · `sm` 28 · `default` 32 · `icon-xs/-sm/icon`. `className` may adjust layout but **never** a variant's color, height or hover.

### 2.5b Color roles: neutral primary, separate brand accent  [code]
`--color-accent` aliases `--primary`, which is **near-white (`oklch .90`) in dark and near-black in light**. The "accent" is neutral. Brand blue (`#338ad3`) is a *separate* role used for (a) the `accent` button variant and (b) as a lighter shade (`#66a4de` dark / `#2f6da4` light) for small text: links, inline code. Do not treat "accent = blue".

### 2.6 Status is a glyph in a fixed slot  [doc]
Prefer an icon/glyph to a prose badge, in **one fixed status slot**. Precedence:
1. Actively working → spinner in `--color-status-working`.
2. Else if a PR/MR is relevant → PR glyph tinted by its actionable state.
3. Else → a dot (amber/red = attention, muted = idle/complete).

Shapes differ so **color is never the only signal**: blue spinner (working) · red alert (waiting on you / CI failing / conflicts / changes requested) · orange alert (a person's review is next) · green PR icon (ready) · dimmed purple merge icon (merged) · **filled grey dot = changed since you last looked; hollow ring = seen**. Colour is spent on *working* and *needs you*; unread stays grey so a long list isn't loud.

Never show several competing status chips on one row/card.

### 2.7 Semantic status palette  [code]

| Meaning | Token | Dark | Light |
| --- | --- | --- | --- |
| Working | `--color-status-working` | `#60a5fa` | `#2563eb` |
| Needs you | `--color-status-needs-you` | `#f06445` | `#c2412d` |
| Validating | `--color-status-validating` | `#e58bb5` | `#9b4871` |
| In review / caution | `--color-status-in-review` | `#f59e0b` | `#d97706` |
| Ready / success | `--color-status-ready` | `#4ade80` | `#16a34a` |
| Merged | `--color-status-merged` | `#c084fc` | `#9333ea` |
| Failure | `--color-status-exited` → `--destructive` | `#fb4934` | `#9d0006` |
| Idle/unknown | muted tokens | — | — |

The **same six hues** exist in light, **deepened to hold contrast**. Status is *derived* from durable facts (runtime, PR, CI, review); the UI never stores a separate display truth. [doc] And **absence of a probe is not "dead"**: communicate uncertainty honestly.

### 2.8 Sentence case, always; mono only for code-like values  [doc]
Titles, buttons, menu items, labels, statuses, metadata: sentence case. **Never ALL CAPS** and no uppercase-tracking-as-decoration. (AO's `tokens.css` still contains legacy uppercase/wide-tracking tokens; ignore them.) Mono is for branches, paths, URLs, commands, aligned numbers, not to make prose look "technical".

Copy: **remove helper text the control or title already explains.** Name things by the user's job ("Agents", "Accounts"), not internal vocabulary ("Harness", "Subscriptions"). Verb + source labels ("Clone a repo", "Open a local folder"). Nouns name places ("Archived sessions"), verbs name actions ("Archive").

### 2.9 Type scale  [doc][code]

| Role | Size | Use |
| --- | --- | --- |
| Nano | 8px | exceptional rail chrome |
| Micro | 10px | tight metadata |
| Caption | 11px | lane labels, badges |
| Secondary | 12px | helper copy, compact list detail |
| Dense control | **13px** | buttons, rows |
| Body | **14px** | prose, normal UI |
| Dialog title | 15px | modal/section titles |
| Empty state | 17px | short guidance heading |
| Page heading | 21–22px | route-level title only |

Weights: **500** for controls/small hierarchy, **600** only for headings/selected values. Letter-spacing: slight negative on headings (-0.01 to -0.025em).

### 2.10 Motion  [doc][code]
- `--duration-fast` 120ms (hover/color/icon), `--duration-normal` 150ms (small layout/panel), ~280ms "settle" only where layout must stay spatially legible.
- Easings: `--ease-out: cubic-bezier(0.23, 1, 0.32, 1)` (entry, the house curve), `--ease-in-out: cubic-bezier(0.77, 0, 0.175, 1)`.
- Entry animations: overlay 80ms, modal 120ms, popover 150ms, toast 180ms.
- **Liveness shimmer** on a working row title: a *masked copy* of the text moved with `transform` (2.4s ease-in-out infinite), tinted ~40% toward the working blue. Hidden under reduced motion. (The older `background-position` version was removed because it repainted every row each frame and measurably delayed typing: median input latency 50.1 → 35.4 ms.)
- Don't pulse several things at once; don't animate counts or metrics; don't animate text to feel busy.
- **Streaming text** fades in per character in chat; settling a turn must not make anything jump (fade into reserved space).

### 2.11 Layout rules  [doc]
- Persistent shell: **sidebar (240px / 48px rail) · center task surface · collapsible inspector**. Routes replace the center, never the orientation.
- Topbar row **36px** everywhere; only orientation and high-value actions.
- Narrow widths: keep the task, compact sidebar chrome, collapse inspector content before shrinking targets below usable size.
- **Resize grips:** hover-only 2px line over the pane border, fading at both ends; no pill, nothing on click/drag; width stops at the panel's min/max.
- **Nothing moves on hover.** Row actions appear in a reserved slot on hover/focus, never changing row width, and remain keyboard-reachable. Destructive actions stay visually quiet until intentional hover/focus.

### 2.12 Elevation & glass  [doc][code]
Shadows only for transient layers: `--shadow-popover: 0 4px 12px -4px oklch(0 0 0 / 14%)`. Overlay edge is a **2% foreground** hairline. A blurred "glass" surface (`popover` at 60% + `blur(28px) saturate(1.6)`) is allowed **only** for Cmd+K, the archive modal and the cloud New-task dialog, and falls back to a solid surface when the window is translucent or reduced transparency is requested.

### 2.13 Modals  [doc]
One composed elevated surface: restrained scrim, **one title**, a close control, the decision/form body, a concise action area. No header band, no nested cards, no gradient, no prose that doesn't change the decision. Multi-step flows present **one decision at a time**, cross-fade in place while height springs; **nothing slides sideways**. Tiles in pickers are borderless: faint fill at rest, stronger fill on hover (instant), bare muted icon, one short line saying *why you'd pick it*.

### 2.14 "Worked for" and truthful live feedback  [doc]
Every settled agent turn opens with **"Worked for <time>"** (a fold with chevron if it had steps, plain text otherwise). The time never repeats elsewhere. Live rows fade a changing label once and update text in place. The sidebar update row shows what is happening in plain words and the whole row is the button for the next step; no percentages ("downloads never show a percentage"); a failed *check* never reaches the UI, only a failed *download/install*.

## 3. Explicit anti-patterns  [doc]

Generic SaaS hero/feature-grid inside the shell · decorative gradients/glows/blobs/textures · ALL CAPS labels · rainbow status colors or decorative accent borders · oversized avatars/icons · large empty padding that lowers density · uniform rounded cards separated by gaps where a divided list fits · a border around every row/field/option/nested panel · repeated labels/helper text · a new button/select/tooltip/menu/dialog when a shared primitive exists · repeated metric tiles when one table or glyph is clearer · fake terminals/progress/"AI" illustration · moving controls on hover or layout shift on refresh.

## 4. Signature checklist (quick review)

- [ ] Hairline/divider structure, not outlined boxes
- [ ] Ghost-family actions use the growing pill; nothing scales on press
- [ ] One primary per region; variants by meaning
- [ ] Status = one glyph in a fixed slot; distinct shapes; color spent sparingly
- [ ] Sentence case; no helper text restating the control
- [ ] Mono only for code-like values; tabular numbers in columns
- [ ] Motion ≤150ms, compositor-only, reduced-motion safe
