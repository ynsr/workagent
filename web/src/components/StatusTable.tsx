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
  /** Restore a soft-deleted worktree (`link undelete` confirmed run). */
  onUndelete?: (key: string) => void
  /** Permanently destroy a soft-deleted row: worktree dir + local branch + row. */
  onDestroyDeleted?: (key: string) => void
  onOpenWorktree: (key: string) => void
  onOpenRun: (key: string) => void
  /** Navigate to the Sessions page filtered to this worktree. */
  onOpenSessions?: (key: string) => void
  /** Navigate to the Flows page filtered to this worktree. */
  onOpenFlows?: (key: string) => void
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

/** Embedded-table options: hide the inner filter toolbar and namespace URL params. */
export interface StatusTableEmbed {
  /** Skip the filter toolbar (parent owns the single filter input). */
  hideFilter?: boolean
  /** URL param carrying the selection (default "sel"); null = internal-only, never touches the URL. */
  selParam?: string | null
  /** URL param carrying the filter text (default "q"); null = ignore the URL filter. */
  filterParam?: string | null
}
