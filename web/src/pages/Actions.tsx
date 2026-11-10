import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, Pencil, Play, Trash2, Zap } from "lucide-react"
import { ActionIcon } from "@/components/ActionIcon"
import { ActionFormCard } from "@/components/ActionForm"
import { ImmediateRunDialog, ImmediateRunForm } from "@/components/ImmediateRunForm"
import { PageHeader } from "@/components/PageHeader"
import { ScriptBlock } from "@/components/ScriptBlock"
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
import { Badge } from "@/components/ui/badge"
import { useConfirm } from "@/lib/confirm"
import { copyToClipboard, downloadText, exactDateTime, relativeDate, toCsv } from "@/lib/format"
import { useActions, useCreateRun } from "@/lib/queries"
import type { ActionRow } from "@/lib/api"
import { actionRunModeOf, buildImmediateActionArgs, isRunImmediateAction, type RowCategory } from "@/lib/messageRunConfig"
export function Actions() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data, isPending, isFetching, isError, error, refetch } = useActions()
  const actions: ActionRow[] = data?.actions ?? []
  const sortedActions = [...actions].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.id - b.id,
  )

  const [detail, setDetail] = useState<ActionRow | null>(null)
  const [editing, setEditing] = useState<ActionRow | null>(null)
  const [runRow, setRunRow] = useState<ActionRow | null>(null)

  function handleEdit(row: ActionRow) {
    setDetail(null)
    setEditing(row)
  }

  async function handleQuickRun(row: ActionRow) {
    // run_immediately rows launch straight away (standard confirm); everything
    // else opens the shared run form with the row's label and mode prefilled.
    if (!isRunImmediateAction(row)) {
      setRunRow(row)
      return
    }
    const ok = await confirm({
      action: null,
      title: `Run action ${row.name} now`,
      description: "The action runs immediately with auto-approve. The server appends --yes.",
      confirmLabel: "Run now",
      details: [
        { label: "Action", value: row.label, mono: true },
        { label: "Mode", value: actionRunModeOf(row), mono: true },
      ],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "action",
        args: buildImmediateActionArgs(row.label, "HEAD", actionRunModeOf(row)),
        confirm: true,
      })
      toast.success(`Action ${row.label} started`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
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
  async function handleToggle(row: ActionRow, field: "protected" | "dedicated_top_bar" | "dedicated_overlay") {
    try {
      const on = !(row as unknown as Record<string, unknown>)[field]
      const flag = field.replaceAll("_", "-")
      await createRun.mutateAsync({
        command: "action",
        confirm: false,
        args: ["update", row.label, on ? `--${flag}` : `--no-${flag}`, "--yes"],
      })
      toast.success(`Action ${row.label} ${field} ${on ? "on" : "off"}`)
      void refetch()
    } catch (err) {
      toast.error(errorText(err))
    }
  }
  async function handleMove(row: ActionRow, dir: -1 | 1) {
    const idx = sortedActions.findIndex((a) => a.label === row.label)
    const other = sortedActions[idx + dir]
    if (idx < 0 || !other) return
    try {
      const aOrder = row.sort_order ?? 0
      const bOrder = other.sort_order ?? 0
      const target = bOrder === aOrder ? aOrder + dir : bOrder
      await createRun.mutateAsync({
        command: "action",
        confirm: false,
        args: ["update", row.label, "--sort-order", String(target), "--yes"],
      })
      if (bOrder === aOrder) {
        await createRun.mutateAsync({
          command: "action",
          confirm: false,
          args: ["update", other.label, "--sort-order", String(aOrder), "--yes"],
        })
      }
      toast.success(`Action ${row.label} moved ${dir < 0 ? "up" : "down"}`)
      void refetch()
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
        actions.map((a) => [a.label, a.name, a.icon, actionRunModeOf(a)]),
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
                    {sortedActions.map((a) => (
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
                          <Badge variant="secondary">{actionRunModeOf(a)}</Badge>
                          {isRunImmediateAction(a) ? (
                            <Badge variant="outline" className="ml-1">immediate</Badge>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={isRunImmediateAction(a) ? `Run ${a.name} now` : `Run ${a.name}…`} onClick={() => void handleQuickRun(a)}>
                            <Play aria-hidden />
                          </Button>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{a.category ?? "worktree"}</TableCell>
                        <TableCell className="font-mono text-xs">
                          {a.sort_order ?? 0}
                          <span className="ml-1 inline-flex gap-0.5">
                            <Button variant="ghost" size="sm" title={`Move ${a.name} up`} onClick={() => void handleMove(a, -1)}>▲</Button>
                            <Button variant="ghost" size="sm" title={`Move ${a.name} down`} onClick={() => void handleMove(a, 1)}>▼</Button>
                          </span>
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={a.protected ? `Unprotect ${a.name}` : `Protect ${a.name}`} onClick={() => void handleToggle(a, "protected")}>
                            {a.protected ? "🔒" : "🔓"}
                          </Button>
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={a.dedicated_top_bar ? `Remove top-bar button for ${a.name}` : `Add top-bar button for ${a.name}`} onClick={() => void handleToggle(a, "dedicated_top_bar")}>
                            {a.dedicated_top_bar ? "★" : "☆"}
                          </Button>
                        </TableCell>
                        <TableCell>
                          <Button variant="ghost" size="sm" title={a.dedicated_overlay ? `Remove overlay button for ${a.name}` : `Add overlay button for ${a.name}`} onClick={() => void handleToggle(a, "dedicated_overlay")}>
                            {a.dedicated_overlay ? "◉" : "○"}
                          </Button>
                        </TableCell>
                        <TableCell title={exactDateTime(a.modified_at)}>
                          {relativeDate(a.modified_at)}
                        </TableCell>
                        <TableCell>
                          <div className="flex gap-1">
                            {a.protected ? null : (
                              <>
                                <Button variant="ghost" size="sm" title={`Edit ${a.name}`} onClick={() => handleEdit(a)}>
                                  <Pencil aria-hidden />
                                </Button>
                                <Button variant="ghost" size="sm" title={`Delete ${a.name}`} onClick={() => void handleRemove(a)}>
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
              </div>
            )}
          </CardContent>
        </Card>

        <div className="order-1 grid gap-6 lg:order-2">
          <Card className="h-fit">
            <CardHeader>
              <CardTitle>Run an action now</CardTitle>
              <CardDescription>
                Runs immediately with the standard confirm. Rows flagged run immediately skip this form.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ImmediateRunForm kind="action" />
            </CardContent>
          </Card>
          <ActionFormCard
            key={editing?.label ?? "new"}
            editing={editing}
            onDone={() => { setEditing(null); void refetch() }}
            onCancel={() => setEditing(null)}
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
            <ActionIcon name={detail.icon} className="size-4" />
            <Badge variant="secondary">{actionRunModeOf(detail)}</Badge>
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
      {runRow ? (
        <ImmediateRunDialog
          kind="action"
          category={(runRow.category as RowCategory | undefined) ?? "worktree"}
          lockCategory
          preset={runRow.label}
          onClose={() => setRunRow(null)}
        />
      ) : null}
    </div>
  )
}
