/**
 * StatusTable overlay-button catalog + per-repo visibility helpers.
 * Backend: repos.overlay_buttons {button_id: bool} ('{}' = all visible).
 * Fixed ids mirror FIXED_OVERLAY_BUTTONS in src/workagent/repos.py.
 * Per-item dedicated ids are `flow:<label>` / `action:<label>` (the same
 * labels the Actions/Flow-templates pages toggle via dedicated_overlay).
 */
export const FIXED_OVERLAY_BUTTONS: readonly { id: string; label: string }[] = [
  { id: "sync", label: "Sync" },
  { id: "review", label: "Review" },
  { id: "fix-comments", label: "Fix PR comments" },
  { id: "prodtest", label: "Generate prodtest plan" },
  { id: "adhoc", label: "Run adhoc prompt" },
  { id: "cleanup", label: "Cleanup" },
  { id: "stop-terminal", label: "Stop terminal agent" },
  { id: "open-runs", label: "Open runs" },
  { id: "open-sessions", label: "Worktree sessions" },
  { id: "open-flows", label: "Open flows" },
  { id: "resume-session", label: "Resume session" },
  { id: "dedicated", label: "Dedicated action/flow buttons" },
]

/** The full overlay id for a dedicated item: `flow:<label>` / `action:<label>`. */
export function dedicatedButtonId(kind: "flow" | "action", label: string): string {
  return `${kind}:${label}`
}


/**
 * Hidden overlay ids for one repo: every `false` entry in the stored map.
 * `dedicated: false` is collected like any other id; `DedicatedOverlay`
 * treats membership as the group kill-switch for flagged flow/action buttons.
 * Unknown ids (e.g. labels since renamed) are ignored by the renderers.
 */
export function hiddenOverlayIds(
  overlay: Record<string, boolean> | undefined | null,
): Set<string> {
  const hidden = new Set<string>()
  if (!overlay || typeof overlay !== "object") return hidden
  for (const [k, v] of Object.entries(overlay)) {
    if (v === false) hidden.add(k)
  }
  return hidden
}


/**
 * Parse a `--overlay-buttons` CLI value (`"a,-b,flow:x"`) into
 * {button_id: bool}. Later entries win; empty → {}. Mirrors the backend
 * (`repos.parse_overlay_buttons`): `-` alone, empty ids, and `flow:` /
 * `action:` with an empty or whitespace-containing label throw.
 */
export function parseOverlayButtons(raw: string): Record<string, boolean> {
  const out: Record<string, boolean> = {}
  for (const tok of (raw ?? "").split(",")) {
    let t = tok.trim()
    if (!t) continue
    let show = true
    if (t.startsWith("-")) {
      show = false
      t = t.slice(1).trim()
    }
    if (!t) throw new Error(`invalid overlay button id: ${JSON.stringify(tok)}`)
    if (t.startsWith("flow:") || t.startsWith("action:")) {
      const label = t.slice(t.indexOf(":") + 1)
      if (!label || /\s/.test(label)) throw new Error(`invalid overlay button id: ${JSON.stringify(t)}`)
    }
    out[t] = show
  }
  return out
}

/** Serialize a visibility map back to `--overlay-buttons` form. */
export function formatOverlayButtons(map: Record<string, boolean>): string {
  return Object.entries(map)
    .map(([k, v]) => (v === false ? `-${k}` : k))
    .join(",")
}
