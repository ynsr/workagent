import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, Pencil, Plus, Trash2, Zap } from "lucide-react"
import { ActionIcon } from "@/components/ActionIcon"
import { IconPicker } from "@/components/IconPicker"
import { PageHeader } from "@/components/PageHeader"
import { ScriptBlock, ScriptField } from "@/components/ScriptBlock"
import {
  EmptyState,
  ErrorState,
  RefreshOverlay,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ActionDialog } from "@/components/LinkDialogs"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { useConfirm } from "@/lib/confirm"
import { copyToClipboard, downloadText, exactDateTime, relativeDate, toCsv } from "@/lib/format"
import { useActions, useCreateRun } from "@/lib/queries"
import { ACTION_PRESETS } from "@/lib/actionPresets"
import type { ActionRow } from "@/lib/api"

const RUN_MODES = ["preview", "headless", "terminal"] as const

function runModeOf(row: ActionRow): string {
  const m = row.default_options?.run_mode
  return typeof m === "string" && (RUN_MODES as readonly string[]).includes(m) ? m : "preview"
}

export function Actions() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data, isPending, isFetching, isError, error, refetch } = useActions()
  const actions = data?.actions ?? []

  const [name, setName] = useState("")
  const [label, setLabel] = useState("")
  const [labelDirty, setLabelDirty] = useState(false)
  const [icon, setIcon] = useState("")
  const [prompt, setPrompt] = useState("")
  const [pre, setPre] = useState("")
  const [post, setPost] = useState("")
  const [mode, setMode] = useState<string>("preview")
  const [adding, setAdding] = useState(false)
  const [detail, setDetail] = useState<ActionRow | null>(null)
  const [editingLabel, setEditingLabel] = useState<string | null>(null)
  const isEdit = editingLabel !== null
  const labelValid = /^[a-z0-9_]+$/.test(label)

  function handleNameChange(value: string) {
    setName(value)
    if (editingLabel === null && !labelDirty) {
      setLabel(value.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_{2,}/g, "_"))
    }
  }

  function handleLabelChange(value: string) {
    setLabel(value)
    setLabelDirty(true)
  }

  function applyPreset(id: string) {
    const preset = ACTION_PRESETS.find((p) => p.id === id)
    if (!preset) return
    setName(preset.name)
    if (editingLabel === null && !labelDirty) {
      setLabel(preset.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/_{2,}/g, "_"))
    }
    setIcon(preset.icon)
    setPrompt(preset.prompt)
    setPre(preset.pre_execution)
    setPost(preset.post_execution)
    setMode(preset.run_mode)
  }

  function reset() {
    setName("")
    setLabel("")
    setLabelDirty(false)
    setIcon("")
    setPrompt("")
    setPre("")
    setPost("")
    setMode("preview")
    setEditingLabel(null)
  }

  async function handleAdd() {
    if (!name.trim() || !labelValid || (!prompt.trim() && !pre.trim())) return
    setAdding(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "action",
        args: isEdit
          ? [
              "update", editingLabel as string,
              "--name", name.trim(),
              ...(label !== editingLabel ? ["--new-label", label] : []),
              "--icon", icon.trim() || "Zap",
              "--prompt", prompt,
              "--pre-execution", pre,
              "--post-execution", post,
              "--default-options", JSON.stringify({ run_mode: mode }),
            ]
          : [
              "add",
              "--name", name.trim(),
              "--label", label,
              ...(icon.trim() ? ["--icon", icon.trim()] : []),
              ...(prompt.trim() ? ["--prompt", prompt.trim()] : []),
              ...(pre.trim() ? ["--pre-execution", pre.trim()] : []),
              ...(post.trim() ? ["--post-execution", post.trim()] : []),
              "--default-options", JSON.stringify({ run_mode: mode }),
            ],
      })
      toast.success(isEdit ? "Action update started" : "Action add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      reset()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setAdding(false)
    }
  }

  function handleEdit(row: ActionRow) {
    setDetail(null)
    setEditingLabel(row.label)
    setName(row.name)
    setLabel(row.label)
    setLabelDirty(false)
    setIcon(row.icon === "Zap" ? "" : row.icon)
    setPrompt(row.prompt ?? "")
    setPre(row.pre_execution ?? "")
    setPost(row.post_execution ?? "")
    setMode(runModeOf(row))
  }

  async function handleRemove(row: ActionRow) {
    const ok = await confirm({
      action: "action delete",
      title: `Delete action ${row.name}`,
      description: "Deletes the custom action row. Linked worktrees are untouched.",
      destructive: true,
      confirmLabel: "Delete action",
      details: [
        { label: "Label", value: row.label, mono: true },
        { label: "Name", value: row.name },
      ],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "action",
        args: ["delete", row.label, "--yes"],
        confirm: true,
      })
      toast.success("Action delete started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCopyJson() {
    try {
      await copyToClipboard(JSON.stringify(actions, null, 2))
      toast.success("Action list JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleDownloadCsv() {
    downloadText(
      "workagent-actions.csv",
      toCsv(
        ["label", "name", "icon", "mode"],
        actions.map((a) => [a.label, a.name, a.icon, runModeOf(a)]),
      ),
      "text/csv",
    )
  }

  return (
    <div>
      <PageHeader
        title="Actions"
        description="Custom actions (action add / action run / action delete). Run them from the Dashboard Actions dropdown."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={handleCopyJson}>
              <Copy aria-hidden /> JSON
            </Button>
            <Button variant="outline" size="sm" onClick={handleDownloadCsv}>
              <Download aria-hidden /> CSV
            </Button>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <Card className="order-2 lg:order-1">
          <CardHeader>
            <CardTitle>Custom actions</CardTitle>
            <CardDescription>GET /api/actions</CardDescription>
          </CardHeader>
          <CardContent>
            {(isPending && !data) ? (
              <TableSkeleton rows={4} />
            ) : (isError && !data) ? (
              <ErrorState error={error} onRetry={() => void refetch()} />
            ) : actions.length === 0 ? (
              <EmptyState
                icon={<Zap className="size-10" aria-hidden />}
                title="No custom actions"
                description="Add one with the form — a name plus a prompt and/or a pre-execution script."
              />
            ) : (
              <div className="relative">
                {isFetching && !isPending ? <RefreshOverlay /> : null}
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead aria-label="Icon" />
                      <TableHead>Name</TableHead>
                      <TableHead>Label</TableHead>
                      <TableHead>Mode</TableHead>
                      <TableHead>Updated</TableHead>
                      <TableHead className="w-28" aria-label="Actions" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {actions.map((a) => (
                      <TableRow key={a.label}>
                        <TableCell>
                          <ActionIcon name={a.icon} className="size-4" />
                        </TableCell>
                        <TableCell>
                          <button
                            type="button"
                            className="font-medium underline-offset-2 hover:underline"
                            onClick={() => setDetail(a)}
                          >
                            {a.name}
                          </button>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{a.label}</TableCell>
                        <TableCell>
                          <Badge variant="secondary">{runModeOf(a)}</Badge>
                        </TableCell>
                        <TableCell title={exactDateTime(a.modified_at)}>
                          {relativeDate(a.modified_at)}
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            <Button variant="ghost" size="sm" title={`Edit ${a.name}`} onClick={() => handleEdit(a)}>
                              <Pencil aria-hidden />
                            </Button>
                            <Button variant="ghost" size="sm" title={`Delete ${a.name}`} onClick={() => void handleRemove(a)}>
                              <Trash2 aria-hidden />
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="order-1 h-fit lg:order-2">
          <CardHeader>
            <CardTitle>{isEdit ? `Update ${editingLabel}` : "Add an action"}</CardTitle>
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
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setPrompt(e.target.value)}
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
            <div className="flex gap-2">
              <Button className="flex-1" onClick={() => void handleAdd()} disabled={!name.trim() || !labelValid || (!prompt.trim() && !pre.trim()) || adding}>
                {isEdit ? <Pencil aria-hidden /> : <Plus aria-hidden />}
                {adding ? "Saving…" : isEdit ? "Update action" : "Add action"}
              </Button>
              {isEdit ? (
                <Button variant="outline" onClick={reset} disabled={adding}>
                  Cancel
                </Button>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>

      {detail ? (
        <ActionDialog
          title={detail.name}
          description={detail.label}
          onClose={() => setDetail(null)}
          footer={
            <Button variant="outline" size="sm" onClick={() => handleEdit(detail)}>
              <Pencil aria-hidden /> Edit
            </Button>
          }
        >
          <div className="flex items-center gap-2 text-sm">
            <ActionIcon name={detail.icon} className="size-4" />
            <Badge variant="secondary">{runModeOf(detail)}</Badge>
            <span className="text-muted-foreground" title={exactDateTime(detail.modified_at)}>
              updated {relativeDate(detail.modified_at)}
            </span>
          </div>
          {detail.prompt ? (
            <div className="grid gap-1 text-sm">
              <span className="font-medium">Prompt</span>
              <pre className="max-h-48 overflow-auto rounded-md border p-2 font-mono text-xs whitespace-pre-wrap">{detail.prompt}</pre>
            </div>
          ) : null}
          {detail.pre_execution ? (
            <div className="grid gap-1 text-sm">
              <span className="font-medium">Pre-execution</span>
              <ScriptBlock code={detail.pre_execution} />
            </div>
          ) : null}
          {detail.post_execution ? (
            <div className="grid gap-1 text-sm">
              <span className="font-medium">Post-execution</span>
              <ScriptBlock code={detail.post_execution} />
            </div>
          ) : null}
        </ActionDialog>
      ) : null}
    </div>
  )
}
