import { useState, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { errorText } from "@/components/StatusFeedback"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { queryKeys, useCreateRun } from "@/lib/queries"

/**
 * Submit a link/register run from a modal form: toasts the outcome (with a
 * View-run action) and refreshes the shared reads the command may change.
 * Resolves to true when the run was created, so the caller can close.
 * `submitting` guards the dialog buttons while the request is in flight.
 */
export function useLinkSubmit() {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const qc = useQueryClient()
  const [submitting, setSubmitting] = useState(false)
  const run = async (input: {
    command: "link" | "register"
    args: string[]
    confirm?: boolean
    force?: boolean
    label: string
  }): Promise<boolean> => {
    setSubmitting(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: input.command,
        args: input.args,
        confirm: input.confirm,
        force: input.force,
      })
      toast.success(`${input.label} started`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      void qc.invalidateQueries({ queryKey: queryKeys.links })
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
      return true
    } catch (err) {
      toast.error(errorText(err))
      return false
    } finally {
      setSubmitting(false)
    }
  }
  return { submitting, run }
}

/** Modal shell for the link forms (same AlertDialog pattern as useConfirm). */
export function ActionDialog({
  title,
  description,
  onClose,
  footer,
  children,
  busy = false,
}: {
  title: string
  description: string
  onClose: () => void
  footer: ReactNode
  children: ReactNode
  /** While true, Esc/overlay/Cancel cannot close the dialog mid-submit. */
  busy?: boolean
}) {
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <AlertDialogContent className="max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="grid gap-4">{children}</div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          {footer}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
