import { useState, type ReactNode } from "react"
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
  transcript = "ready",
  variant = "icon",
}: {
  runId?: string
  sessionId?: string
  worktree: string
  sessionFile: string
  /** "missing" when the backend reports no resumable transcript (detail view). */
  transcript?: string
  variant?: "icon" | "outline"
}) {
  const resumeRun = useResumeRun()
  const resumeSession = useResumeSession()
  const label = runId ? `run ${runId}` : `session ${sessionId}`
  // Copy button mirrors the backend rule: --resume only for a known-good
  // transcript, else fresh via --session-dir (omp v18+ rejects --resume
  // on missing/empty files). Row lists assume ready; the detail view
  // passes transcript="missing" through.
  const cmd = resumeCommand(worktree, sessionFile, transcript !== "missing")
  const icon = variant === "icon"
  const resumePending = resumeRun.isPending || resumeSession.isPending
  const canResume = Boolean(!resumePending && worktree && sessionFile)
  const resume = (e: React.MouseEvent) => {
    if (icon) e.preventDefault()
    const done = () => toast.success("Terminal opened on the session")
    const fail = (err: unknown) => toast.error(errorText(err))
    if (runId) void resumeRun.mutateAsync(runId).then(done).catch(fail)
    else if (sessionId) void resumeSession.mutateAsync(sessionId).then(done).catch(fail)
  }
  const copy = (e: React.MouseEvent) => {
    if (icon) e.preventDefault()
    void copyToClipboard(cmd)
      .then(() => toast.success("Resume command copied"))
      .catch((err: unknown) => toast.error(errorText(err)))
  }
  return (
    <div className="inline-flex items-stretch" role="group" aria-label={`Resume ${label}`}>
      <Button
        variant={icon ? "ghost" : "outline"}
        size={icon ? "icon" : "sm"}
        aria-label={`Resume session for ${label} in terminal`}
        title={cmd || `Resume ${label} in terminal (nothing to resume yet)`}
        disabled={!canResume}
        onClick={resume}
        className={icon ? "size-9 rounded-r-none text-muted-foreground hover:text-foreground" : "rounded-r-none"}
      >
        <SquareTerminal aria-hidden /> {icon ? null : "Resume in terminal"}
      </Button>
      <Button
        variant={icon ? "ghost" : "outline"}
        size={icon ? "icon" : "sm"}
        aria-label={`Copy resume command for ${label}`}
        title={cmd || `Copy resume command for ${label} (nothing to copy yet)`}
        disabled={!canResume}
        onClick={copy}
        className={icon ? "size-9 rounded-l-none border-l-0 text-muted-foreground hover:text-foreground" : "rounded-l-none border-l-0"}
      >
        <Copy aria-hidden /> {icon ? null : "Copy"}
      </Button>
    </div>
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
    extras?: ReactNode
    ref?: string
    force?: boolean
    destructive?: boolean
    command: RunCommand
    args: string[]
    confirm?: boolean
    successLabel?: string
    successMessage?: string
    navigateToRun?: boolean
  }) {
    const ok = await confirm({
      action: input.action,
      title: input.title,
      description: input.description,
      warning: input.warning,
      confirmLabel: input.confirmLabel,
      details: input.details,
      extras: input.extras,
      ref: input.ref,
      force: input.force,
      destructive: input.destructive,
    })
    if (!ok) return null
    try {
      const { run_id } = await createRun.mutateAsync({
        command: input.command,
        args: input.args,
        confirm: input.confirm ?? true,
        ...(input.force ? { force: true } : {}),
      })
      const label = input.successMessage ?? `${input.title} ${input.successLabel ?? "started"}`
      toast.success(label, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      if (input.navigateToRun ?? true) navigate(`/runs/${run_id}`)
      void qc.invalidateQueries({ queryKey: queryKeys.links })
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
      return run_id
    } catch (err) {
      toast.error(errorText(err))
      return null
    }
  }
}
