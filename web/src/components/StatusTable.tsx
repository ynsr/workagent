import type { WorktreeMap } from "@/lib/api"

export interface StatusTableActions {
  /** Navigate to Launch with mode=sync&ref=key (prefill contract). */
  onSync?: (key: string) => void
  /** Navigate to Launch with mode=review&ref=key (prefill contract). */
  onReview?: (key: string) => void
  /** Navigate to Launch with mode=review&fixComments=1&ref=key. */
  onFixComments?: (key: string) => void
  onCleanup?: (key: string) => void
  /** Reactivate a deactivated worktree (`link reactivate` confirmed run). */
  onReactivate?: (key: string) => void
  onOpenWorktree: (key: string) => void
  onOpenRun: (key: string) => void
  /** Navigate to the Sessions page filtered to this worktree. */
  onOpenSessions?: (key: string) => void
  /** Stop the terminal-origin agent running on this worktree (404 when none). */
  onStopTerminal?: (key: string) => void
}

export { StatusTable } from "@/components/StatusTableMain"
export type { WorktreeMap }

/** Optional row-selection contract (Dashboard bulk actions). */
export interface StatusTableSelection {
  /** Selected worktree keys (controlled). */
  selected?: Set<string>
  /** Called with the next selection set on toggle/select-all/clear. */
  onSelectionChange?: (next: Set<string>) => void
  /** Called with the filtered visible keys whenever they change. */
  onVisibleKeys?: (keys: string[]) => void
}
