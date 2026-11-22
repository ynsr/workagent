import { useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { CalendarClock, Play, Plus, Sparkles, Trash2, Edit2 } from "lucide-react"
import {
  api,
  type CreateScheduleInput,
  type ScheduleRecord,
  type ScheduleStarterTemplate,
  type ScheduleTargetKind,
} from "@/lib/api"
import { queryKeys } from "@/lib/queries"
import { getVerbose } from "@/lib/settings"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"

const DEFAULT_FORM: CreateScheduleInput = {
  name: "",
  cron_expr: "0 9 * * 1-5",
  timezone: "UTC",
  target_kind: "command",
  target_label: "",
  args_json: "{}",
  enabled: true,
}

function formatTs(iso: string): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

export function Schedules() {
  const qc = useQueryClient()
  const verbose = getVerbose()
  const confirm = useConfirm()

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.schedules,
    queryFn: () => api.schedules(),
  })

  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<CreateScheduleInput>(DEFAULT_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [previewRuns, setPreviewRuns] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: queryKeys.schedules })
  }

  const saveMut = useMutation({
    mutationFn: async (input: CreateScheduleInput) => {
      if (editingId !== null) {
        return api.updateSchedule(editingId, input)
      }
      return api.createSchedule(input)
    },
    onSuccess: () => {
      setForm(DEFAULT_FORM)
      setEditingId(null)
      setFormError(null)
      setPreviewRuns([])
      invalidate()
    },
    onError: (err: unknown) => {
      setFormError(err instanceof Error ? err.message : String(err))
    },
  })

  const toggleMut = useMutation({
    mutationFn: (s: ScheduleRecord) => api.updateSchedule(s.id, { enabled: !s.enabled }),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      toast.error(errorText(err))
    },
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteSchedule(id),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      toast.error(errorText(err))
    },
  })

  const runNowMut = useMutation({
    mutationFn: (id: number) => api.runScheduleNow(id, { confirm: true }),
    onSuccess: () => {
      invalidate()
      void qc.invalidateQueries({ queryKey: queryKeys.runs })
    },
    onError: (err: unknown) => {
      toast.error(errorText(err))
    },
  })

  const handleDelete = async (s: ScheduleRecord) => {
    const ok = await confirm({
      title: `Delete schedule ${s.name}`,
      description: "Deletes the schedule row. Already-dispatched runs are untouched.",
      destructive: true,
      confirmLabel: "Delete schedule",
      details: [
        { label: "Name", value: s.name },
        { label: "Schedule", value: s.cron_expr, mono: true },
        { label: "Target", value: `${s.target_kind} ${s.target_label}`, mono: true },
      ],
    })
    if (!ok) return
    deleteMut.mutate(s.id)
  }

  const previewMut = useMutation({
    mutationFn: ({ cron_expr, timezone }: { cron_expr: string; timezone: string }) =>
      api.previewSchedule(cron_expr, timezone, 5),
    onSuccess: (res) => {
      setPreviewRuns(res.next_runs)
      setPreviewError(null)
    },
    onError: (err: unknown) => {
      setPreviewRuns([])
      setPreviewError(err instanceof Error ? err.message : String(err))
    },
  })

  const applyStarter = (tpl: ScheduleStarterTemplate) => {
    setEditingId(null)
    setForm({
      name: tpl.name,
      cron_expr: tpl.cron_expr,
      timezone: tpl.timezone,
      target_kind: tpl.target_kind,
      target_label: tpl.target_label,
      args_json: tpl.args_json || "{}",
      enabled: true,
    })
    setFormError(null)
    previewMut.mutate({ cron_expr: tpl.cron_expr, timezone: tpl.timezone })
  }

  const startEdit = (s: ScheduleRecord) => {
    setEditingId(s.id)
    setForm({
      name: s.name,
      cron_expr: s.cron_expr,
      timezone: s.timezone,
      target_kind: s.target_kind,
      target_label: s.target_label,
      args_json: s.args_json || "{}",
      enabled: s.enabled,
    })
    setFormError(null)
    previewMut.mutate({ cron_expr: s.cron_expr, timezone: s.timezone })
  }

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    saveMut.mutate(form)
  }

  const schedules = data?.schedules ?? []
  const starters = data?.starters ?? []

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2">
            <CalendarClock className="h-5 w-5" />
            Schedules
          </h1>
          <p className="text-sm text-muted-foreground">
            Recurring Cron and RRULE schedules for flow templates, custom actions, and maintenance commands.
          </p>
        </div>
      </div>

      {/* Starter templates */}
      {starters.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Sparkles className="h-4 w-4" />
              Starter Templates
            </CardTitle>
            <CardDescription>
              Pre-fill the schedule form with common maintenance and review routines.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-3">
            {starters.map((tpl) => (
              <div
                key={tpl.id}
                className="flex flex-col justify-between rounded-md border p-3 text-sm"
              >
                <div className="space-y-1">
                  <div className="font-medium">{tpl.name}</div>
                  <p className="text-xs text-muted-foreground">{tpl.description}</p>
                  <div className="pt-1 font-mono text-xs text-muted-foreground">
                    {tpl.cron_expr} ({tpl.timezone}) · {tpl.target_label}
                  </div>
                </div>
                <div className="pt-3">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => applyStarter(tpl)}
                  >
                    <Plus className="mr-1 h-3.5 w-3.5" />
                    Use template
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Create / Edit form */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {editingId !== null ? `Edit Schedule #${editingId}` : "New Schedule"}
          </CardTitle>
          {verbose && <CardDescription>POST /api/schedules · POST /api/schedules/preview</CardDescription>}
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="sched-name">Name</Label>
                <Input
                  id="sched-name"
                  required
                  placeholder="Daily unreviewed PR review"
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sched-cron">Cron / RRULE Expression</Label>
                <div className="flex gap-2">
                  <Input
                    id="sched-cron"
                    required
                    placeholder="0 9 * * 1-5"
                    className="font-mono"
                    value={form.cron_expr}
                    onChange={(e) => setForm((f) => ({ ...f, cron_expr: e.target.value }))}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => previewMut.mutate({ cron_expr: form.cron_expr, timezone: form.timezone })}
                    disabled={previewMut.isPending}
                  >
                    Preview
                  </Button>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sched-tz">Timezone (IANA)</Label>
                <Input
                  id="sched-tz"
                  placeholder="UTC"
                  value={form.timezone}
                  onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sched-kind">Target Kind</Label>
                <select
                  id="sched-kind"
                  className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
                  value={form.target_kind}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, target_kind: e.target.value as ScheduleTargetKind }))
                  }
                >
                  <option value="command">command</option>
                  <option value="flow">flow</option>
                  <option value="action">action</option>
                </select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sched-label">Target Label / Command</Label>
                <Input
                  id="sched-label"
                  required
                  placeholder={
                    form.target_kind === "command"
                      ? "review --all --yes"
                      : form.target_kind === "flow"
                        ? "template-label"
                        : "action-label"
                  }
                  value={form.target_label}
                  onChange={(e) => setForm((f) => ({ ...f, target_label: e.target.value }))}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sched-args">Args (JSON)</Label>
                <Input
                  id="sched-args"
                  className="font-mono"
                  placeholder="{}"
                  value={form.args_json ?? "{}"}
                  onChange={(e) => setForm((f) => ({ ...f, args_json: e.target.value }))}
                />
              </div>
            </div>

            {previewError && (
              <p className="text-xs text-destructive" role="alert">
                Preview error: {previewError}
              </p>
            )}
            {previewRuns.length > 0 && (
              <div className="rounded-md bg-muted/50 p-3 text-xs">
                <div className="font-medium mb-1">Next {previewRuns.length} runs (UTC):</div>
                <ul className="list-disc pl-4 font-mono space-y-0.5">
                  {previewRuns.map((ts) => (
                    <li key={ts}>{ts}</li>
                  ))}
                </ul>
              </div>
            )}

            {formError && (
              <p className="text-xs text-destructive" role="alert">
                {formError}
              </p>
            )}

            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={Boolean(form.enabled)}
                  onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
                />
                Enabled
              </label>
              <Button type="submit" size="sm" disabled={saveMut.isPending}>
                {editingId !== null ? "Save changes" : "Create schedule"}
              </Button>
              {editingId !== null && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setEditingId(null)
                    setForm(DEFAULT_FORM)
                    setPreviewRuns([])
                  }}
                >
                  Cancel
                </Button>
              )}
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Schedules list */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Configured Schedules ({schedules.length})</CardTitle>
          {verbose && <CardDescription>GET /api/schedules</CardDescription>}
        </CardHeader>
        <CardContent>
          {isLoading && <p className="text-sm text-muted-foreground">Loading schedules…</p>}
          {error && (
            <p className="text-sm text-destructive">
              Failed to load schedules: {error instanceof Error ? error.message : String(error)}
            </p>
          )}
          {!isLoading && !error && schedules.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No schedules configured yet. Pick a starter template above or create one manually.
            </p>
          )}
          {schedules.length > 0 && (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Schedule</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Next Run</TableHead>
                  <TableHead>Last Run</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {schedules.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-medium">{s.name}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {s.cron_expr}
                      <span className="ml-1 text-muted-foreground">({s.timezone})</span>
                    </TableCell>
                    <TableCell className="text-xs">
                      <Badge variant="outline" className="mr-1.5">
                        {s.target_kind}
                      </Badge>
                      <span className="font-mono">{s.target_label}</span>
                    </TableCell>
                    <TableCell className="text-xs">{formatTs(s.next_run_at)}</TableCell>
                    <TableCell className="text-xs">
                      <div className="flex items-center gap-2">
                        <span>{formatTs(s.last_run_at)}</span>
                        {s.last_run_id !== null && (
                          <Link
                            to={`/runs/db-${s.last_run_id}`}
                            className="text-primary underline-offset-2 hover:underline"
                          >
                            Run #{s.last_run_id}
                          </Link>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Button
                        type="button"
                        size="sm"
                        variant={s.enabled ? "secondary" : "outline"}
                        onClick={() => toggleMut.mutate(s)}
                      >
                        {s.enabled ? "Enabled" : "Paused"}
                      </Button>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="inline-flex items-center gap-1">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          title="Run now"
                          onClick={() => runNowMut.mutate(s.id)}
                          disabled={runNowMut.isPending}
                        >
                          <Play className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          title="Edit"
                          onClick={() => startEdit(s)}
                        >
                          <Edit2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          title="Delete"
                          onClick={() => void handleDelete(s)}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
