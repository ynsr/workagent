import type { PrDetail, RunState } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const RUN_STATE_BADGES: Record<RunState, { label: string; className: string }> = {
  running: {
    label: "Running",
    className: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  },
  succeeded: {
    label: "Succeeded",
    className:
      "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
  failed: {
    label: "Failed",
    className:
      "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  },
  needs_input: {
    label: "Needs input",
    className:
      "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
  cancelled: {
    label: "Cancelled",
    className: "border-border bg-muted text-muted-foreground",
  },
}

export function RunStateBadge({
  state,
  className,
}: {
  state: RunState
  className?: string
}) {
  const badge = RUN_STATE_BADGES[state]
  return (
    <Badge variant="outline" className={cn(badge.className, className)}>
      {badge.label}
    </Badge>
  )
}

const PR_STATE_STYLES: Record<string, string> = {
  open: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  merged: "border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300",
  closed: "border-border bg-muted text-muted-foreground",
}

/** PR/MR state badge from pr_detail; falls back to the display string. */
export function PrBadge({ pr }: { pr: PrDetail | null | undefined }) {
  if (!pr) return null
  const style = PR_STATE_STYLES[pr.state] ?? PR_STATE_STYLES.closed
  return (
    <Badge
      variant="outline"
      className={style}
      title={`${pr.title} — ${pr.author} (${pr.tool})`}
    >
      #{pr.number} {pr.state}
    </Badge>
  )
}


type CiState = "success" | "failure" | "running" | "not_started"

const CI_BADGES: Record<CiState, { symbol: string; className: string }> = {
  success: {
    symbol: "✓",
    className:
      "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  },
  failure: {
    symbol: "✗",
    className:
      "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  },
  running: {
    symbol: "●",
    className:
      "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  },
  not_started: {
    symbol: "–",
    className: "border-border bg-muted text-muted-foreground",
  },
}

/** CI pipeline badge; missing/unknown status renders as a gray dash. */
export function CiBadge({ ci }: { ci?: string | null }) {
  const state: CiState =
    ci === "success" || ci === "failure" || ci === "running" ? ci : "not_started"
  const badge = CI_BADGES[state]
  return (
    <Badge variant="outline" className={badge.className} title={`CI: ${ci ?? "unknown"}`}>
      {badge.symbol}
    </Badge>
  )
}
