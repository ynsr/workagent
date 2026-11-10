import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, Pencil, Play, Trash2, Workflow } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { ScriptBlock } from "@/components/ScriptBlock"
import {
  EMPTY_FLOW_TEMPLATE_SEED,
  FlowTemplateFormCard,
  seedFromTemplate,
  type FlowTemplateFormSeed,
} from "@/components/FlowTemplateForm"
import { ImmediateRunDialog, ImmediateRunForm } from "@/components/ImmediateRunForm"
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
import { isRunImmediateTemplate, type RowCategory } from "@/lib/messageRunConfig"
export function FlowTemplates() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data, isPending, isFetching, isError, error, refetch } = useFlowTemplates()
  const templates: FlowTemplateRow[] = data?.templates ?? []
  const { data: actionsData } = useActions()
  const knownActions = (actionsData?.actions ?? []).map((a) => ({ label: a.label, name: a.name }))
  const sortedTemplates = [...templates].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
  )
  const [detail, setDetail] = useState<FlowTemplateRow | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [runRow, setRunRow] = useState<FlowTemplateRow | null>(null)
  const [seed, setSeed] = useState<FlowTemplateFormSeed>(EMPTY_FLOW_TEMPLATE_SEED)
  const [seedKey, setSeedKey] = useState(0)

  function restart(seedDraft: FlowTemplateFormSeed) {
    setSeed(seedDraft)
    setSeedKey((k) => k + 1)
  }

  function handleEdit(row: FlowTemplateRow) {
    setDetail(null)
    restart(seedFromTemplate(row))
  }

  function handleQuickRun(row: FlowTemplateRow) {
    // run_immediately templates launch straight away (standard confirm via the
    // shared run form prefilled with the row); everything else opens the form.
    setRunRow(row)
  }

  function handleClone(row: FlowTemplateRow) {
    setDetail(null)
    const base = seedFromTemplate(row)
    restart({
      ...base,
      editingLabel: null,
      name: `${row.name} (copy)`,
      label: "",
      lineageNote: `Clone of ${row.label} — saving creates a new template with lineage.`,
    })
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
    setSelected([])
    restart({
      ...EMPTY_FLOW_TEMPLATE_SEED,
      steps: merged,
      lineageNote: `Composed from ${parents.join(", ")}${dropped ? " — extra 'start' steps collapsed" : ""}.`,
    })
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
  async function handleToggle(row: FlowTemplateRow, field: "protected" | "dedicated_top_bar" | "dedicated_overlay") {
    try {
      const on = !(row as unknown as Record<string, unknown>)[field]
      const flag = field.replaceAll("_", "-")
      await createRun.mutateAsync({
        command: "flow",
        confirm: false,
        args: ["template", "update", row.label, on ? `--${flag}` : `--no-${flag}`, "--yes"],
      })
      toast.success(`Template ${row.label} ${field} ${on ? "on" : "off"}`)
      void refetch()
    } catch (err) {
      toast.error(errorText(err))
    }
  }
  async function handleMove(row: FlowTemplateRow, dir: -1 | 1) {
    const idx = sortedTemplates.findIndex((t) => t.label === row.label)
    const other = sortedTemplates[idx + dir]
    if (idx < 0 || !other) return
    try {
      const aOrder = row.sort_order ?? 0
      const bOrder = other.sort_order ?? 0
      const target = bOrder === aOrder ? aOrder + dir : bOrder
      await createRun.mutateAsync({
        command: "flow",
        confirm: false,
        args: ["template", "update", row.label, "--sort-order", String(target), "--yes"],
      })
      if (bOrder === aOrder) {
        await createRun.mutateAsync({
          command: "flow",
          confirm: false,
          args: ["template", "update", other.label, "--sort-order", String(aOrder), "--yes"],
        })
      }
      toast.success(`Template ${row.label} moved ${dir < 0 ? "up" : "down"}`)
      void refetch()
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
                      <TableHead>Run</TableHead>
                      <TableHead>Category</TableHead>
                      <TableHead>Order</TableHead>
                      <TableHead>Protected</TableHead>
                      <TableHead>Top</TableHead>
                      <TableHead>Overlay</TableHead>
                      <TableHead>Updated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sortedTemplates.map((t) => (
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
                        <TableCell className="font-mono text-xs">{t.label}</TableCell>
                        <TableCell className="font-mono text-xs">
                          {t.steps?.length ?? 0}
                          {isRunImmediateTemplate(t) ? " · immediate" : ""}
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={isRunImmediateTemplate(t) ? `Run ${t.name} now` : `Run ${t.name}…`} onClick={() => handleQuickRun(t)}>
                            <Play aria-hidden />
                          </Button>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{t.category ?? "worktree"}</TableCell>
                        <TableCell className="font-mono text-xs">
                          {t.sort_order ?? 0}
                          <span className="ml-1 inline-flex gap-0.5">
                            <Button variant="ghost" size="sm" title={`Move ${t.name} up`} onClick={() => void handleMove(t, -1)}>▲</Button>
                            <Button variant="ghost" size="sm" title={`Move ${t.name} down`} onClick={() => void handleMove(t, 1)}>▼</Button>
                          </span>
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={t.protected ? `Unprotect ${t.name}` : `Protect ${t.name}`} onClick={() => void handleToggle(t, "protected")}>
                            {t.protected ? "🔒" : "🔓"}
                          </Button>
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={t.dedicated_top_bar ? `Remove top-bar button for ${t.name}` : `Add top-bar button for ${t.name}`} onClick={() => void handleToggle(t, "dedicated_top_bar")}>
                            {t.dedicated_top_bar ? "★" : "☆"}
                          </Button>
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={t.dedicated_overlay ? `Remove overlay button for ${t.name}` : `Add overlay button for ${t.name}`} onClick={() => void handleToggle(t, "dedicated_overlay")}>
                            {t.dedicated_overlay ? "◉" : "○"}
                          </Button>
                        </TableCell>
                        <TableCell title={exactDateTime(t.modified_at)}>
                          {relativeDate(t.modified_at)}
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            <Button variant="ghost" size="sm" title={`Clone ${t.name}`} onClick={() => handleClone(t)}>
                              <Copy aria-hidden />
                            </Button>
                            {t.protected ? null : (
                              <>
                                <Button variant="ghost" size="sm" title={`Edit ${t.name}`} onClick={() => handleEdit(t)}>
                                  <Pencil aria-hidden />
                                </Button>
                                <Button variant="ghost" size="sm" title={`Delete ${t.name}`} onClick={() => void handleRemove(t)}>
                                  <Trash2 aria-hidden />
                                </Button>
                              </>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {isFetching && !isPending ? <RefreshOverlay /> : null}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="order-1 grid gap-6 lg:order-2">
          <Card className="h-fit">
            <CardHeader>
              <CardTitle>Run a message flow</CardTitle>
              <CardDescription>
                Runs immediately with the standard confirm. Rows flagged run immediately skip this form.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ImmediateRunForm kind="flow" />
            </CardContent>
          </Card>
          <FlowTemplateFormCard
            key={seedKey}
            seed={seed}
            knownActions={knownActions}
            onDone={() => restart(EMPTY_FLOW_TEMPLATE_SEED)}
          />
        </div>
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
      {runRow ? (
        <ImmediateRunDialog
          kind="flow"
          category={(runRow.category as RowCategory | undefined) ?? "worktree"}
          lockCategory
          preset={runRow.label}
          onClose={() => setRunRow(null)}
        />
      ) : null}
    </div>
  )
}
