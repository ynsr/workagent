import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ChainEditor, startRepoState, validateChain } from "@/components/ChainEditor"
import { HarnessPicker } from "@/components/HarnessPicker"
import { modelEffortArgs } from "@/lib/harness"
import { buildFlowTemplateDefaultsArgs } from "@/lib/messageRunConfig"
import { chainRunsParallel, runChainOnWorktrees, summarizeExecution } from "@/lib/flowExecution"
import { ActionDialog } from "@/components/LinkDialogs"
import { Button } from "@/components/ui/button"
import { errorText } from "@/components/StatusFeedback"
import { queryKeys, useActions, useCreateRun } from "@/lib/queries"
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
  const qc = useQueryClient()
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const { data: actionsData } = useActions()
  const [working, setWorking] = useState<FlowStep[]>(() => structuredClone(template.steps ?? []))
  const [harness, setHarness] = useState(() => template.default_harness ?? "")
  const [model, setModel] = useState(() => template.default_model ?? "")
  const [effort, setEffort] = useState(() => template.default_effort ?? "")
  const [fresh, setFresh] = useState(false)
  const [saving, setSaving] = useState(false)
  const knownActions = (actionsData?.actions ?? []).map((a) => ({ label: a.label, name: a.name }))
  const chainErrors = validateChain(working)
  const sorted = [...refs].sort()
  const repoState = startRepoState(working)
  const repoMissing = repoState === "missing"
  const runDisabled = sorted.length === 0 || repoMissing || chainErrors.length > 0
  const stepsDirty = JSON.stringify(working) !== JSON.stringify(template.steps ?? [])
  const harnessDirty =
    harness !== (template.default_harness ?? "") ||
    model !== (template.default_model ?? "") ||
    effort !== (template.default_effort ?? "")
  const dirty = stepsDirty || harnessDirty
  const harnessArgs = [...(harness ? ["--harness", harness] : []), ...modelEffortArgs({ model, effort })]
  const stepArgs = stepsDirty ? ["--steps", JSON.stringify(working)] : []
  const freshArgs = fresh ? ["--fresh"] : []
  async function persistTemplateDefaults() {
    if (!harnessDirty) return
    try {
      await createRun.mutateAsync({
        command: "flow",
        args: buildFlowTemplateDefaultsArgs(template.label, { harness, model, effort }),
        confirm: false,
      })
      if (queryKeys.flowTemplates) void qc.invalidateQueries({ queryKey: queryKeys.flowTemplates })
    } catch (err) {
      toast.error(errorText(err))
    }
  }
  function handleConfirm() {
    const parallel = chainRunsParallel(working)
    const label = template.label
    onClose()
    void (async () => {
      await persistTemplateDefaults()
      await runChainOnWorktrees({
      keys: sorted,
      parallel,
      createRun: (key) => createRun.mutateAsync({
        command: "flow",
        args: ["run", label, key, ...harnessArgs, ...stepArgs, ...freshArgs, "--yes"],
        confirm: true,
      }),
      hooks: {
        onStarted: (key, runId) => {
          toast.success(`Flow ${label} on ${key} started`, {
            action: { label: "View", onClick: () => navigate(`/runs/${runId}`) },
          })
        },
        onSettled: (key, runId) => {
          toast.success(`Flow ${label} on ${key} settled — next worktree starting`, {
            action: { label: "View", onClick: () => navigate(`/runs/${runId}`) },
          })
        },
        onTimeout: (key, runId) => {
          toast.warning(`Flow ${label} on ${key} still running after 60s — next worktree starting`, {
            action: { label: "View", onClick: () => navigate(`/runs/${runId}`) },
          })
        },
        onError: (key, reason) => {
          toast.error(parallel ? errorText(reason) : `${key}: ${errorText(reason)}`)
        },
      },
    })
    })()
  }
  const saveDisabled = saving || chainErrors.length > 0
  async function handleSave() {
    setSaving(true)
    try {
      await createRun.mutateAsync({
        command: "flow",
        args: buildFlowTemplateDefaultsArgs(template.label, { steps: working, harness, model, effort }),
        confirm: false,
      })
      if (queryKeys.flowTemplates) void qc.invalidateQueries({ queryKey: queryKeys.flowTemplates })
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
               "--steps", JSON.stringify(working), ...harnessArgs, "--yes"],
        confirm: false,
      })
      if (queryKeys.flowTemplates) void qc.invalidateQueries({ queryKey: queryKeys.flowTemplates })
      toast.success(`Template ${label.trim()} created`)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }
  function handleReset() {
    setWorking(structuredClone(template.steps ?? []))
    setHarness(template.default_harness ?? "")
    setModel(template.default_model ?? "")
    setEffort(template.default_effort ?? "")
  }

  return (
    <ActionDialog
      title={`Run flow ${template.name}`}
      onClose={onClose}
      description={sorted.length === 1 ? (sorted[0] ?? template.label) : `${sorted.length} worktrees: ${sorted.slice(0, 3).join(", ")}${sorted.length > 3 ? ` +${sorted.length - 3} more` : ""}`}
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
            <label className="flex items-center gap-1 text-[12px] text-muted-foreground" title="Start a new run on the edited chain instead of resuming a paused run">
              <input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} />
              Fresh
            </label>
          </div>
          <span className="mx-2 text-muted-foreground" aria-hidden>|</span>
          <Button
            size="sm"
            onClick={() => void handleConfirm()}
            disabled={runDisabled}
            title={repoMissing ? "Select a Repository on the Start step to run" : chainErrors.length > 0 ? chainErrors[0] : undefined}
          >
            {sorted.length > 1 ? `Run on ${sorted.length} worktrees` : "Run flow"}
          </Button>
        </div>
      }
    >
      {repoMissing ? (
        <p className="mb-2 text-[13px] text-destructive" role="alert">
          Repository is required on the Start step — select one below to enable Run flow.
        </p>
      ) : null}
      {sorted.length > 1 ? (
        <p className="mb-2 text-[13px] text-muted-foreground" role="status" data-component="WizardFanOutSummary">
          {(() => {
            const s = summarizeExecution(working)
            const parts: string[] = []
            if (s.parallel > 0) parts.push(`${s.parallel} parallel`)
            if (s.sequential > 0) parts.push(`${s.sequential} sequential`)
            if (s.forced > 0) parts.push(`${s.forced} always-sequential`)
            return s.runsParallel
              ? `Runs on ${sorted.length} worktrees fan out at once (${parts.join(" · ")}).`
              : `Runs on ${sorted.length} worktrees go one worktree at a time (${parts.join(" · ")}).`
          })()}
        </p>
      ) : null}
      <div className="mb-3">
        <HarnessPicker
          idPrefix="flow-wizard"
          harness={harness}
          model={model}
          effort={effort}
          onHarnessChange={(v) => { setHarness(v); setModel(""); setEffort("") }}
          onModelChange={setModel}
          onEffortChange={setEffort}
          allowDefault
          defaultLabel="Use template / default"
        />
      </div>
      <p className="mb-2 text-[13px] text-muted-foreground">
        Steps edited for this run only — the template is untouched.
      </p>
      <ChainEditor
        mode="edit"
        steps={working}
        onChange={setWorking}
        knownActions={knownActions}
        showExecutionMode={sorted.length > 1}
      />
    </ActionDialog>
  )
}
