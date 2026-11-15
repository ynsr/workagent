import { useMemo, useState } from "react"
import { Trash2 } from "lucide-react"
import type { Repo, WorktreeMap } from "@/lib/api"
import { StatusTable, type StatusTableActions, type StatusTableSelection } from "@/components/StatusTable"
import { Button } from "@/components/ui/button"
import { EMPTY_WORKTREE_FILTERS, filterWorktreeKeys, type WorktreeFilterState } from "@/lib/worktreeFilter"
import { repoDisplayForEntry } from "@/lib/useRepoTabs"

/** Collapsed soft-deleted-worktrees section with undelete/destroy actions.
 * Search lives in the shared Dashboard filter above (issue #102): this
 * table applies the parent filter instead of owning a second input,
 * including free-text repo-name matching via repoDisplayForEntry. */
export function DeletedTable({
  worktrees,
  actions,
  repos,
  onDestroyDeleted,
  selection,
  filter = EMPTY_WORKTREE_FILTERS,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  repos?: Repo[]
  onDestroyDeleted?: (keys: string[]) => void
  selection?: StatusTableSelection
  filter?: WorktreeFilterState
}) {
  const [open, setOpen] = useState(false)
  const repoList = repos ?? []
  const keys = useMemo(() => Object.keys(worktrees), [worktrees])
  const filtered = useMemo(() => {
    const hits = new Set(
      filterWorktreeKeys(worktrees, filter, "", undefined, (entry) => repoDisplayForEntry(entry, repoList)),
    )
    return Object.fromEntries(Object.entries(worktrees).filter(([k]) => hits.has(k)))
  }, [worktrees, filter, repoList])
  const visibleSelected = useMemo(() => {
    const visible = new Set(Object.keys(filtered))
    return [...(selection?.selected ?? new Set<string>())].filter((k) => visible.has(k)).sort()
  }, [filtered, selection?.selected])
  if (keys.length === 0) return null
  return (
    <section className="mt-4 rounded-md border bg-muted/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium"
      >
        <Trash2 aria-hidden className="size-3.5 text-muted-foreground" />
        {open ? "Hide" : "Show"} deleted worktrees ({keys.length})
      </button>
      {open ? (
        <div className="grid gap-2 px-3 pb-3">
          <div className="flex flex-wrap items-center gap-2">
            {onDestroyDeleted && visibleSelected.length > 0 ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  // Destroy only the selection still visible in this table
                  // (shared-form scoping, issue #102): the parent selection
                  // may hold keys filtered out above.
                  onDestroyDeleted(visibleSelected)
                }}
                title={`Permanently destroy ${visibleSelected.length} selected soft-deleted row(s): worktree dir + local branch + AI harness sessions. Remote branches untouched.`}
              >
                Destroy ({visibleSelected.length})
              </Button>
            ) : null}
          </div>
          <StatusTable
            worktrees={filtered}
            actions={actions}
            selection={selection}
            embed={{ hideFilter: true, selParam: null, filterParam: null }}
          />
        </div>
      ) : null}
    </section>
  )
}
