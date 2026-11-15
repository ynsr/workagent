import { useMemo, useState } from "react"
import { Pause } from "lucide-react"
import type { WorktreeMap } from "@/lib/api"
import { StatusTable, type StatusTableActions } from "@/components/StatusTable"
import { EMPTY_WORKTREE_FILTERS, filterWorktreeKeys, type WorktreeFilterState } from "@/lib/worktreeFilter"

/** Collapsed deactivated-worktrees section with Reactivate-only actions.
 * Search lives in the shared Dashboard filter above (issue #102). */
export function DeactivatedTable({
  worktrees,
  actions,
  filter = EMPTY_WORKTREE_FILTERS,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  filter?: WorktreeFilterState
}) {
  const [open, setOpen] = useState(false)
  const keys = useMemo(() => Object.keys(worktrees), [worktrees])
  const filtered = useMemo(() => {
    const hits = new Set(filterWorktreeKeys(worktrees, filter))
    return Object.fromEntries(Object.entries(worktrees).filter(([k]) => hits.has(k)))
  }, [worktrees, filter])
  if (keys.length === 0) return null
  return (
    <section className="mt-4 rounded-md border bg-muted/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium"
      >
        <Pause aria-hidden className="size-3.5 text-muted-foreground" />
        {open ? "Hide" : "Show"} deactivated worktrees ({keys.length})
      </button>
      {open ? (
        <div className="grid gap-2 px-3 pb-3">
          <StatusTable
            worktrees={filtered}
            actions={actions}
            embed={{ hideFilter: true, selParam: null, filterParam: null }}
          />
