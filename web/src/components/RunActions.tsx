import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Copy, SquareTerminal } from "lucide-react"
import { Button } from "@/components/ui/button"
import { errorText } from "@/components/StatusFeedback"
import { copyToClipboard, resumeCommand } from "@/lib/format"
import { queryKeys, useCreateRun, useResumeRun, useResumeSession } from "@/lib/queries"
import type { RunCommand } from "@/lib/api"
import type { ConfirmDetailRow } from "@/lib/confirm"
import type { OptOutAction } from "@/lib/settings"
import { useConfirm } from "@/lib/confirm"

/** Copy-to-clipboard with transient "Copied" feedback (replaces the three
 * ad-hoc `copied` states in CandidatesCard/Runs). */
export function useCopyFeedback(timeoutMs = 1200) {
  const [copied, setCopied] = useState(false)
  async function copy(text: string, label = "Copied") {
    try {
      await copyToClipboard(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), timeoutMs)
      toast.success(label)
    } catch (err) {
      toast.error(errorText(err))
    }
  }
  return { copied, copy }
}

/** Resume-in-terminal + copy-resume-command buttons (shared by the Runs page
 * icon variant and the RunDetail/Sessions outline variant). Exactly one of
 * `runId` (live run resume) or `sessionId` (persisted session resume) applies.
 */
export function SessionResumeActions({
  runId,
  sessionId,
  worktree,
  sessionFile,
  variant = "icon",
}: {
  runId?: string
  sessionId?: string
  worktree: string
  sessionFile: string
  variant?: "icon" | "outline"
}) {
  const resumeRun = useResumeRun()
  const resumeSession = useResumeSession()
  const label = runId ? `run ${runId}` : `session ${sessionId}`
  const icon = variant === "icon"
  return (
    <>
      <Button
        variant={icon ? "ghost" : "outline"}
        size={icon ? "icon" : "sm"}
        aria-label={`Resume session for ${label} in terminal`}
        title="Resume session in terminal"
        disabled={resumeRun.isPending || resumeSession.isPending}
        onClick={(e) => {
          if (icon) e.preventDefault()
          const done = () => toast.success("Terminal opened on the session")
          const fail = (err: unknown) => toast.error(errorText(err))
          if (runId) void resumeRun.mutateAsync(runId).then(done).catch(fail)
          else if (sessionId) void resumeSession.mutateAsync(sessionId).then(done).catch(fail)
        }}
        className={icon ? "size-9 text-muted-foreground hover:text-foreground" : undefined}
      >
        <SquareTerminal aria-hidden /> {icon ? null : "Resume in terminal"}
      </Button>
      <Button
        variant={icon ? "ghost" : "outline"}
        size={icon ? "icon" : "sm"}
        aria-label={`Copy resume command for ${label}`}
        title="Copy resume command"
        disabled={!worktree || !sessionFile}
        onClick={(e) => {
          if (icon) e.preventDefault()
          void copyToClipboard(resumeCommand(worktree, sessionFile))
            .then(() => toast.success("Resume command copied"))
            .catch((err: unknown) => toast.error(errorText(err)))
        }}
        className={icon ? "size-9 text-muted-foreground hover:text-foreground" : undefined}
      >
        <Copy aria-hidden /> {icon ? null : "Copy resume command"}
      </Button>
    </>
  )
}

/** Confirm → createRun → success toast with "View run" → invalidate
 * links/status (shared by CandidatesCard/Launch/Dashboard/Links/RunDetail). */
export function useConfirmedRun() {
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const qc = useQueryClient()
  const navigate = useNavigate()
  return async function run(input: {
    action: OptOutAction | null
    title: string
    description: string
    warning?: string
    confirmLabel: string
    details?: ConfirmDetailRow[]
    command: RunCommand
    args: string[]
    successLabel?: string
    navigateToRun?: boolean
  }) {
    const ok = await confirm({
      action: input.action,
      title: input.title,
      description: input.description,
      warning: input.warning,
      confirmLabel: input.confirmLabel,
      details: input.details,
    })
    if (!ok) return null
    try {
      const { run_id } = await createRun.mutateAsync({
        command: input.command,
        args: input.args,
        confirm: true,
      })
      const label = input.successLabel ?? "started"
      toast.success(`${input.title} ${label}`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      void qc.invalidateQueries({ queryKey: queryKeys.links })
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
      return run_id
    } catch (err) {
      toast.error(errorText(err))
      return null
    }
  }
}
