import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ChainEditor, startRepoState, validateChain } from "@/components/ChainEditor"
import { ActionDialog } from "@/components/LinkDialogs"
import { Button } from "@/components/ui/button"
import { errorText } from "@/components/StatusFeedback"
import { useCreateRun } from "@/lib/queries"
import type { FlowStep, FlowTemplateRow } from "@/lib/api"

/** Launch wizard modal: read-only chain + per-step override editors, confirm
 * fans out one `flow run` per ref (worktree key or issue ref). Shared by the
 * Dashboard FlowsMenu (selected worktrees) and the Launch template picker. */
export function FlowWizard({
  template,
  refs,
  onClose,
}: {
  template: FlowTemplateRow
  refs: string[]
  onClose: () => void
}) {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const [working, setWorking] = useState<FlowStep[]>(() => structuredClone(template.steps ?? []))
  const [overrides, setOverrides] = useState<Record<number, Record<string, unknown>>>({})
  const [running, setRunning] = useState(false)

  const base = template.steps ?? []
  const changed = JSON.stringify(working) !== JSON.stringify(base)
  const chainErrors = validateChain(working)
  const active = Object.fromEntries(
    Object.entries(overrides).filter(([, v]) => Object.keys(v).length > 0),
  )
  const stepConfig = Object.keys(active).length > 0 ? JSON.stringify(active) : null
  const sorted = [...refs].sort()
  const repoState = startRepoState(working, overrides)
  const repoMissing = repoState === "missing"
  const runDisabled = running || sorted.length === 0 || repoMissing || chainErrors.length > 0
  async function handleConfirm() {
    setRunning(true)
    for (const key of sorted) {
      try {
        const { run_id } = await createRun.mutateAsync({
          command: "flow",
          args: ["run", template.label, key,
            ...(changed ? ["--steps", JSON.stringify(working)] : []),
            ...(stepConfig ? ["--step-config", stepConfig] : []), "--yes"],
          confirm: true,
        })
        toast.success(`Flow ${template.label} on ${key} started`, {
          action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
        })
      } catch (err) {
        toast.error(`${key}: ${errorText(err)}`)
      }
    }
    setRunning(false)
    onClose()
  }

  return (
    <ActionDialog
      title={`Run flow ${template.name}`}
      onClose={onClose}
      description={sorted.length === 1 ? (sorted[0] ?? template.label) : `${sorted.length} worktrees: ${sorted.slice(0, 3).join(", ")}${sorted.length > 3 ? ` +${sorted.length - 3} more` : ""}`}
      busy={running}
      footer={
        <Button
          size="sm"
          onClick={() => void handleConfirm()}
          disabled={runDisabled}
          title={repoMissing ? "Select a Repository on the Start step to run" : chainErrors.length > 0 ? chainErrors[0] : undefined}
        >
          {running ? "Starting…" : sorted.length > 1 ? `Run on ${sorted.length} worktrees` : "Run flow"}
        </Button>
      }
    >
      {repoMissing ? (
        <p className="mb-2 text-[13px] text-destructive" role="alert">
          Repository is required on the Start step — select one below to enable Run flow.
        </p>
      ) : null}
      {changed ? (
        <p className="mb-2 text-[13px] text-muted-foreground">
          Steps edited for this run only — the template is untouched.
        </p>
      ) : null}
      <ChainEditor
        mode="edit"
        steps={working}
        onChange={setWorking}
        knownActions={[]}
      />
      <div className="mt-3">
        <ChainEditor
          mode="launch"
          steps={working}
          overrides={overrides}
          onOverridesChange={setOverrides}
          knownActions={[]}
        />
      </div>
    </ActionDialog>
  )
}
