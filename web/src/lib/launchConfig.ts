export type Mode = "start" | "review" | "sync" | "adhoc" | "flow" | "prodtest"

/** Issue #26: Launch never defaults to the serve CWD. The backend
 * `GET /api/default-repo?ref=` returns the linked-worktree repo for the
 * typed ref, else the single-linked repo — else no default. */

export const MODES: readonly Mode[] = ["start", "review", "sync", "adhoc", "flow", "prodtest"]

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
  flow: {
    label: "Flow",
    title: "Run a flow template",
    description: "Flow templates starting with Start, scoped to an issue ref.",
    refHint: "Issue key, OWNER/REPO#22, or a full issue URL.",
    refPlaceholder: "IPG-932, OWNER/REPO#22, or https://…/issues/22",
    confirmDesc: "Runs the flow template scoped to the issue ref.",
  },
  sync: {
    label: "Sync",
    title: "Sync a worktree",
    description: "Worktree key, issue/PR ref, or branch — bring its branch up to date with the base.",
    refHint: "Worktree key, issue/PR ref, or branch.",
    refPlaceholder: "jira:IPG-1, github:OWNER/REPO#33, or feat/IPG-929--x",
    confirmDesc: "Brings the worktree's branch up to date with its base branch (remote rebase by default; --merge merges locally).",
  },
  prodtest: {
    label: "Prodtest",
    title: "Plan or run the prodtest",
    description: "Issue key, OWNER/REPO#22, or a worktree ref — generate the plan or run it at its release SHA.",
    refHint: "Issue key, OWNER/REPO#22, or a worktree ref.",
    refPlaceholder: "IPG-932, OWNER/REPO#22, or jira:IPG-932@proj",
    confirmDesc: "Runs the prodtest plan or run command (harness flags per run mode).",
  },
  adhoc: {
    label: "Adhoc Prompt",
    title: "Run an adhoc prompt",
    description: "One or more worktree refs — runs the prompt on each linked worktree, sequentially.",
    refHint: "Worktree key, branch, or path — one per line, or comma-separated.",
    refPlaceholder: "jira:IPG-932@proj",
    confirmDesc: "Runs the adhoc prompt on each linked worktree (non-TTY; deactivated/invalid rows are skipped).",
  },
}

/** Prodtest-tab action selector: Plan (generate) vs Run (execute at release SHA). */
export type ProdtestAction = "plan" | "run"
export const PRODTEST_ACTION_OPTIONS: readonly { value: ProdtestAction; label: string; hint: string }[] = [
  { value: "plan", label: "Plan", hint: "prodtest plan — generate the plan" },
  { value: "run", label: "Run", hint: "prodtest run — execute at the release SHA" },
]
export interface LaunchForm {
  ref: string
  repo: string
  depth: string
  base: string
  runMode: RunMode
  reviewAction: ReviewAction
  fixComments: boolean
  thenFixComments: boolean
  useReviewSession: boolean
  merge: boolean
  noStatus: boolean
  extraPrompt: string
  relatedRepos: string[]
  prodtestAction: ProdtestAction
  adhocPrompt: string
}
export const INITIAL: LaunchForm = {
  ref: "",
  repo: "",
  depth: "7",
  base: "",
  // Issue #24: Start/Review launches default to printing the harness
  // command for manual execution (Preview); running is opt-in per run mode.
  runMode: "preview",
  reviewAction: "review",
  fixComments: false,
  thenFixComments: false,
  useReviewSession: false,
  merge: false,
  noStatus: false,
  extraPrompt: "",
  relatedRepos: [],
  prodtestAction: "run",
  adhocPrompt: "",
}


/** Extra user instructions appended via --extra-prompt (all modes). */
export function extraPromptArgs(form: Pick<LaunchForm, "extraPrompt">): string[] {
  const extra = form.extraPrompt.trim()
  return extra ? [`--extra-prompt=${extra}`] : []
}

/**
 * Split the adhoc ref box into positional refs. The Launch adhoc field
 * takes one ref per line (or comma-separated); blank entries are dropped
 * so `adhoc REF… --prompt` always receives ≥1 ref from the form.
 */
export function splitAdhocRefs(ref: string): string[] {
  return ref
    .split(/[\n,]+/)
    .map((r) => r.trim())
    .filter((r) => r.length > 0)
}

/** CLI args for `adhoc REF… --prompt TEXT` (Preview emits neither --launch nor --terminal: no -p flag). */
export function adhocArgs(form: Pick<LaunchForm, "adhocPrompt" | "extraPrompt">): string[] {
  const prompt = form.adhocPrompt.trim()
  return [`--prompt=${prompt}`, ...extraPromptArgs(form)]
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

/** Review-tab action selector: Review vs Fix comments. Fix maps to the
 * existing standalone `review --fix-comments` action (same-session reuse);
 * Review optionally chains it via --then-fix-comments. */
export type ReviewAction = "review" | "fix"
export const REVIEW_ACTION_OPTIONS: readonly { value: ReviewAction; label: string; hint: string }[] = [
  { value: "review", label: "Review", hint: "review the PR/MR" },
  { value: "fix", label: "Fix comments", hint: "--fix-comments — fix open review comments" },
]
/** True when the ref looks like a PR/MR (mirrors refs.py URL shapes). */
export function isPrShaped(ref: string): boolean {
  const t = ref.trim()
  return /\/pull\/\d+\/*$/.test(t) || /\/-\/merge_requests\/\d+\/*$/.test(t) || /^[^/\s]+\/[^/\s#]+#\d+$/.test(t)
}

export type RunMode = "preview" | "headless" | "terminal"

