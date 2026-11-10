import { useState } from "react"
import { Plus } from "lucide-react"
import { toast } from "sonner"
import { useNavigate } from "react-router-dom"
import { ChainEditor, validateChain } from "@/components/ChainEditor"
import { ScriptField } from "@/components/ScriptBlock"
import { errorText } from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCreateRun } from "@/lib/queries"
import {
  ROW_CATEGORIES,
  buildFlowTemplateUpsertArgs,
  isRunImmediateTemplate,
  type RowCategory,
} from "@/lib/messageRunConfig"
import type { FlowStep, FlowTemplateRow } from "@/lib/api"

export interface FlowTemplateFormSeed {
  editingLabel: string | null
  name: string
  label: string
  steps: FlowStep[]
  pre: string
  post: string
  lineageNote: string | null
  category: RowCategory
  runImmediately: boolean
}

export const EMPTY_FLOW_TEMPLATE_SEED: FlowTemplateFormSeed = {
  editingLabel: null,
  name: "",
  label: "",
  steps: [],
  pre: "",
  post: "",
  lineageNote: null,
  category: "worktree",
  runImmediately: false,
}

/** Seed a draft from an existing template (edit). */
export function seedFromTemplate(row: FlowTemplateRow): FlowTemplateFormSeed {
  return {
    editingLabel: row.label,
    name: row.name,
    label: row.label,
    steps: row.steps ?? [],
    pre: row.pre_execution ?? "",
    post: row.post_execution ?? "",
    lineageNote: null,
    category: row.category === "message" ? "message" : "worktree",
    runImmediately: isRunImmediateTemplate(row),
  }
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_{2,}/g, "_")
}

/** Add/update card for the Flow Templates page. Owns the draft state; the
 * page re-mounts it (via key) to seed an edit, clone, or compose draft.
 * Category is a first-class field; run_immediately round-trips through the
 * first step's config (templates have no default_options column). */
export function FlowTemplateFormCard({ seed, knownActions, onDone }: {
  seed: FlowTemplateFormSeed
  knownActions: { label: string; name: string }[]
  onDone: () => void
}) {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const [name, setName] = useState(seed.name)
  const [label, setLabel] = useState(seed.label)
  const [labelDirty, setLabelDirty] = useState(seed.editingLabel !== null)
  const [steps, setSteps] = useState<FlowStep[]>(seed.steps)
  const [pre, setPre] = useState(seed.pre)
  const [post, setPost] = useState(seed.post)
  const [category, setCategory] = useState<RowCategory>(seed.category)
  const [runImmediately, setRunImmediately] = useState(seed.runImmediately)
  const [saving, setSaving] = useState(false)
  const isEdit = seed.editingLabel !== null
  const labelValid = /^[a-z0-9_]+$/.test(label)

  function handleNameChange(value: string) {
    setName(value)
    if (!isEdit && !labelDirty) setLabel(slug(value))
  }

  function handleLabelChange(value: string) {
    setLabel(value)
    setLabelDirty(true)
  }

  async function handleSave() {
    if (!name.trim() || !labelValid || validateChain(steps).length > 0 || saving) return
    setSaving(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "flow",
        confirm: true,
        args: buildFlowTemplateUpsertArgs(seed.editingLabel, {
          name, label, steps, pre, post, runImmediately, category,
        }),
      })
      toast.success(isEdit ? "Flow template update started" : "Flow template add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      onDone()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  const chainErrors = validateChain(steps)
  const canSave = name.trim().length > 0 && labelValid && steps.length > 0 && chainErrors.length === 0 && !saving

  return (
    <Card className="order-1 h-fit lg:order-2">
      <CardHeader>
        <CardTitle>{isEdit ? `Edit ${seed.editingLabel}` : "Add a template"}</CardTitle>
        <CardDescription>
          {isEdit
            ? "flow template update replaces all fields; renaming the label retargets flow history."
            : "flow template add --name --label creates the row; run it per worktree from the Dashboard."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="flow-name">Name</Label>
          <Input
            id="flow-name"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder="Full review cycle"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="flow-label">Label</Label>
          <Input
            id="flow-label"
            value={label}
            onChange={(e) => handleLabelChange(e.target.value)}
            placeholder="full_review_cycle"
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-[13px]"
          />
          <p className="text-xs text-muted-foreground">Lowercase letters, digits, underscores only (^[a-z0-9_]+$).</p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="flow-category">Category</Label>
          <select
            id="flow-category"
            value={category}
            onChange={(e) => setCategory(e.target.value as RowCategory)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
          >
            {ROW_CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            worktree templates surface on the Dashboard; message templates on the Messages page.
          </p>
        </div>
        <div className="grid gap-2">
          <Label>Steps</Label>
          {seed.lineageNote ? (
            <p className="text-xs text-muted-foreground">{seed.lineageNote}</p>
          ) : null}
          <ChainEditor mode="edit" steps={steps} onChange={setSteps} knownActions={knownActions} />
        </div>
        <ScriptField id="flow-pre" label="Pre-execution script" value={pre} onChange={setPre} placeholder={"#!/bin/bash\nmake lint"} />
        <ScriptField id="flow-post" label="Post-execution script" value={post} onChange={setPost} placeholder={"#!/bin/bash\nmake test"} />
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={runImmediately}
            onChange={(e) => setRunImmediately(e.target.checked)}
          />
          Run immediately (skip the run form; the standard confirm still applies)
        </label>
        <div className="flex gap-2">
          <Button className="flex-1" onClick={() => void handleSave()} disabled={!canSave}>
            <Plus aria-hidden />
            {saving ? "Saving…" : isEdit ? "Save changes" : "Add template"}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
