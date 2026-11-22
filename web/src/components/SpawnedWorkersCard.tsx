import { Link } from "react-router-dom"
import { GitBranch, Loader2, Layers } from "lucide-react"
import { RunStateBadge } from "@/components/StateBadge"
import { Badge } from "@/components/ui/badge"
import type { SpawnedRunSummary } from "@/lib/api"
import { shortId } from "@/lib/format"

export interface SpawnedWorkersCardProps {
  workers: SpawnedRunSummary[]
}

function harnessBadgeLabel(harness?: string): string {
  const h = (harness ?? "").trim().toUpperCase()
  return h || "AGENT"
}

export function SpawnedWorkersCard({ workers }: SpawnedWorkersCardProps) {
  if (!workers || workers.length === 0) return null

  const runningCount = workers.filter((w) => w.state === "running").length
  const succeededCount = workers.filter((w) => w.state === "succeeded").length
  const failedCount = workers.filter((w) => w.state === "failed").length

  return (
    <div
      className="rounded-md border border-sky-500/30 bg-sky-500/[0.06] p-3 text-xs"
      data-testid="spawned-workers-card"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-medium text-sky-200">
          <Layers className="h-3.5 w-3.5 text-sky-400" />
          <span>
            Started {workers.length} {workers.length === 1 ? "worker" : "workers"} — Each in its own worktree
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-white/60">
          {runningCount > 0 ? <span>{runningCount} running</span> : null}
          {succeededCount > 0 ? <span>{succeededCount} done</span> : null}
          {failedCount > 0 ? <span className="text-rose-300">{failedCount} failed</span> : null}
        </div>
      </div>

      <ul className="space-y-1.5">
        {workers.map((w) => {
          const branchSlug = w.branch || w.worktree_ref || w.target || shortId(w.id)
          const workspaceHref = w.worktree_ref
            ? `/worktrees/${encodeURIComponent(w.worktree_ref)}`
            : null
          const runHref = `/runs/${encodeURIComponent(w.id)}`
          return (
            <li
              key={w.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded border border-white/10 bg-black/25 px-2.5 py-1.5"
              data-testid={`spawned-worker-${w.id}`}
            >
              <div className="flex min-w-0 items-center gap-2">
                {w.state === "running" ? (
                  <Loader2
                    className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-300"
                    aria-label="Running"
                    data-testid="worker-spinner"
                  />
                ) : null}
                <RunStateBadge state={w.state} />
                <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                  {harnessBadgeLabel(w.harness_name)}
                </Badge>
                <span className="flex min-w-0 items-center gap-1 truncate font-mono text-white/85">
                  <GitBranch className="h-3 w-3 shrink-0 text-white/45" />
                  <span className="truncate">{branchSlug}</span>
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {workspaceHref ? (
                  <Link
                    to={workspaceHref}
                    className="text-sky-300 underline-offset-2 hover:underline"
                  >
                    Open workspace
                  </Link>
                ) : null}
                <Link
                  to={runHref}
                  className="text-white/60 underline-offset-2 hover:text-white hover:underline"
                >
                  Run
                </Link>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
