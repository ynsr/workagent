import type { WorktreeMap } from "@/lib/api"

export interface StatusTableActions {
  /** Navigate to Launch with mode=sync&ref=key (prefill contract). */
  onSync?: (key: string) => void
  /** Navigate to Launch with mode=review&ref=key (prefill contract). */
  onReview?: (key: string) => void
  /** Navigate to Launch with mode=review&fixComments=1&ref=key. */
  onFixComments?: (key: string) => void
  onCleanup?: (key: string) => void
  /** Open the worktree folder locally (`open` RunCommand; disabled when network-exposed). */
  onOpenWorktree: (key: string) => void
  onOpenRun: (key: string) => void
  /** Navigate to the Sessions page filtered to this worktree. */
  onOpenSessions?: (key: string) => void
}

export { StatusTable } from "@/components/StatusTableMain"
export type { WorktreeMap }
