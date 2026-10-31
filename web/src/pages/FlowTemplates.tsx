import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, Pencil, Plus, Trash2, Workflow } from "lucide-react"
import { ChainEditor, validateChain } from "@/components/ChainEditor"
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
import { useConfirm } from "@/lib/confirm"
import { copyToClipboard, downloadText, exactDateTime, relativeDate, toCsv } from "@/lib/format"
import { useActions, useCreateRun, useFlowTemplates } from "@/lib/queries"
import type { FlowStep, FlowTemplateRow } from "@/lib/api"

export function FlowTemplates() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data, isPending, isFetching, isError, error, refetch } = useFlowTemplates()
  const { data: actionsData } = useActions()
  const templates = data?.templates ?? []
  const knownActions = (actionsData?.actions ?? []).map((a) => ({ label: a.label, name: a.name }))
  const [name, setName] = useState("")
  const [label, setLabel] = useState("")
  const [labelDirty, setLabelDirty] = useState(false)
  const [steps, setSteps] = useState<FlowStep[]>([])
  const [pre, setPre] = useState("")
  const [post, setPost] = useState("")
  const [adding, setAdding] = useState(false)
  const [detail, setDetail] = useState<FlowTemplateRow | null>(null)
  const [editingLabel, setEditingLabel] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [lineageNote, setLineageNote] = useState<string | null>(null)

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

  function reset() {
    setName("")
    setLabel("")
    setLabelDirty(false)
    setSteps([])
    setPre("")
    setPost("")
    setEditingLabel(null)
    setLineageNote(null)
  }

  async function handleAdd() {
    if (!name.trim() || !labelValid || validateChain(steps).length > 0) return
    setAdding(true)
    try {
      const isEdit = editingLabel !== null
      const { run_id } = await createRun.mutateAsync({
        command: "flow",
        confirm: true,
        args: isEdit
          ? [
              "template", "update", editingLabel,
              "--name", name.trim(),
              ...(label !== editingLabel ? ["--new-label", label] : []),
              "--steps", JSON.stringify(steps),
              "--pre-execution", pre,
              "--post-execution", post,
            ]
          : [
              "template", "add",
              "--name", name.trim(),
              "--label", label,
              "--steps", JSON.stringify(steps),
              ...(pre.trim() ? ["--pre-execution", pre.trim()] : []),
              ...(post.trim() ? ["--post-execution", post.trim()] : []),
            ],
      })
      toast.success(isEdit ? "Flow template update started" : "Flow template add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      reset()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setAdding(false)
    }
  }

  function handleEdit(row: FlowTemplateRow) {
    setDetail(null)
    setEditingLabel(row.label)
    setName(row.name)
    setLabel(row.label)
    setLabelDirty(false)
    setSteps(row.steps ?? [])
    setPre(row.pre_execution ?? "")
    setPost(row.post_execution ?? "")
  }

  function handleClone(row: FlowTemplateRow) {
    setDetail(null)
    setEditingLabel(null)
    setName(`${row.name} (copy)`)
    setLabel("")
    setLabelDirty(false)
    setSteps(structuredClone(row.steps ?? []))
    setPre(row.pre_execution ?? "")
    setPost(row.post_execution ?? "")
    setLineageNote(`Clone of ${row.label} — saving creates a new template with lineage.`)
  }

  function handleCompose() {
    const rows = selected
      .map((label) => templates.find((t) => t.label === label))
      .filter((t): t is FlowTemplateRow => Boolean(t))
    if (rows.length === 0) return
    const merged: FlowStep[] = []
    let startSeen = false
    let dropped = false
    for (const row of rows) {
      for (const step of row.steps ?? []) {
        if (step.kind === "start") {
          if (startSeen) {
            dropped = true
            continue
          }
          startSeen = true
        }
        merged.push(structuredClone(step))
      }
    }
    const parents = rows.map((r) => r.label)
    setEditingLabel(null)
    setName("")
    setLabel("")
    setLabelDirty(false)
    setSteps(merged)
    setSelected([])
    setLineageNote(
      `Composed from ${parents.join(", ")}${dropped ? " — extra 'start' steps collapsed" : ""}.`,
    )
  }

  async function handleRemove(row: FlowTemplateRow) {
    const ok = await confirm({
      title: `Delete flow template ${row.name}`,
      description: "Deletes the flow template row. Flow runs are untouched.",
      destructive: true,
      confirmLabel: "Delete template",
      details: [
        { label: "Label", value: row.label, mono: true },
        { label: "Name", value: row.name },
      ],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "flow",
        confirm: true,
        args: ["template", "delete", row.label, "--yes"],
      })
      toast.success("Flow template delete started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCopyJson() {
    try {
      await copyToClipboard(JSON.stringify(templates, null, 2))
      toast.success("Flow template list JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleDownloadCsv() {
    downloadText(
      "workagent-flow-templates.csv",
      toCsv(
        ["label", "name", "steps"],
        templates.map((t) => [t.label, t.name, String(t.steps?.length ?? 0)]),
      ),
      "text/csv",
    )
  }

  const chainErrors = validateChain(steps)
  const canSave = name.trim().length > 0 && labelValid && steps.length > 0 && chainErrors.length === 0 && !adding

  return (
    <div>
      <PageHeader
        title="Flow Templates"
        description="Step chains (Start → … → Release). Run them from the Dashboard Flows dropdown or Launch."
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

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <Card className="order-2 lg:order-1">
          <CardHeader>
            <CardTitle>Flow templates</CardTitle>
            <CardDescription>GET /api/flow-templates</CardDescription>
          </CardHeader>
          <CardContent>
            {(isPending && !data) ? (
              <TableSkeleton rows={4} />
            ) : (isError && !data) ? (
              <ErrorState error={error} onRetry={() => void refetch()} />
            ) : templates.length === 0 ? (
              <EmptyState
                icon={<Workflow className="size-10" aria-hidden />}
                title="No flow templates"
                description="Add one with the form — a name plus at least one step."
              />
            ) : (
              <div className="relative">
                {selected.length > 0 ? (
                  <div className="mb-2 flex items-center gap-2">
                    <Button variant="outline" size="sm" onClick={handleCompose}>
                      Compose {selected.length} selected
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
                      Clear
                    </Button>
                  </div>
                ) : null}
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-8" aria-label="Select" />
                      <TableHead>Name</TableHead>
                      <TableHead>Label</TableHead>
                      <TableHead>Steps</TableHead>
                      <TableHead>Updated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {templates.map((t) => (
                      <TableRow key={t.label}>
                        <TableCell>
                          <input
                            type="checkbox"
                            aria-label={`Select ${t.name}`}
                            checked={selected.includes(t.label)}
                            onChange={(e) =>
                              setSelected((prev) =>
                                e.target.checked ? [...prev, t.label] : prev.filter((l) => l !== t.label),
                              )
                            }
                          />
                        </TableCell>
                        <TableCell>
                          <button
                            type="button"
                            className="font-medium underline-offset-2 hover:underline"
                            onClick={() => setDetail(t)}
                          >
                            {t.name}
                          </button>
                          {t.parent_labels?.length ? (
                            <div className="text-xs text-muted-foreground">from {t.parent_labels.join(", ")}</div>
                          ) : null}
                        </TableCell>
                        <TableCell className="font-mono text-xs">
                          {t.steps?.length ?? 0}
                        </TableCell>
                        <TableCell title={exactDateTime(t.modified_at)}>
                          {relativeDate(t.modified_at)}
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            <Button variant="ghost" size="sm" title={`Clone ${t.name}`} onClick={() => handleClone(t)}>
                              <Copy aria-hidden />
                            </Button>
                            <Button variant="ghost" size="sm" title={`Edit ${t.name}`} onClick={() => handleEdit(t)}>
                              <Pencil aria-hidden />
                            </Button>
                            <Button variant="ghost" size="sm" title={`Delete ${t.name}`} onClick={() => void handleRemove(t)}>
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
            <CardTitle>{editingLabel ? `Edit ${editingLabel}` : "Add a template"}</CardTitle>
            <CardDescription>
              {editingLabel
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
              <Label>Steps</Label>
              {lineageNote ? (
                <p className="text-xs text-muted-foreground">{lineageNote}</p>
              ) : null}
              <ChainEditor mode="edit" steps={steps} onChange={setSteps} knownActions={knownActions} />
            </div>
            <ScriptField id="flow-pre" label="Pre-execution script" value={pre} onChange={setPre} placeholder={"#!/bin/bash\nmake lint"} />
            <ScriptField id="flow-post" label="Post-execution script" value={post} onChange={setPost} placeholder={"#!/bin/bash\nmake test"} />
            <div className="flex gap-2">
              <Button className="flex-1" onClick={() => void handleAdd()} disabled={!canSave}>
                <Plus aria-hidden />
                {adding ? "Saving…" : editingLabel ? "Save changes" : "Add template"}
              </Button>
              {editingLabel ? (
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
            <span className="text-muted-foreground" title={exactDateTime(detail.modified_at)}>
              updated {relativeDate(detail.modified_at)}
            </span>
          </div>
          <div className="grid gap-1 text-sm">
            <span className="font-medium">Steps</span>
            <pre className="max-h-64 overflow-auto rounded-md border p-2 font-mono text-xs whitespace-pre-wrap">
              {JSON.stringify(detail.steps, null, 2)}
            </pre>
          </div>
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
