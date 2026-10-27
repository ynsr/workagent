import { useEffect, useMemo, useState } from "react"
import { Trash2 } from "lucide-react"
import type { Repo, WorktreeMap } from "@/lib/api"
import { matchesQuery } from "@/components/StatusCells"
import { StatusTable, type StatusTableActions } from "@/components/StatusTable"
import { repoDisplayForEntry } from "@/lib/useRepoTabs"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"

/** Collapsed, searchable soft-deleted-worktrees section with Undelete-only actions + per-repo purge. */
export function DeletedTable({
  worktrees,
  actions,
  repos,
  onClearDeleted,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  repos?: Repo[]
  onClearDeleted?: (repo: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState("")
  const [repo, setRepo] = useState("all")
  const list = repos ?? []
  const available = useMemo(() => {
    const seen: Record<string, true> = {}
    for (const e of Object.values(worktrees)) {
      const label = repoDisplayForEntry(e, list)
      if (label) seen[label] = true
    }
    return Object.keys(seen).sort((a, b) => a.localeCompare(b))
  }, [worktrees, list])
  useEffect(() => {
    if (repo !== "all" && !available.includes(repo)) setRepo("all")
  }, [available, repo])
  const keys = useMemo(() => Object.keys(worktrees), [worktrees])
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return Object.fromEntries(
      Object.entries(worktrees).filter(([k, e]) => {
        if (repo !== "all" && repoDisplayForEntry(e, list) !== repo) return false
        return !needle || matchesQuery(k, e, needle)
      }),
    )
  }, [worktrees, q, repo, list])
  if (keys.length === 0) return null
  const activeRepo = repo === "all" ? null : repo
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
              placeholder="Filter deleted worktrees"
              className="min-w-52 flex-1 placeholder:text-muted-foreground/60 placeholder:italic sm:max-w-xs"
            />
            <select
              value={repo}
              onChange={(e) => setRepo(e.target.value)}
              aria-label="Filter deleted worktrees by repo"
              className="h-9 rounded-md border border-input bg-transparent px-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
            >
              <option value="all">All repos ({keys.length})</option>
              {available.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            {onClearDeleted ? (
              <Button
                variant="outline"
                size="sm"
                disabled={Object.keys(filtered).length === 0}
                onClick={() => onClearDeleted(activeRepo)}
                title={
                  Object.keys(filtered).length === 0
                    ? "Nothing to purge"
                    : activeRepo
                      ? `Permanently drop ${Object.keys(filtered).length} soft-deleted row(s) for ${activeRepo} from the database`
                      : `Permanently drop ${Object.keys(filtered).length} soft-deleted row(s) from the database`
                }
              >
                Clear{activeRepo ? ` ${activeRepo}` : ""} ({Object.keys(filtered).length})
              </Button>
            ) : null}
          </div>
          <StatusTable
            worktrees={filtered}
            actions={actions}
          />
        </div>
      ) : null}
    </section>
  )
}
