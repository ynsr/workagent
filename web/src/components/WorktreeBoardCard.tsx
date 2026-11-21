import { Link, useInRouterContext } from "react-router-dom"
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  FolderOpen,
  GitBranch,
  GitPullRequest,
  Loader2,
  MessageSquare,
  RefreshCw,
  Terminal,
  Trash2,
  Wrench,
} from "lucide-react"
import type { KanbanColumn, WorktreeEntry } from "@/lib/api"
import type { StatusTableActions } from "@/components/StatusTable"
import { Button } from "@/components/ui/button"

export interface WorktreeBoardCardProps {
  worktreeKey: string
  entry: WorktreeEntry
  actions: StatusTableActions
  column?: KanbanColumn
}

function harnessBadgeLabel(harness?: string): string | null {
  const raw = (harness ?? "").trim()
  if (!raw) return null
  const name = raw.split(/\s+/)[0] ?? ""
  return name ? name.toUpperCase() : null
}

function ciBadge(ci?: string): { icon: typeof CheckCircle2; label: string; className: string } | null {
  if (!ci) return null
  if (ci === "success") {
    return {
      icon: CheckCircle2,
      label: "passing",
      className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    }
  }
  if (ci === "failure") {
    return {
      icon: AlertCircle,
      label: "failed",
      className: "border-destructive/40 bg-destructive/10 text-destructive",
    }
  }
  if (ci === "running") {
    return {
      icon: Loader2,
      label: "running",
      className: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    }
  }
  return {
    icon: Clock,
    label: ci,
    className: "border-border bg-muted/50 text-muted-foreground",
  }
}

export function WorktreeBoardCard({
  worktreeKey,
  entry,
  actions,
  column,
}: WorktreeBoardCardProps) {
  const inRouter = useInRouterContext()
  const workspaceUrl = `/worktrees/${encodeURIComponent(worktreeKey)}`
  const lane = column ?? entry.kanban_column ?? "in_review"
  const isNeedsYou = lane === "needs_you"
  const isWorking = lane === "working"
  const harnessBadge = harnessBadgeLabel(entry.harness)
  const ci = ciBadge(entry.ci)
  const unresolved = entry.reviews_detail?.unresolved ?? 0
  const behind = entry.commits_detail?.behind ?? 0
  const ahead = entry.commits_detail?.ahead ?? 0
  const relatedCount = entry.related_repos?.length ?? 0
  const title = entry.pr_detail?.title || entry.issue || worktreeKey
  const displayStatus = entry.display_status || (isWorking ? "Working" : "In review")
  const hasProdtest = Boolean(entry.prodtest && entry.prodtest !== "-")

  return (
    <article
      data-testid={`board-card-${worktreeKey}`}
      data-kanban-column={lane}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) {
          e.preventDefault()
          const link = e.currentTarget.querySelector<HTMLAnchorElement>("a[data-workspace-link]")
          link?.click()
        }
      }}
      className={[
        "group relative flex flex-col gap-2.5 rounded-lg border bg-card p-3 text-card-foreground shadow-xs transition-colors hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        isNeedsYou ? "border-[#f06445]/70 ring-1 ring-[#f06445]/25" : "border-border",
      ].join(" ")}
    >
      {/* Header: harness badge + key/title + quick hover actions */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            {harnessBadge ? (
              <span
                className="inline-flex items-center rounded bg-blue-500/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-blue-600 dark:text-blue-400"
                title={`Live harness: ${entry.harness}`}
              >
                {harnessBadge}
              </span>
            ) : null}
            {inRouter ? (
              <Link
                to={workspaceUrl}
                data-workspace-link
                className="truncate font-mono text-xs font-medium text-muted-foreground hover:text-foreground hover:underline"
              >
                {worktreeKey}
              </Link>
            ) : (
              <a
                href={workspaceUrl}
                data-workspace-link
                className="truncate font-mono text-xs font-medium text-muted-foreground hover:text-foreground hover:underline"
              >
                {worktreeKey}
              </a>
            )}
          </div>
          {inRouter ? (
            <Link
              to={workspaceUrl}
              className="mt-0.5 line-clamp-2 block text-sm font-medium leading-snug text-foreground hover:underline"
            >
              {title}
            </Link>
          ) : (
            <a
              href={workspaceUrl}
              className="mt-0.5 line-clamp-2 block text-sm font-medium leading-snug text-foreground hover:underline"
            >
              {title}
            </a>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-0.5 opacity-80 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          {actions.onSync ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              title="Sync"
              aria-label={`Sync ${worktreeKey}`}
              onClick={() => actions.onSync?.(worktreeKey)}
            >
              <RefreshCw className="size-3.5" aria-hidden />
            </Button>
          ) : null}
          {actions.onReview ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              title="Review"
              aria-label={`Review ${worktreeKey}`}
              onClick={() => actions.onReview?.(worktreeKey)}
            >
              <GitPullRequest className="size-3.5" aria-hidden />
            </Button>
          ) : null}
          {unresolved > 0 && actions.onFixComments ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7"
              title="Fix comments"
              aria-label={`Fix comments ${worktreeKey}`}
              onClick={() => actions.onFixComments?.(worktreeKey)}
            >
              <Wrench className="size-3.5" aria-hidden />
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            title="Open runs"
            aria-label={`Open runs ${worktreeKey}`}
            onClick={() => actions.onOpenRun(worktreeKey)}
          >
            <Terminal className="size-3.5" aria-hidden />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            title="Open worktree"
            aria-label={`Open worktree ${worktreeKey}`}
            onClick={() => actions.onOpenWorktree(worktreeKey)}
          >
            <FolderOpen className="size-3.5" aria-hidden />
          </Button>
          {actions.onCleanup ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7 text-muted-foreground hover:text-destructive"
              title="Cleanup"
              aria-label={`Cleanup ${worktreeKey}`}
              onClick={() => actions.onCleanup?.(worktreeKey)}
            >
              <Trash2 className="size-3.5" aria-hidden />
            </Button>
          ) : null}
        </div>
      </div>

      {/* Branch + related repos */}
      {entry.branch ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <GitBranch className="size-3 shrink-0" aria-hidden />
          <span className="truncate font-mono">{entry.branch}</span>
          {relatedCount > 0 ? (
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium">
              +{relatedCount} related
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Metric pills: PR, CI, Reviews, Behind/Ahead, Prodtest */}
      <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
        {entry.pr_detail ? (
          <a
            href={entry.pr_detail.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/40 px-2 py-0.5 font-medium hover:bg-muted"
          >
            <GitPullRequest className="size-3" aria-hidden />
            <span>#{entry.pr_detail.number}</span>
            <span className="text-muted-foreground">{entry.pr_detail.state}</span>
          </a>
        ) : null}

        {ci ? (
          <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium ${ci.className}`}>
            <ci.icon className={`size-3 ${entry.ci === "running" ? "animate-spin motion-reduce:animate-none" : ""}`} aria-hidden />
            <span className="sr-only">CI status: </span>
            <span>{ci.label}</span>
          </span>
        ) : null}

        {entry.reviews_detail ? (
          <span
            className={[
              "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-medium",
              unresolved > 0
                ? "border-[#f06445]/40 bg-[#f06445]/10 text-[#f06445]"
                : "border-border bg-muted/40 text-muted-foreground",
            ].join(" ")}
          >
            <MessageSquare className="size-3" aria-hidden />
            <span className="sr-only">Unresolved review comments: </span>
            <span>{unresolved}</span>
          </span>
        ) : null}

        {entry.commits_detail ? (
          <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 font-mono text-muted-foreground">
            <span className="sr-only">Behind and ahead commits: </span>
            {behind}|{ahead}
          </span>
        ) : null}

        {hasProdtest ? (
          <span className="inline-flex items-center rounded-full border border-border bg-muted/40 px-2 py-0.5 text-muted-foreground">
            <span className="sr-only">Prodtest status: </span>
            PT: {entry.prodtest}
          </span>
        ) : null}
      </div>

      {/* Derived display_status footer */}
      <div className="flex items-center justify-between gap-2 border-t border-border/60 pt-2 text-xs">
        <span
          className={[
            "inline-flex min-w-0 items-center gap-1.5 font-medium",
            isNeedsYou
              ? "text-[#f06445]"
              : isWorking
                ? "text-blue-600 dark:text-blue-400"
                : lane === "ready_to_merge"
                  ? "text-emerald-600 dark:text-emerald-400"
                  : "text-muted-foreground",
          ].join(" ")}
        >
          {isWorking ? (
            <Loader2 className="size-3 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden />
          ) : null}
          <span className="truncate">{displayStatus}</span>
        </span>
      </div>
    </article>
  )
}
