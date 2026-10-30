import { useMemo, useState } from "react"
import { Trash2 } from "lucide-react"
import type { Repo, WorktreeMap } from "@/lib/api"
import { matchesQuery } from "@/components/StatusCells"
import { StatusTable, type StatusTableActions, type StatusTableSelection } from "@/components/StatusTable"
import { repoDisplayForEntry } from "@/lib/useRepoTabs"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"

/** Collapsed, searchable soft-deleted-worktrees section with undelete/destroy actions + per-repo purge. */
export function DeletedTable({
  worktrees,
  actions,
  repos,
  onClearDeleted,
  onDestroyDeleted,
  selection,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  repos?: Repo[]
  onClearDeleted?: (repo: string | null) => void
  onDestroyDeleted?: (keys: string[]) => void
  selection?: StatusTableSelection
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const list = repos ?? []
  const keys = useMemo(() => Object.keys(worktrees), [worktrees])
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return Object.fromEntries(
      Object.entries(worktrees).filter(([k, e]) => {
        if (!needle) return true
        if (repoDisplayForEntry(e, list).toLowerCase().includes(needle)) return true
        return matchesQuery(k, e, needle)
      }),
    )
  }, [worktrees, q, list])
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
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Filter deleted worktrees (key, branch, repo…)"
              className="min-w-52 flex-1 placeholder:text-muted-foreground/60 placeholder:italic sm:max-w-xs"
            />
            {onDestroyDeleted && selection && selection.selected && selection.selected.size > 0 ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onDestroyDeleted([...selection.selected].sort())}
                title={`Permanently destroy ${selection.selected.size} selected soft-deleted row(s): worktree dir + local branch + row. Remote branches untouched.`}
              >
                Destroy ({selection.selected.size})
              </Button>
            ) : null}
            {onClearDeleted ? (
              <Button
                variant="outline"
                size="sm"
                disabled={Object.keys(filtered).length === 0}
                onClick={() => onClearDeleted(null)}
                title={
                  Object.keys(filtered).length === 0
                    ? "Nothing to purge"
                    : `Permanently drop ${Object.keys(filtered).length} soft-deleted row(s) from the database`
                }
              >
                Clear ({Object.keys(filtered).length})
              </Button>
            ) : null}
          </div>
          <StatusTable
            worktrees={filtered}
            actions={actions}
            selection={selection}
          />
        </div>
      ) : null}
    </section>
  )
}
