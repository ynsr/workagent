import type { ReactNode } from "react"
import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { FileCheck, GitPullRequest, History, Play, RefreshCw, Rocket, Square, Trash2, Undo2, Workflow, Wrench, Zap } from "lucide-react"
import { FlowWizard } from "@/components/FlowWizard"
import type { FlowTemplateRow, WorktreeMap } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useActions, useCreateRun, useFlowTemplates, useSessions } from "@/lib/queries"
import { SessionResumeActions } from "@/components/RunActions"
import type { StatusTableActions } from "@/components/StatusTable"
import { errorText } from "@/components/StatusFeedback"

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

/** Dedicated overlay buttons for flagged templates/actions on one worktree row. */
function DedicatedOverlay({ worktreeKey }: { worktreeKey: string }) {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const { data: flowData } = useFlowTemplates()
  const { data: actionData } = useActions()
  const sortedTemplates = [...(flowData?.templates ?? [])].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
  )
  const sortedActions = [...(actionData?.actions ?? [])].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
  )
  const flaggedTemplates = sortedTemplates.filter((t) => t.dedicated_overlay)
  const flaggedActions = sortedActions.filter((a) => a.dedicated_overlay)
  const [wizard, setWizard] = useState<FlowTemplateRow | null>(null)
  if (flaggedTemplates.length === 0 && flaggedActions.length === 0) return null
  async function handleAction(label: string, name: string) {
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "action",
        args: ["run", label, worktreeKey, "--launch", "--yes"],
        confirm: false,
      })
      toast.success(`Action ${name} started on ${worktreeKey}`, {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(`${worktreeKey}: ${errorText(err)}`)
    }
  }
  return (
    <>
      {flaggedTemplates.map((t) => (
        <ActionIcon key={`flow-${t.label}`} title={`Run flow ${t.name} on ${worktreeKey}`} onClick={() => setWizard(t)}>
          <Workflow aria-hidden />
        </ActionIcon>
      ))}
      {flaggedActions.map((a) => (
        <ActionIcon key={`action-${a.label}`} title={`Run action ${a.name} on ${worktreeKey}`} onClick={() => void handleAction(a.label, a.name)}>
          <Zap aria-hidden />
        </ActionIcon>
      ))}
      {wizard ? (
        <FlowWizard template={wizard} refs={[worktreeKey]} onClose={() => setWizard(null)} />
      ) : null}
    </>
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
      {overlay ? <DedicatedOverlay worktreeKey={worktreeKey} /> : null}
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
      {actions.onGenerateProdtest ? (
        <ActionIcon
          title={`Generate prodtest plan for ${worktreeKey}`}
          onClick={() => actions.onGenerateProdtest?.(worktreeKey)}
        >
          <FileCheck aria-hidden />
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
