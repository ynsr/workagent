import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ChainEditor, startRepoState, validateChain } from "@/components/ChainEditor"
import { ActionDialog } from "@/components/LinkDialogs"
import { Button } from "@/components/ui/button"
import { errorText } from "@/components/StatusFeedback"
import { useActions, useCreateRun } from "@/lib/queries"
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
  const { data: actionsData } = useActions()
  const [working, setWorking] = useState<FlowStep[]>(() => structuredClone(template.steps ?? []))
  const [running, setRunning] = useState(false)
  const [saving, setSaving] = useState(false)
  const knownActions = (actionsData?.actions ?? []).map((a) => ({ label: a.label, name: a.name }))
  const chainErrors = validateChain(working)
  const sorted = [...refs].sort()
  const repoState = startRepoState(working)
  const repoMissing = repoState === "missing"
  const runDisabled = running || sorted.length === 0 || repoMissing || chainErrors.length > 0
  async function handleConfirm() {
    setRunning(true)
    for (const key of sorted) {
      try {
        const { run_id } = await createRun.mutateAsync({
          command: "flow",
          args: ["run", template.label, key, "--steps", JSON.stringify(working), "--yes"],
          confirm: true,
        })
        toast.success(`Flow ${template.label} on ${key} started`, {
          action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
        })
      } catch (err) {
        toast.error(`${key}: ${errorText(err)}`)
      }
    }
    onClose()
  }
  const dirty = JSON.stringify(working) !== JSON.stringify(template.steps ?? [])
  const saveDisabled = saving || chainErrors.length > 0
  async function handleSave() {
    setSaving(true)
    try {
      await createRun.mutateAsync({
        command: "flow",
        args: ["template", "update", template.label, "--steps", JSON.stringify(working), "--yes"],
        confirm: false,
      })
      toast.success(`Template ${template.label} updated`)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }
  async function handleSaveAs() {
    const name = window.prompt("New template name:", `${template.name} (copy)`)
    if (!name || !name.trim()) return
    const suggested =
      name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_{2,}/g, "_") ||
      `${template.label}_copy`
    const label = window.prompt("New template label (a-z, 0-9, _):", suggested)
    if (!label || !label.trim()) return
    setSaving(true)
    try {
      await createRun.mutateAsync({
        command: "flow",
        args: ["template", "add", "--name", name.trim(), "--label", label.trim(),
               "--steps", JSON.stringify(working), "--yes"],
        confirm: false,
      })
      toast.success(`Template ${label.trim()} created`)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }
  function handleReset() {
    setWorking(structuredClone(template.steps ?? []))
  }

  return (
    <ActionDialog
      title={`Run flow ${template.name}`}
      onClose={onClose}
      description={sorted.length === 1 ? (sorted[0] ?? template.label) : `${sorted.length} worktrees: ${sorted.slice(0, 3).join(", ")}${sorted.length > 3 ? ` +${sorted.length - 3} more` : ""}`}
      busy={running}
      footer={
        <div className="flex w-full items-center justify-between">
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => void handleSave()}
              disabled={saveDisabled || !dirty}
              title={dirty ? "Save edits back to this template" : "No unsaved edits"}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => void handleSaveAs()}
              disabled={saveDisabled}
              title="Save the working chain as a new template"
            >
              Save as
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={handleReset}
              disabled={!dirty}
              title="Discard edits and restore the template chain"
            >
              Reset
            </Button>
          </div>
          <span className="mx-2 text-muted-foreground" aria-hidden>|</span>
          <Button
            size="sm"
            onClick={() => void handleConfirm()}
            disabled={runDisabled}
            title={repoMissing ? "Select a Repository on the Start step to run" : chainErrors.length > 0 ? chainErrors[0] : undefined}
          >
            {running ? "Starting…" : sorted.length > 1 ? `Run on ${sorted.length} worktrees` : "Run flow"}
          </Button>
        </div>
      }
    >
      {repoMissing ? (
        <p className="mb-2 text-[13px] text-destructive" role="alert">
          Repository is required on the Start step — select one below to enable Run flow.
        </p>
      ) : null}
      <p className="mb-2 text-[13px] text-muted-foreground">
        Steps edited for this run only — the template is untouched.
      </p>
      <ChainEditor
        mode="edit"
        steps={working}
        onChange={setWorking}
        knownActions={knownActions}
      />
    </ActionDialog>
  )
}
