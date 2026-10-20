import { useMemo, useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router-dom"
import { CheckCircle2, Circle, Loader2, Workflow, XCircle } from "lucide-react"
import { toast } from "sonner"
import { PageHeader } from "@/components/PageHeader"
import { ActionDialog } from "@/components/LinkDialogs"
import { SearchableSelect } from "@/components/SearchableSelect"
import {
  EmptyState,
  ErrorState,
  RefreshOverlay,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { FlowRow } from "@/lib/api"
import { exactDateTime, relativeDate, copyToClipboard } from "@/lib/format"
import { useCreateRun, useFlows } from "@/lib/queries"

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      : status === "failed"
        ? "bg-red-500/15 text-red-700 dark:text-red-300"
        : "bg-amber-500/15 text-amber-700 dark:text-amber-300"
  return <Badge className={tone}>{status}</Badge>
}

function StepIcon({ status }: { status: string }) {
  if (status === "ok") return <CheckCircle2 aria-hidden className="size-4 text-emerald-500" />
  if (status === "failed") return <XCircle aria-hidden className="size-4 text-destructive" />
  if (status === "paused") return <Circle aria-hidden className="size-4 text-amber-500" />
  if (status === "running") return <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
  if (status === "skipped") return <Circle aria-hidden className="size-4 text-muted-foreground" />
  return <Circle aria-hidden className="size-4 text-muted-foreground" />
}

export function Flows() {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const [params, setParams] = useSearchParams()
  const worktreeFilter = (params.get("worktree") ?? "").trim()
  const statusFilter = (params.get("status") ?? "").trim()
  const { data, isPending, isFetching, isError, error, refetch } = useFlows(
    worktreeFilter || undefined,
  )
  const [detail, setDetail] = useState<FlowRow | null>(null)
  const [resuming, setResuming] = useState(false)
  const pausedCmd = (detail?.step_results ?? []).find((r) => r.status === "paused")?.detail ?? ""

  async function handleResume(skipStep = false) {
    if (!detail || resuming) return
    const ref = detail.worktree_ref || detail.repo_snapshot?.launch_ref || ""
    if (!ref) {
      toast.error("No worktree or launch ref to resume from")
      return
    }
    setResuming(true)
    try {
      const args = ["run", detail.template_label, ref, "--yes"]
      if (skipStep) args.push("--skip-steps", String(detail.current_step))
      const { run_id } = await createRun.mutateAsync({
        command: "flow",
        args,
        confirm: true,
      })
      toast.success(skipStep ? `Step ${detail.current_step} marked done — flow resumed` : `Flow ${detail.template_label} resumed`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      setDetail(null)
      void refetch()
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setResuming(false)
    }
  }
  const allRows: FlowRow[] = useMemo(() => {
    const rows: FlowRow[] = data?.flows ?? []
    if (!statusFilter) return rows
    return rows.filter((f) => f.status === statusFilter)
  }, [data, statusFilter])
  const worktreeOptions = useMemo(
    () => ["(all worktrees)", ...new Set(allRows.map((f) => f.worktree_ref).filter(Boolean))].sort(),
    [allRows],
  )

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value.trim()) next.set(key, value.trim())
    else next.delete(key)
    setParams(next, { replace: true })
  }

  return (
    <div>
      <PageHeader
        title="Flows"
        description="Flow runs (Start → … → Release), newest first. Scope to one worktree with ?worktree=."
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="min-w-52 flex-1 sm:max-w-xs">
          <SearchableSelect
            value={worktreeFilter || "(all worktrees)"}
            options={worktreeOptions}
            onChange={(v) => setParam("worktree", v === "(all worktrees)" ? "" : v)}
            placeholder="(all worktrees)"
          />
        </div>
        {worktreeFilter ? (
          <Button variant="outline" size="sm" onClick={() => setParam("worktree", "")}>
            Clear worktree
          </Button>
        ) : null}
        {statusFilter ? (
          <Button variant="outline" size="sm" onClick={() => setParam("status", "")}>
            Clear status: {statusFilter} ✕
          </Button>
        ) : null}
      </div>

      {(isPending && !data) ? (
        <TableSkeleton rows={5} />
      ) : (isError && !data) ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : (
        <div className="relative">
          {isFetching && !isPending ? <RefreshOverlay /> : null}
          {isError ? (
            <p role="alert" className="mb-3 text-sm text-destructive">
              {errorText(error)}{" "}
              <button type="button" onClick={() => void refetch()} className="underline underline-offset-2">
                Retry
              </button>
            </p>
          ) : null}
          {allRows.length === 0 ? (
            <EmptyState
              icon={<Workflow className="size-10" aria-hidden />}
              title="No flow runs yet"
              description="Run a flow template from the Dashboard Flows dropdown or Launch — every run appears here."
            >
              <Button asChild size="sm">
                <Link to="/flow-templates">Flow templates</Link>
              </Button>
            </EmptyState>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Flow runs</CardTitle>
                <CardDescription>GET /api/flows{worktreeFilter ? `?worktree=${worktreeFilter}` : ""}</CardDescription>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>ID</TableHead>
                      <TableHead>Template</TableHead>
                      <TableHead>Worktree</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Step</TableHead>
                      <TableHead>Updated</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {allRows.map((f) => (
                      <TableRow key={f.id}>
                        <TableCell className="font-mono text-[13px]">
                          <button
                            type="button"
                            className="underline decoration-dotted underline-offset-2"
                            onClick={() => setDetail(f)}
                          >
                            #{f.id}
                          </button>
                        </TableCell>
                        <TableCell className="font-mono text-[13px]">{f.template_label}</TableCell>
                        <TableCell className="max-w-48 truncate font-mono text-[13px]" title={f.worktree_ref}>
                          {f.worktree_ref || "—"}
                        </TableCell>
                        <TableCell>
                          <StatusBadge status={f.status} />
                        </TableCell>
                        <TableCell
                          className="font-mono text-[13px]"
                          title={`${f.step_results?.length ?? 0} step result${(f.step_results?.length ?? 0) === 1 ? "" : "s"} recorded`}
                        >
                          step {f.current_step}
                        </TableCell>
                        <TableCell title={exactDateTime(f.modified_at)}>
                          {relativeDate(f.modified_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {detail ? (
        <ActionDialog
          title={`Flow #${detail.id} — ${detail.template_label}`}
          description={`${detail.worktree_ref || "no worktree yet"} · ${detail.status}`}
          onClose={() => setDetail(null)}
          footer={
            <>
              {detail.status === "paused" ? (
                <Button
                  size="sm"
                  onClick={() => void handleResume()}
                  disabled={resuming}
                  title={pausedCmd ? `Resume from step ${detail.current_step}: ${pausedCmd}` : "Resume from the paused step"}
                >
                  {resuming ? "Resuming…" : "Resume"}
                </Button>
              ) : null}
              {detail.status === "paused" ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void handleResume(true)}
                  disabled={resuming}
                  title={`Mark step ${detail.current_step} done (already finished by hand) and continue from step ${detail.current_step + 1}`}
                >
                  Skip step {detail.current_step}
                </Button>
              ) : null}
              {detail.status === "paused" && pausedCmd ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    copyToClipboard(pausedCmd)
                      .then(() => toast.success("Manual command copied"))
                      .catch(() => toast.error("Copy failed"))
                  }}
                  title={pausedCmd}
                >
                  Copy command
                </Button>
              ) : null}
              <Button variant="outline" size="sm" asChild>
                <Link to={`/sessions?flow=${detail.id}`}>Sessions</Link>
              </Button>
              <Button variant="outline" size="sm" asChild>
                <Link to={`/runs?flow=${detail.id}`}>Runs</Link>
              </Button>
            </>
          }
        >
          <ol className="grid gap-2">
            {(detail.step_results ?? []).map((r, i) => (
              <li key={i} className="flex items-start gap-2 rounded-md border p-2 text-sm">
                <StepIcon status={r.status} />
                <div className="min-w-0">
                  <p className="font-mono text-[13px]">
                    {i + 1}. {r.kind} — {r.status}
                  </p>
                  {r.detail ? (
                    <p className="truncate text-muted-foreground" title={r.detail}>
                      {r.detail}
                    </p>
                  ) : null}
                </div>
              </li>
            ))}
            {(detail.step_results ?? []).length === 0 ? (
              <li className="text-sm text-muted-foreground">No steps executed yet.</li>
            ) : null}
          </ol>
          {detail.repo_snapshot?.repo ? (
            <p className="text-sm text-muted-foreground">
              Repo snapshot: <span className="font-mono text-[13px]">{detail.repo_snapshot.repo}</span>
            </p>
          ) : null}
        </ActionDialog>
      ) : null}
    </div>
  )
}
