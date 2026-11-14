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

/** True when *id* is visible under the repo's stored map (missing = visible). */
export function isOverlayVisible(
  overlay: Record<string, boolean> | undefined | null,
  id: string,
): boolean {
  if (!overlay || typeof overlay !== "object") return true
  const v = (overlay as Record<string, unknown>)[id]
  return v === undefined ? true : v !== false
}

/**
 * Hidden overlay ids for one repo: every `false` entry in the stored map.
 * `dedicated: false` additionally hides every flagged flow/action button.
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

/** Filter a repo-row actions object down to the visible overlay buttons. */
export function visibleActionsForRepo<T extends {
  onSync?: unknown
  onReview?: unknown
  onFixComments?: unknown
  onGenerateProdtest?: unknown
  onAdhoc?: unknown
  onCleanup?: unknown
  onStopTerminal?: unknown
  onOpenRun?: unknown
  onOpenSessions?: unknown
  onOpenFlows?: unknown
}>(
  actions: T,
  overlay: Record<string, boolean> | undefined | null,
): T {
  const next: Record<string, unknown> = { ...actions }
  if (!isOverlayVisible(overlay, "sync")) delete next.onSync
  if (!isOverlayVisible(overlay, "review")) delete next.onReview
  if (!isOverlayVisible(overlay, "fix-comments")) delete next.onFixComments
  if (!isOverlayVisible(overlay, "prodtest")) delete next.onGenerateProdtest
  if (!isOverlayVisible(overlay, "adhoc")) delete next.onAdhoc
  if (!isOverlayVisible(overlay, "cleanup")) delete next.onCleanup
  if (!isOverlayVisible(overlay, "stop-terminal")) delete next.onStopTerminal
  if (!isOverlayVisible(overlay, "open-runs")) delete next.onOpenRun
  if (!isOverlayVisible(overlay, "open-sessions")) delete next.onOpenSessions
  if (!isOverlayVisible(overlay, "open-flows")) delete next.onOpenFlows
  return next as T
}

/** True when the `dedicated` group toggle hides all flagged flow/action buttons. */
export function dedicatedHidden(overlay: Record<string, boolean> | undefined | null): boolean {
  return !isOverlayVisible(overlay, "dedicated")
}

/** True when one dedicated item (`flow:<label>` / `action:<label>`) is visible. */
export function isDedicatedVisible(
  overlay: Record<string, boolean> | undefined | null,
  kind: "flow" | "action",
  label: string,
): boolean {
  if (dedicatedHidden(overlay)) return false
  return isOverlayVisible(overlay, dedicatedButtonId(kind, label))
}

/**
 * Parse a `--overlay-buttons` CLI value (`"a,-b,flow:x"`) into
 * {button_id: bool}. Later entries win; empty → {}.
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
    if (t) out[t] = show
  }
  return out
}

/** Serialize a visibility map back to `--overlay-buttons` form. */
export function formatOverlayButtons(map: Record<string, boolean>): string {
  return Object.entries(map)
    .map(([k, v]) => (v === false ? `-${k}` : k))
    .join(",")
}
