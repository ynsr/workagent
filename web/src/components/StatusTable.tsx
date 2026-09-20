import type { ReactNode } from "react"
import { FolderOpen, RefreshCw, Rocket, Trash2 } from "lucide-react"
import type { SessionMap } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { PrBadge } from "@/components/StateBadge"
import { cn } from "@/lib/utils"

export interface StatusTableActions {
  onSync?: (key: string) => void
  onCleanup?: (key: string) => void
  onCopyPath: (key: string) => void
  onOpenRun: (key: string) => void
}

function ActionIcon({
  title,
  onClick,
  children,
  destructive,
}: {
  title: string
  onClick: () => void
  children: ReactNode
  destructive?: boolean
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={title}
      title={title}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className={cn("size-11 text-muted-foreground hover:text-foreground", destructive && "hover:bg-destructive/10 hover:text-destructive")}
    >
      {children}
    </Button>
  )
}

function RowActions({
  sessionKey,
  actions,
}: {
  sessionKey: string
  actions: StatusTableActions
}) {
  return (
    <div className="flex items-center justify-end gap-0.5">
      {actions.onSync ? (
        <ActionIcon title={`Sync ${sessionKey}`} onClick={() => actions.onSync?.(sessionKey)}>
          <RefreshCw aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onCleanup ? (
        <ActionIcon
          title={`Cleanup ${sessionKey}`}
          onClick={() => actions.onCleanup?.(sessionKey)}
          destructive
        >
          <Trash2 aria-hidden />
        </ActionIcon>
      ) : null}
      <ActionIcon
        title={`Copy worktree path of ${sessionKey}`}
        onClick={() => actions.onCopyPath(sessionKey)}
      >
        <FolderOpen aria-hidden />
      </ActionIcon>
      <ActionIcon
        title={`Open runs for ${sessionKey}`}
        onClick={() => actions.onOpenRun(sessionKey)}
      >
        <Rocket aria-hidden />
      </ActionIcon>
    </div>
  )
}

function CommitsCell({ entry }: { entry: SessionMap[string] }) {
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

/**
 * Sessions table (GET /api/status or /api/links sessions).
 * Table at ≥640px, cards below.
 */
export function StatusTable({
  sessions,
  actions,
  showWorktree = false,
  className,
}: {
  sessions: SessionMap
  actions: StatusTableActions
  showWorktree?: boolean
  className?: string
}) {
  const keys = Object.keys(sessions).sort()
  return (
    <div className={className}>
      {/* Desktop table */}
      <div className="hidden sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Session</TableHead>
              <TableHead>Branch</TableHead>
              <TableHead className="w-20">Behind|Ahead</TableHead>
              <TableHead>PR / MR</TableHead>
              {showWorktree ? <TableHead>Worktree</TableHead> : null}
              <TableHead className="text-right pr-2">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((key) => {
              const entry = sessions[key]
              if (!entry) return null
              return (
                <TableRow key={key}>
                  <TableCell className="font-medium">
                    <SessionKeyLink sessionKey={key} entry={entry} />
                  </TableCell>
                  <TableCell className="max-w-52 truncate font-mono text-[13px]" title={entry.branch}>
                    {entry.branch ?? "—"}
                  </TableCell>
                  <TableCell>
                    <CommitsCell entry={entry} />
                  </TableCell>
                  <TableCell>
                    <div className="flex min-w-0 items-center gap-2">
                      <PrBadge pr={entry.pr_detail ?? null} />
                      <span className="truncate text-muted-foreground" title={entry.pr}>
                        {entry.pr_detail ? entry.pr_detail.title : entry.pr ?? "—"}
                      </span>
                    </div>
                  </TableCell>
                  {showWorktree ? (
                    <TableCell className="max-w-52 truncate font-mono text-[13px]" title={entry.worktree}>
                      {entry.worktree ?? "—"}
                    </TableCell>
                  ) : null}
                  <TableCell className="pr-1">
                    <RowActions sessionKey={key} actions={actions} />
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      {/* Mobile cards */}
      <div className="space-y-3 sm:hidden">
        {keys.map((key) => {
          const entry = sessions[key]
          if (!entry) return null
          return (
            <div key={key} className="rounded-xl border bg-card p-4">
              <div className="flex items-start justify-between gap-2">
                <SessionKeyLink sessionKey={key} entry={entry} />
                <PrBadge pr={entry.pr_detail ?? null} />
              </div>
              <dl className="mt-3 space-y-1.5 text-sm">
                <div className="flex items-baseline gap-2">
                  <dt className="w-16 shrink-0 text-xs text-muted-foreground">Branch</dt>
                  <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.branch}>
                    {entry.branch ?? "—"}
                  </dd>
                </div>
                <div className="flex items-baseline gap-2">
                  <dt className="w-16 shrink-0 text-xs text-muted-foreground">Commits</dt>
                  <dd>
                    <CommitsCell entry={entry} />
                  </dd>
                </div>
                {showWorktree && entry.worktree ? (
                  <div className="flex items-baseline gap-2">
                    <dt className="w-16 shrink-0 text-xs text-muted-foreground">Worktree</dt>
                    <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.worktree}>
                      {entry.worktree}
                    </dd>
                  </div>
                ) : null}
                {entry.pr && !entry.pr_detail ? (
                  <div className="flex items-baseline gap-2">
                    <dt className="w-16 shrink-0 text-xs text-muted-foreground">PR</dt>
                    <dd className="min-w-0 truncate">{entry.pr}</dd>
                  </div>
                ) : null}
              </dl>
              <div className="mt-3 border-t pt-1">
                <RowActions sessionKey={key} actions={actions} />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function SessionKeyLink({
  sessionKey,
  entry,
}: {
  sessionKey: string
  entry: SessionMap[string]
}) {
  const inner = (
    <span className="font-mono text-[13px] font-semibold">{sessionKey}</span>
  )
  if (!entry.issue_url) return inner
  return (
    <a
      href={entry.issue_url}
      target="_blank"
      rel="noreferrer"
      className="underline-offset-2 hover:underline"
      onClick={(e) => e.stopPropagation()}
    >
      {inner}
    </a>
  )
}

