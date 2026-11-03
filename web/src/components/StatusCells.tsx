import { MessagesSquare } from "lucide-react"
import type { WorktreeMap } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export function CommitsCell({ entry }: { entry: WorktreeMap[string] }) {
  const behind = entry.commits_detail?.behind
  const ahead = entry.commits_detail?.ahead
  return (
    <span
      className="font-mono text-[13px]"
      title="commits behind|ahead vs the base branch"
    >
      {typeof behind === "number" ? (
        <span className={behind > 0 ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground"}>
          {behind}
        </span>
      ) : (
        <span className="text-muted-foreground">—</span>
      )}
      <span className="text-muted-foreground">|</span>
      {typeof ahead === "number" ? (
        <span className={ahead > 0 ? "text-sky-600 dark:text-sky-400" : "text-muted-foreground"}>
          {ahead}
        </span>
      ) : null}
    </span>
  )
}
/** Reviews badge `<reviews> · <resolved> of <total threads>`; "—" when no PR or lookup failed. */
export function ReviewsCell({ entry }: { entry: WorktreeMap[string] }) {
  const rd = entry.reviews_detail
  if (!rd) return <span className="font-mono text-[13px] text-muted-foreground">—</span>
  const total = rd.resolved + rd.unresolved
  const label = `${rd.resolved} of ${total}`
  const title = `${rd.reviews} completed review passes, ${label} threads resolved`
  return (
    <Badge
      variant="secondary"
      title={title}
      aria-label={title}
      className="cursor-default rounded-full font-mono font-normal"
    >
      <MessagesSquare data-testid="comments-icon" aria-hidden className="size-3" />
      <span className="text-muted-foreground">{rd.reviews}</span>
      <span aria-hidden className="text-muted-foreground/60">·</span>
      <span>
        <span className="text-emerald-600 dark:text-emerald-400">{rd.resolved}</span>
        <span className="text-muted-foreground"> of {total}</span>
      </span>
    </Badge>
  )
}

const PRODTEST_TONE: Record<string, string> = {
  pending: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  pending_prodtest: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  running: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  passed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  failed: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  blocked: "border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300",
}

/** Prodtest badge: done|risk|revN or status; "—" when no plan exists. */
export function ProdtestCell({ entry }: { entry: WorktreeMap[string] }) {
  const d = entry.prodtest_detail
  if (!d) return <span className="font-mono text-[13px] text-muted-foreground">—</span>
  const status = entry.prodtest ?? d.status ?? "-"
  const risk = d.plan_risk ? ` · ${d.plan_risk}` : ""
  const rev = d.plan_revision ? ` · rev${d.plan_revision}` : ""
  const report = d.last_run ? ` · ${d.last_run}` : ""
  const title = `prodtest ${status}${risk}${rev}${report} (${d.issue_key})`
  const tone = PRODTEST_TONE[status] ?? ""
  return (
    <Badge
      variant="outline"
      title={title}
      aria-label={title}
      className={cn("cursor-default rounded-full font-mono font-normal", tone)}
    >
      <span>{status}</span>
      {d.plan_risk ? <span aria-hidden className="text-muted-foreground/60">·</span> : null}
      {d.plan_risk ? <span>{d.plan_risk}</span> : null}
      {d.plan_revision ? <span aria-hidden className="text-muted-foreground/60">·</span> : null}
      {d.plan_revision ? <span className="text-muted-foreground">rev{d.plan_revision}</span> : null}
    </Badge>
  )
}

export function matchesQuery(key: string, entry: WorktreeMap[string], q: string): boolean {
  const hay = [
    key,
    entry.branch ?? "",
    entry.pr ?? "",
    entry.pr_detail?.title ?? "",
    entry.worktree ?? "",
  ]
    .join("\n")
    .toLowerCase()
  return hay.includes(q)
}

