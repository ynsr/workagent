import { MessagesSquare } from "lucide-react"
import type { WorktreeMap } from "@/lib/api"
import { Badge } from "@/components/ui/badge"

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
/** Reviews badge `<reviews>|<resolved> of <total threads>`; "—" when no PR or lookup failed. */
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
      <span>
        {rd.reviews}|{label}
      </span>
    </Badge>
  )
}

/** First-seen stamp → locale date; missing/unparseable → "—". */
export function formatAdded(addedAt: string | undefined): string {
  if (!addedAt) return "—"
  const d = new Date(addedAt)
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString()
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

