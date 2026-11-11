import { useState } from "react"
import { Pencil, Plus } from "lucide-react"
import { toast } from "sonner"
import { useNavigate } from "react-router-dom"
import { IconPicker } from "@/components/IconPicker"
import { ScriptField } from "@/components/ScriptBlock"
import { errorText } from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCreateRun } from "@/lib/queries"
import { ACTION_PRESETS } from "@/lib/actionPresets"
import {
  ROW_CATEGORIES,
  actionRunModeOf,
  buildActionUpsertArgs,
  isRunImmediateAction,
  type ActionUpsertForm,
  type RowCategory,
} from "@/lib/messageRunConfig"
import type { ActionRow } from "@/lib/api"

const RUN_MODES = ["preview", "headless", "terminal"] as const

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_{2,}/g, "_")
}

/** Add/update card for the Actions page: label auto-slug, presets, prompt +
 * pre/post scripts, default run mode, category, and the run_immediately flag
 * (first-class backend column, v20). */
export function ActionFormCard({ editing, onDone, onCancel }: {
  editing: ActionRow | null
  onDone: () => void
  onCancel: () => void
}) {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const [name, setName] = useState(editing?.name ?? "")
  const [label, setLabel] = useState(editing?.label ?? "")
  const [labelDirty, setLabelDirty] = useState(false)
  const [icon, setIcon] = useState(editing && editing.icon !== "Zap" ? editing.icon : "")
  const [prompt, setPrompt] = useState(editing?.prompt ?? "")
  const [pre, setPre] = useState(editing?.pre_execution ?? "")
  const [post, setPost] = useState(editing?.post_execution ?? "")
  const [mode, setMode] = useState<string>(editing ? actionRunModeOf(editing) : "preview")
  const [category, setCategory] = useState<RowCategory>(
    (editing?.category as RowCategory | undefined) ?? "worktree",
  )
  const [runImmediately, setRunImmediately] = useState(
    editing ? isRunImmediateAction(editing) : false,
  )
  const [saving, setSaving] = useState(false)
  const isEdit = editing !== null
  const labelValid = /^[a-z0-9_]+$/.test(label)

  function handleNameChange(value: string) {
    setName(value)
    if (!isEdit && !labelDirty) setLabel(slug(value))
  }

  function handleLabelChange(value: string) {
    setLabel(value)
    setLabelDirty(true)
  }

  function applyPreset(id: string) {
    const preset = ACTION_PRESETS.find((p) => p.id === id)
    if (!preset) return
    setName(preset.name)
    if (!isEdit && !labelDirty) setLabel(slug(preset.name))
    setIcon(preset.icon)
    setPrompt(preset.prompt)
    setPre(preset.pre_execution)
    setPost(preset.post_execution)
    setMode(preset.run_mode)
  }

  async function handleSave() {
    if (saving) return
    if (!name.trim() || !labelValid) return
    if (!prompt.trim() && !pre.trim()) return
    setSaving(true)
    try {
      const form: ActionUpsertForm = {
        name, label, icon, prompt, pre, post, runMode: mode, runImmediately, category,
      }
      const { run_id } = await createRun.mutateAsync({
        command: "action",
        args: buildActionUpsertArgs(editing?.label ?? null, form),
      })
      toast.success(isEdit ? "Action update started" : "Action add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      onDone()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  const canSave = name.trim().length > 0 && labelValid && (prompt.trim().length > 0 || pre.trim().length > 0) && !saving

  return (
    <Card className="order-1 h-fit lg:order-2">
      <CardHeader>
        <CardTitle>{isEdit ? `Update ${editing.label}` : "Add an action"}</CardTitle>
        <CardDescription>
          {isEdit
            ? "action update replaces all fields; renaming the label cascades to flow steps."
            : "action add --name --label creates the row; run it per worktree from the Dashboard."}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="action-preset">Start from preset</Label>
          <select
            id="action-preset"
            defaultValue=""
            onChange={(e) => applyPreset(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
          >
            <option value="">Select a preset…</option>
            {ACTION_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="action-name">Name</Label>
          <Input
            id="action-name"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder="Generate Post PRD"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="action-label">Label</Label>
          <Input
            id="action-label"
            value={label}
            onChange={(e) => handleLabelChange(e.target.value)}
            placeholder="generate_post_prd"
            autoComplete="off"
            spellCheck={false}
            className="font-mono text-[13px]"
          />
          <p className="text-xs text-muted-foreground">Lowercase letters, digits, underscores only (^[a-z0-9_]+$).</p>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="action-icon">Icon</Label>
          <IconPicker id="action-icon" value={icon} onChange={setIcon} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="action-prompt">Prompt</Label>
          <textarea
            id="action-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Instructions for the agent…"
            rows={4}
            className="flex min-h-24 w-full rounded-md border border-input bg-transparent px-3 py-2 text-[13px] shadow-xs outline-none placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
          />
        </div>
        <ScriptField id="action-pre" label="Pre-execution script" value={pre} onChange={setPre} placeholder={"#!/bin/bash\nmake lint"} />
        <ScriptField id="action-post" label="Post-execution script" value={post} onChange={setPost} placeholder={"#!/bin/bash\nmake test"} />
        <div className="grid gap-2">
          <Label htmlFor="action-mode">Default run mode</Label>
          <select
            id="action-mode"
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
          >
            {RUN_MODES.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="action-category">Category</Label>
          <select
            id="action-category"
            value={category}
            onChange={(e) => setCategory(e.target.value as RowCategory)}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring"
          >
            {ROW_CATEGORIES.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            worktree rows surface on the Dashboard; message rows on the Messages page.
          </p>
        </div>
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
            {isEdit ? <Pencil aria-hidden /> : <Plus aria-hidden />}
            {saving ? "Saving…" : isEdit ? "Update action" : "Add action"}
          </Button>
          {isEdit ? (
            <Button variant="outline" onClick={onCancel} disabled={saving}>
              Cancel
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
