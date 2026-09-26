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
  launch: boolean
  fixComments: boolean
  merge: boolean
}
export const INITIAL: LaunchForm = {
  ref: "",
  repo: "",
  depth: "7",
  base: "",
  // Issue #24: Start/Review launches default to printing the harness
  // command for manual execution instead of auto-running (opt in with Run agent).
  launch: false,
  fixComments: false,
  merge: false,
}

