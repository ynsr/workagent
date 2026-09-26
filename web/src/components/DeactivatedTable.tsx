import { useMemo, useState } from "react"
import { Pause } from "lucide-react"
import type { WorktreeMap } from "@/lib/api"
import { matchesQuery } from "@/components/StatusCells"
import { StatusTable, type StatusTableActions } from "@/components/StatusTable"
import { Input } from "@/components/ui/input"

/** Collapsed, searchable deactivated-worktrees section with Reactivate-only actions. */
export function DeactivatedTable({
  worktrees,
  actions,
  networkExposed = false,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  networkExposed?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const keys = useMemo(() => Object.keys(worktrees), [worktrees])
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return Object.fromEntries(
      Object.entries(worktrees).filter(
        ([k, e]) => !needle || matchesQuery(k, e, needle),
      ),
    )
  }, [worktrees, q])
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
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Filter deactivated worktrees"
            className="placeholder:text-muted-foreground/60 placeholder:italic"
          />
          <StatusTable
            worktrees={filtered}
            actions={actions}
            networkExposed={networkExposed}
          />
        </div>
      ) : null}
    </section>
  )
}
