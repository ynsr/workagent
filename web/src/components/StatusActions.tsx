import type { ReactNode } from "react"
import { FolderOpen, GitPullRequest, History, Info, Play, RefreshCw, Rocket, Trash2, Wrench } from "lucide-react"
import type { WorktreeMap } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
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
  detailOpen,
  onToggleDetail,
  networkExposed,
  overlay = false,
}: {
  worktreeKey: string
  entry: WorktreeMap[string]
  actions: StatusTableActions
  detailOpen: boolean
  onToggleDetail: () => void
  networkExposed: boolean
  overlay?: boolean
}) {
  const invalid = entry.wt_valid === false
  return (
    <div className={overlay ? "flex items-center justify-end gap-0.5" : "flex items-center justify-end gap-0.5 opacity-100 transition-opacity focus-within:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:hover:opacity-100"}>
      <ActionIcon
        title={detailOpen ? `Hide details of ${worktreeKey}` : `Details of ${worktreeKey}`}
        onClick={onToggleDetail}
      >
        <Info aria-hidden />
      </ActionIcon>
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
      {actions.onReactivate ? (
        <ActionIcon
          title={`Reactivate ${worktreeKey}`}
          onClick={() => actions.onReactivate?.(worktreeKey)}
        >
          <Play aria-hidden />
        </ActionIcon>
      ) : null}
      <ActionIcon
        title={
          networkExposed
            ? `Cannot open ${worktreeKey}: disabled while the server is network-exposed`
            : `Open worktree folder of ${worktreeKey}`
        }
        onClick={() => actions.onOpenWorktree(worktreeKey)}
        disabled={networkExposed}
      >
        <FolderOpen aria-hidden />
      </ActionIcon>
      <ActionIcon
        title={`Open runs for ${worktreeKey}`}
        onClick={() => actions.onOpenRun(worktreeKey)}
      >
        <Rocket aria-hidden />
      </ActionIcon>
      {actions.onOpenSessions ? (
        <ActionIcon
          title={`Worktree sessions for ${worktreeKey}`}
          onClick={() => actions.onOpenSessions?.(worktreeKey)}
        >
          <History aria-hidden />
        </ActionIcon>
      ) : null}
    </div>
  )
}

