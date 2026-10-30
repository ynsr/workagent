import type { ReactNode } from "react"
import { GitPullRequest, History, Play, RefreshCw, Rocket, Square, Trash2, Undo2, Wrench } from "lucide-react"
import type { WorktreeMap } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useSessions } from "@/lib/queries"
import { SessionResumeActions } from "@/components/RunActions"
import type { StatusTableActions } from "@/components/StatusTable"

export function ActionIcon({
  title,
  onClick,
  children,
  destructive,
  disabled,
}: {
  title: string
  onClick: () => void
  children: ReactNode
  destructive?: boolean
  disabled?: boolean
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={title}
      title={title}
      disabled={disabled}
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

export function RowActions({
  worktreeKey,
  entry,
  actions,
  overlay = false,
  showRuns = true,
}: {
  worktreeKey: string
  entry: WorktreeMap[string]
  actions: StatusTableActions
  detailOpen?: boolean
  onToggleDetail?: () => void
  networkExposed?: boolean
  overlay?: boolean
  /** False hides the Runs/Sessions buttons (StatusTable rows — they live in the details dialog). */
  showRuns?: boolean
}) {
  const invalid = entry.wt_valid === false
  const lastSession = useCopyResumeSession(worktreeKey, entry.worktree)
  return (
    <div className={overlay ? "flex items-center justify-end gap-0.5" : "flex items-center justify-end gap-0.5 opacity-100 transition-opacity focus-within:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:hover:opacity-100"}>
      {actions.onSync ? (
        <ActionIcon title={`Sync ${worktreeKey}`} onClick={() => actions.onSync?.(worktreeKey)}>
          <RefreshCw aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onReview ? (
        <ActionIcon
          title={`Review ${worktreeKey}`}
          onClick={() => actions.onReview?.(worktreeKey)}
        >
          <GitPullRequest aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onFixComments ? (
        <ActionIcon
          title={`Fix PR comments of ${worktreeKey}`}
          onClick={() => actions.onFixComments?.(worktreeKey)}
        >
          <Wrench aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onCleanup ? (
        <ActionIcon
          title={invalid ? `Delete invalid worktree ${worktreeKey}` : `Cleanup ${worktreeKey}`}
          onClick={() => actions.onCleanup?.(worktreeKey)}
          destructive
        >
          <Trash2 aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onStopTerminal && entry.harness?.startsWith("terminal ") ? (
        <ActionIcon
          title={`Stop terminal agent ${entry.harness} on ${worktreeKey}`}
          onClick={() => actions.onStopTerminal?.(worktreeKey)}
          destructive
        >
          <Square aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onReactivate ? (
        <ActionIcon
          title={`Reactivate ${worktreeKey}`}
          onClick={() => actions.onReactivate?.(worktreeKey)}
        >
          <Play aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onUndelete ? (
        <ActionIcon
          title={`Undelete ${worktreeKey} (restore soft-deleted entry)`}
          onClick={() => actions.onUndelete?.(worktreeKey)}
        >
          <Undo2 aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onDestroyDeleted ? (
        <ActionIcon
          title={`Destroy ${worktreeKey} permanently (worktree dir + local branch + row; remote branches untouched)`}
          onClick={() => actions.onDestroyDeleted?.(worktreeKey)}
          destructive
        >
          <Trash2 aria-hidden />
        </ActionIcon>
      ) : null}
      {showRuns ? (
        <ActionIcon
          title={`Open runs for ${worktreeKey}`}
          onClick={() => actions.onOpenRun(worktreeKey)}
        >
          <Rocket aria-hidden />
        </ActionIcon>
      ) : null}
      {showRuns && actions.onOpenSessions ? (
        <ActionIcon
          title={`Worktree sessions for ${worktreeKey}`}
          onClick={() => actions.onOpenSessions?.(worktreeKey)}
        >
          <History aria-hidden />
        </ActionIcon>
      ) : null}
      {lastSession && entry.worktree ? (
        <SessionResumeActions
          sessionId={lastSession.id}
          worktree={entry.worktree}
          sessionFile={lastSession.file_path}
          variant="icon"
        />
      ) : (
        <ActionIcon
          title={`Resume session: no resumable session for ${worktreeKey} yet`}
          disabled
          onClick={() => undefined}
        >
          <History aria-hidden />
        </ActionIcon>
      )}
    </div>
  )
}

/** Latest non-running session for a worktree key (any type — start, review,
 * sync, fix_comments — matching the Sessions page; `/api/sessions` is
 * newest-first, so the first match wins). Null while loading or when no
 * resumable session exists. */
function useCopyResumeSession(worktreeKey: string, worktree?: string): { id: string; file_path: string } | null {
  const { data } = useSessions()
  const sessions = data?.sessions
  if (!sessions || !worktree?.trim()) return null
  const match = sessions.find(
    (row) =>
      row.worktree_ref === worktreeKey &&
      row.state !== "running" &&
      row.file_path?.trim(),
  )
  if (!match?.file_path) return null
  return { id: match.id, file_path: match.file_path }
}
