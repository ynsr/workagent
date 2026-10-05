export type Mode = "start" | "review" | "sync"

/** Issue #26: Launch never defaults to the serve CWD. The backend
 * `GET /api/default-repo?ref=` returns the linked-worktree repo for the
 * typed ref, else the single-linked repo — else no default. */

export const MODES: readonly Mode[] = ["start", "review", "sync"]

export const COPY: Record<
  Mode,
  {
    label: string
    title: string
    description: string
    refHint: string
    refPlaceholder: string
    /** Confirm-dialog description, kept per mode so label edits can never
     * silently change the confirm text (replaces title string matching). */
    confirmDesc: string
  }
> = {
  start: {
    label: "Start",
    title: "Start from an issue",
    description: "Issue key, OWNER/REPO#22, or a full issue URL.",
    refHint: "Issue key, OWNER/REPO#22, or a full issue URL.",
    refPlaceholder: "IPG-932, OWNER/REPO#22, or https://…/issues/22",
    confirmDesc: "Creates a worktree from the issue and launches the coding agent.",
  },
  review: {
    label: "Review",
    title: "Review a PR/MR",
    description: "PR/MR URL, OWNER/REPO#33, or a worktree ref (key/branch/path).",
    refHint: "PR/MR URL, OWNER/REPO#33, or a worktree ref (key/branch/path).",
    refPlaceholder: "https://…/pull/33, OWNER/REPO#33, or jira:IPG-929",
    confirmDesc: "Creates a worktree from the PR/MR and launches a review agent.",
  },
  sync: {
    label: "Sync",
    title: "Sync a worktree",
    description: "Worktree key, issue/PR ref, or branch — bring its branch up to date with the base.",
    refHint: "Worktree key, issue/PR ref, or branch.",
    refPlaceholder: "jira:IPG-1, github:OWNER/REPO#33, or feat/IPG-929--x",
    confirmDesc: "Brings the worktree's branch up to date with its base branch (remote rebase by default; --merge merges locally).",
  },
}

export interface LaunchForm {
  ref: string
  repo: string
  depth: string
  base: string
  runMode: RunMode
  fixComments: boolean
  newFixSession: boolean
  merge: boolean
  extraPrompt: string
}
export const INITIAL: LaunchForm = {
  ref: "",
  repo: "",
  depth: "7",
  base: "",
  // Issue #24: Start/Review launches default to printing the harness
  // command for manual execution (Preview); running is opt-in per run mode.
  runMode: "preview",
  fixComments: false,
  newFixSession: false,
  merge: false,
  extraPrompt: "",
}

/** Extra user instructions appended via --extra-prompt (all modes). */
export function extraPromptArgs(form: Pick<LaunchForm, "extraPrompt">): string[] {
  const extra = form.extraPrompt.trim()
  return extra ? [`--extra-prompt=${extra}`] : []
}

/** CLI args for the selected run mode (Preview emits none: no -p flag). */
export function runModeArgs(mode: RunMode): string[] {
  return mode === "headless" ? ["--launch"] : mode === "terminal" ? ["--terminal"] : []
}

/** Options for the run-mode segmented control (Launch + StartDialog). */
export const RUN_MODE_OPTIONS: readonly { value: RunMode; label: string; hint: string }[] = [
  { value: "preview", label: "Preview", hint: "print the harness command (no -p flag), don't run" },
  { value: "headless", label: "Run headless", hint: "--launch — run now with auto-approve (--no-tty)" },
  { value: "terminal", label: "Run in terminal", hint: "--terminal — open the command in the OS terminal" },
]

/** True when the ref looks like a PR/MR (mirrors refs.py URL shapes). */
export function isPrShaped(ref: string): boolean {
  const t = ref.trim()
  return /\/pull\/\d+\/*$/.test(t) || /\/-\/merge_requests\/\d+\/*$/.test(t) || /^[^/\s]+\/[^/\s#]+#\d+$/.test(t)
}

export type RunMode = "preview" | "headless" | "terminal"

