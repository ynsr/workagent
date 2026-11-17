import type { ReactNode } from "react"
import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { FileCheck, GitPullRequest, History, Play, RefreshCw, Rocket, Square, Trash2, Undo2, Workflow, Wrench, Zap } from "lucide-react"
import { FlowWizard } from "@/components/FlowWizard"
import { ImmediateRunDialog, runsImmediately } from "@/components/ImmediateRunForm"
import type { ActionRow, FlowTemplateRow, WorktreeMap } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useActions, useCreateRun, useFlowTemplates, useRuns } from "@/lib/queries"
import { RunResumeActions } from "@/components/RunActions"
import type { StatusTableActions } from "@/components/StatusTable"
import { errorText } from "@/components/StatusFeedback"
import { actionRunModeOf, buildImmediateActionArgs, isRunImmediateAction } from "@/lib/messageRunConfig"

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
function DedicatedOverlay({ worktreeKey, hidden }: { worktreeKey: string; hidden?: Set<string> }) {
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
  const groupHidden = hidden?.has("dedicated") ?? false
  const visTemplates = groupHidden ? [] : flaggedTemplates.filter((t) => !hidden?.has(`flow:${t.label}`))
  const visActions = groupHidden ? [] : flaggedActions.filter((a) => !hidden?.has(`action:${a.label}`))
  const [wizard, setWizard] = useState<FlowTemplateRow | null>(null)
  const [actionForm, setActionForm] = useState<ActionRow | null>(null)
  if (visTemplates.length === 0 && visActions.length === 0) return null
  async function handleAction(action: ActionRow) {
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "action",
        args: buildImmediateActionArgs(action.label, worktreeKey, actionRunModeOf(action)),
        confirm: true,
      })
      toast.success(`Action ${action.name} started on ${worktreeKey}`, {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(`${worktreeKey}: ${errorText(err)}`)
    }
  }


  async function handleTemplateRun(template: FlowTemplateRow) {
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "flow",
        args: ["run", template.label, worktreeKey, "--yes"],
        confirm: true,
      })
      toast.success(`Flow ${template.name} started on ${worktreeKey}`, {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(`${worktreeKey}: ${errorText(err)}`)
    }
  }

  function handleTemplateClick(template: FlowTemplateRow) {
    // run_immediately templates launch straight away (standard confirm); the
    // wizard stays the form for everything else.
    if (runsImmediately("flow", template)) void handleTemplateRun(template)
    else setWizard(template)
  }

  function handleActionClick(action: ActionRow) {
    // run_immediately actions launch straight away (standard confirm); the
    // shared form opens first for everything else.
    if (isRunImmediateAction(action)) void handleAction(action)
    else setActionForm(action)
  }
  return (
    <>
      {visTemplates.map((t) => (
        <ActionIcon key={`flow-${t.label}`} title={`Run flow ${t.name} on ${worktreeKey}`} onClick={() => handleTemplateClick(t)}>
          <Workflow aria-hidden />
        </ActionIcon>
      ))}
      {visActions.map((a) => (
        <ActionIcon key={`action-${a.label}`} title={isRunImmediateAction(a) ? `Run action ${a.name} on ${worktreeKey}` : `Run action ${a.name} on ${worktreeKey}…`} onClick={() => handleActionClick(a)}>
          <Zap aria-hidden />
        </ActionIcon>
      ))}
      {wizard ? (
        <FlowWizard template={wizard} refs={[worktreeKey]} onClose={() => setWizard(null)} />
      ) : null}
      {actionForm ? (
        <ImmediateRunDialog kind="action" category="worktree" lockCategory preset={actionForm.label} worktreeRefs={[worktreeKey]} onClose={() => setActionForm(null)} />
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
  hidden,
}: {
  worktreeKey: string
  entry: WorktreeMap[string]
  actions: StatusTableActions
  detailOpen?: boolean
  onToggleDetail?: () => void
  networkExposed?: boolean
  overlay?: boolean
  /** False hides the run buttons (StatusTable rows — they live in the details dialog). */
  showRuns?: boolean
  /** Overlay ids hidden for this row's repo (`{button_id: false}` map). Detail panels pass nothing. */
  hidden?: Set<string>
}) {
  const invalid = entry.wt_valid === false
  const lastRun = useLatestTranscriptRun(entry.worktree)
  // Overlays and StatusTable rows filter by the row repo's hidden set;
  // detail panels (WorktreeDetail) pass nothing → everything visible.
  const hide = hidden ?? new Set<string>()
  return (
    <div className={overlay ? "flex items-center justify-end gap-0.5" : "flex items-center justify-end gap-0.5 opacity-100 transition-opacity focus-within:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:hover:opacity-100"}>
      {overlay ? <DedicatedOverlay worktreeKey={worktreeKey} hidden={hide} /> : null}
      {actions.onSync && !hide.has("sync") ? (
        <ActionIcon title={`Sync ${worktreeKey}`} onClick={() => actions.onSync?.(worktreeKey)}>
          <RefreshCw aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onReview && !hide.has("review") ? (
        <ActionIcon
          title={`Review ${worktreeKey}`}
          onClick={() => actions.onReview?.(worktreeKey)}
        >
          <GitPullRequest aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onFixComments && !hide.has("fix-comments") ? (
        <ActionIcon
          title={`Fix PR comments of ${worktreeKey}`}
          onClick={() => actions.onFixComments?.(worktreeKey)}
        >
          <Wrench aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onGenerateProdtest && !hide.has("prodtest") ? (
        <ActionIcon
          title={`Generate prodtest plan for ${worktreeKey}`}
          onClick={() => actions.onGenerateProdtest?.(worktreeKey)}
        >
          <FileCheck aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onAdhoc && !hide.has("adhoc") ? (
        <ActionIcon
          title={`Run adhoc prompt on ${worktreeKey}`}
          onClick={() => actions.onAdhoc?.(worktreeKey)}
        >
          <Zap aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onCleanup && !hide.has("cleanup") ? (
        <ActionIcon
          title={invalid ? `Delete invalid worktree ${worktreeKey}` : `Cleanup ${worktreeKey}`}
          onClick={() => actions.onCleanup?.(worktreeKey)}
          destructive
        >
          <Trash2 aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onStopTerminal && entry.harness?.startsWith("terminal ") && !hide.has("stop-terminal") ? (
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
      {showRuns && !hide.has("open-runs") ? (
        <ActionIcon
          title={`Open runs for ${worktreeKey}`}
          onClick={() => actions.onOpenRun(worktreeKey)}
        >
          <Rocket aria-hidden />
        </ActionIcon>
      ) : null}
      {showRuns && actions.onOpenFlows && !hide.has("open-flows") ? (
        <ActionIcon
          title={`Open flows for ${worktreeKey}`}
          onClick={() => actions.onOpenFlows?.(worktreeKey)}
        >
          <Workflow aria-hidden />
        </ActionIcon>
      ) : null}
      {!hide.has("resume-session") ? (
        lastRun && entry.worktree ? (
          <RunResumeActions
            runId={lastRun.id}
            worktree={entry.worktree}
            sessionFile={lastRun.sessionFile}
            transcriptReady={lastRun.transcriptReady}
            harnessName={lastRun.harnessName}
            resumeCommand={lastRun.resumeCommand}
            variant="icon"
          />
        ) : (
          <ActionIcon
            title={`Resume transcript: no resumable transcript for ${worktreeKey} yet`}
            disabled
            onClick={() => undefined}
          >
            <History aria-hidden />
          </ActionIcon>
        )
      ) : null}
    </div>
  )
}

/** Newest run that still owns a resumable transcript for this worktree path
 * (Runs own transcripts; live and persisted rows share GET /api/runs, so sort
 * by createdAt instead of trusting the merged order). Null while loading or
 * when the worktree has no finished transcript run. */
function useLatestTranscriptRun(worktree?: string): {
  id: string
  sessionFile: string
  transcriptReady: boolean
  harnessName?: string
  resumeCommand?: string
} | null {
  const { data } = useRuns()
  if (!data || !worktree?.trim()) return null
  const match = data
    .filter(
      (run) =>
        run.worktree === worktree &&
        run.state !== "running" &&
        run.session_file?.trim(),
    )
    .sort((a, b) => b.created - a.created)[0]
  return match
    ? {
        id: match.id,
        sessionFile: match.session_file,
        transcriptReady: match.transcript_ready,
        harnessName: match.harness_name,
        resumeCommand: match.resume_command,
      }
    : null
}
