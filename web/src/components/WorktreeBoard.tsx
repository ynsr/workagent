import { useMemo } from "react"
import type { KanbanColumn, Repo, WorktreeEntry, WorktreeMap } from "@/lib/api"
import type { StatusTableActions } from "@/components/StatusTable"
import { RepoTabsRow } from "@/components/RepoTabs"
import { WorktreeBoardCard } from "@/components/WorktreeBoardCard"
import { filterWorktreeKeys, type WorktreeFilterState } from "@/lib/worktreeFilter"
import type { useRepoTabs } from "@/lib/useRepoTabs"

type RepoTabs = ReturnType<typeof useRepoTabs>

export interface WorktreeBoardProps {
  worktrees: WorktreeMap
  actions: StatusTableActions
  filter?: WorktreeFilterState
  repoTabs?: RepoTabs & { repos: Repo[] }
  /** When true, render as a single stacked card list (used below md breakpoint). */
  stacked?: boolean
}

const LANES: Array<{
  id: KanbanColumn
  label: string
  dotClass: string
}> = [
  { id: "needs_you", label: "Needs you", dotClass: "bg-[#f06445]" },
  { id: "working", label: "Working", dotClass: "bg-[#60a5fa]" },
  { id: "in_review", label: "In review", dotClass: "bg-[#f59e0b]" },
  { id: "ready_to_merge", label: "Ready to merge", dotClass: "bg-[#4ade80]" },
]

const ATTENTION_PRIORITY: Record<string, number> = {
  pending_question: 0,
  needs_input: 1,
  flow_paused: 2,
  run_failed: 3,
  ci_failed: 4,
  unresolved_comments: 5,
}

function attentionRank(entry: WorktreeEntry): number {
  const reason = entry.attention_reason ?? ""
  return reason in ATTENTION_PRIORITY ? ATTENTION_PRIORITY[reason]! : 99
}

function sortLaneItems(items: Array<[string, WorktreeEntry]>): Array<[string, WorktreeEntry]> {
  return [...items].sort(([kA, a], [kB, b]) => {
    const rDiff = attentionRank(a) - attentionRank(b)
    if (rDiff !== 0) return rDiff
    const tA = a.added_at ?? ""
    const tB = b.added_at ?? ""
    if (tA !== tB) return tB.localeCompare(tA)
    return kA.localeCompare(kB)
  })
}

export function WorktreeBoard({
  worktrees,
  actions,
  filter,
  repoTabs,
  stacked = false,
}: WorktreeBoardProps) {
  const visibleKeys = useMemo(() => {
    if (!filter && !repoTabs?.repoFilter) return Object.keys(worktrees)
    const repoOf = repoTabs
      ? (entry: WorktreeEntry) => {
          const p = entry.repo ?? ""
          const match = repoTabs.repos.find((r) => r.path === p || r.name === p)
          return match ? match.name : "(other)"
        }
      : undefined
    return filterWorktreeKeys(
      worktrees,
      filter ?? { q: "", pr: "", issue: "", author: "" },
      repoTabs?.repoFilter ?? "",
      repoOf,
    )
  }, [worktrees, filter, repoTabs])

  const grouped = useMemo(() => {
    const buckets: Record<KanbanColumn, Array<[string, WorktreeEntry]>> = {
      needs_you: [],
      working: [],
      in_review: [],
      ready_to_merge: [],
    }
    for (const key of visibleKeys) {
      const entry = worktrees[key]
      if (!entry) continue
      const col: KanbanColumn =
        entry.kanban_column && entry.kanban_column in buckets
          ? entry.kanban_column
          : "in_review"
      buckets[col].push([key, entry])
    }
    return {
      needs_you: sortLaneItems(buckets.needs_you),
      working: sortLaneItems(buckets.working),
      in_review: sortLaneItems(buckets.in_review),
      ready_to_merge: sortLaneItems(buckets.ready_to_merge),
    }
  }, [visibleKeys, worktrees])

  const totalVisible = visibleKeys.length

  return (
    <div data-testid={stacked ? "worktree-board-stacked" : "worktree-board"}>
      {repoTabs ? <RepoTabsRow repoTabs={repoTabs} /> : null}
      {totalVisible === 0 ? (
        <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
          No worktrees match the current filter.
        </p>
      ) : stacked ? (
        <div className="flex flex-col gap-3">
          {LANES.flatMap((lane) =>
            grouped[lane.id].map(([key, entry]) => (
              <WorktreeBoardCard
                key={key}
                worktreeKey={key}
                entry={entry}
                actions={actions}
                column={lane.id}
              />
            )),
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {LANES.map((lane) => {
            const items = grouped[lane.id]
            return (
              <section
                key={lane.id}
                aria-label={lane.label}
                data-testid={`board-lane-${lane.id}`}
                className="flex min-w-0 flex-col gap-2.5 rounded-lg border border-border/70 bg-muted/20 p-2.5"
              >
                <header className="flex items-center justify-between gap-2 px-1">
                  <div className="flex items-center gap-2">
                    <span className={`size-2.5 shrink-0 rounded-full ${lane.dotClass}`} aria-hidden />
                    <h2 className="text-sm font-semibold text-foreground">{lane.label}</h2>
                  </div>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
                    {items.length}
                  </span>
                </header>
                <div className="flex flex-col gap-2">
                  {items.length === 0 ? (
                    <p className="rounded-md border border-dashed border-border/60 px-3 py-4 text-center text-xs text-muted-foreground">
                      Empty
                    </p>
                  ) : (
                    items.map(([key, entry]) => (
                      <WorktreeBoardCard
                        key={key}
                        worktreeKey={key}
                        entry={entry}
                        actions={actions}
                        column={lane.id}
                      />
                    ))
                  )}
                </div>
              </section>
            )
          })}
        </div>
      )}
    </div>
  )
}
