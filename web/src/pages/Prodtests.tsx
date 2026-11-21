import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { Copy, Download, FlaskConical } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { PageHeader } from "@/components/PageHeader"
import {
  EmptyState,
  ErrorState,
  RefreshOverlay,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { copyToClipboard, downloadText, relativeDate, toCsv } from "@/lib/format"
import { useCreateRun, useProdtests } from "@/lib/queries"
import { useConfirm } from "@/lib/confirm"
import { getVerbose } from "@/lib/settings"
import { toast } from "sonner"
import type { ProdtestRow } from "@/lib/api"

export interface ProdtestFilters {
  q: string
  status: string
}

export const EMPTY_PRODTEST_FILTERS: ProdtestFilters = { q: "", status: "" }

/** Pure helper (unit-tested): extract filename from a full plan path. */
export function planBasename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

/** Pure helper (unit-tested): case-insensitive substring match over issue/plan/worktree + exact status. */
export function filterProdtests(rows: ProdtestRow[], f: ProdtestFilters): ProdtestRow[] {
  const q = f.q.trim().toLowerCase()
  return rows.filter((r) => {
    if (f.status && (r.status ?? "") !== f.status) return false
    if (!q) return true
    const hay = [r.issue_key ?? "", r.plan_path ?? "", r.worktree_ref ?? "", r.last_run_id ?? ""]
      .join("\n")
      .toLowerCase()
    return hay.includes(q)
  })
}

const STATUS_TONE: Record<string, string> = {
  pending: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  pending_prodtest: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  running: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  passed: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  failed: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
  blocked: "border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300",
}

/** Verified against src/workagent/cli_prodtest_{plan,run,checks}.py + cli_release.py:
 * plan writes `pending` (_finish_plan; preserved pending_prodtest/running means
 * "released, awaiting run" survive re-plan), release writes `pending_prodtest`
 * (_record_release), a headless run briefly writes `running` on lock acquire
 * (_run_locked) before the verdict upsert (`passed`/`failed`/`blocked` via
 * _finish_run; pre-harness failures mark `blocked`, GC skips pending_prodtest/running). */
export const PRODTEST_STATUS_HELP: Record<string, string> = {
  pending: "Plan generated/validated, awaiting release. Re-plan after a completed run resets here.",
  pending_prodtest: "Release SHA recorded, awaiting run. Survives re-plan; blocks GC.",
  running: "Run in progress (per-issue lock held).",
  passed: "Last run passed at the recorded release SHA.",
  failed: "Last run failed — follow-up issue/MR loop applies.",
  blocked: "Run couldn't complete (timeout, version gate, unverifiable plan).",
}

export function Prodtests() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const issueParam = params.get("issue") ?? ""
  const [q, setQ] = useState("")
  const [status, setStatus] = useState("")
  const { data, isPending, isFetching, isError, error, refetch } = useProdtests()
  const confirm = useConfirm()
  const createRun = useCreateRun()

  const rows: ProdtestRow[] = data?.prodtests ?? []
  const filters: ProdtestFilters = { q: issueParam || q, status }
  const visible = useMemo(() => filterProdtests(rows, filters), [rows, issueParam, q, status])
  const statuses = useMemo(() => [...new Set(rows.map((r) => r.status).filter(Boolean))].sort(), [rows])

  function setIssue(issue: string) {
    setParams(issue ? { issue } : {}, { replace: true })
  }

  function handleExportJson() {
    copyToClipboard(JSON.stringify(visible, null, 2))
      .then(() => toast.success("Prodtest list JSON copied"))
      .catch((err) => toast.error(errorText(err)))
  }

  function handleDownloadCsv() {
    downloadText(
      "workagent-prodtests.csv",
      toCsv(
        ["issue", "status", "plan", "revision", "risk", "release", "last_run", "worktree_ref", "updated_at"],
        visible.map((r) => [
          r.issue_key,
          r.status,
          r.plan_path ?? "",
          String(r.plan_revision ?? ""),
          r.plan_risk ?? "",
          r.release_sha ?? "",
          r.last_run_id ?? "",
          r.worktree_ref ?? "",
          r.updated_at ?? "",
        ]),
      ),
      "text/csv",
    )
  }

  async function handleDelete(issue: string) {
    const ok = await confirm({
      action: "prodtest",
      title: `Delete prodtest for ${issue}`,
      description: "Drops the stored prodtest item row (plan results stay on disk).",
      destructive: true,
      confirmLabel: `Delete ${issue}`,
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "prodtest",
        args: ["delete", issue],
        confirm: true,
      })
      toast.success(`Delete ${issue} started`, {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
      void refetch()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCleanupPassed() {
    const passed = rows.filter((r) => r.status === "passed").length
    if (passed === 0) {
      toast.info("No passed prodtests to clean up")
      return
    }
    const ok = await confirm({
      action: "prodtest",
      title: `Delete ${passed} passed prodtest${passed === 1 ? "" : "s"}`,
      description: "Drops every prodtest item whose status is passed.",
      destructive: true,
      confirmLabel: `Delete ${passed} passed`,
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "prodtest",
        args: ["cleanup", "--yes"],
        confirm: true,
      })
      toast.success("Prodtest cleanup started", {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
      void refetch()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div>
      <PageHeader
        title="Prodtests"
        description="Post-release production tests, decoupled from worktree flow (prodtest plan/run). Runs go through POST /api/runs."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={handleExportJson}>
              <Copy aria-hidden /> JSON
            </Button>
            <Button variant="outline" size="sm" onClick={handleDownloadCsv}>
              <Download aria-hidden /> CSV
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleCleanupPassed()}
              title="Deletes every prodtest item whose status is passed (prodtest cleanup --yes)"
            >
              Cleanup passed
            </Button>
            <Button variant="outline" size="sm" onClick={() => navigate("/prodtest-launch")}>
              New prodtest
            </Button>
          </>
        }
      />

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div className="grid gap-1">
            <Label htmlFor="pt-q">Search</Label>
            <Input id="pt-q" className="h-8 w-56" placeholder="issue, plan, worktree…" value={issueParam || q} onChange={(e) => { setIssue(""); setQ(e.target.value) }} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="pt-status">Status</Label>
            <Input id="pt-status" className="h-8 w-40" placeholder="any" list="pt-statuses" value={status} onChange={(e) => setStatus(e.target.value)} />
            <datalist id="pt-statuses">{statuses.map((s) => <option key={s} value={s} />)}</datalist>
          </div>
          {issueParam && <Button variant="ghost" size="sm" onClick={() => setIssue("")}>Clear issue filter</Button>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Prodtests</CardTitle>
          {getVerbose() ? (
            <CardDescription>GET /api/prodtests{issueParam ? `?issue=${issueParam} (filtered)` : ""}</CardDescription>
          ) : null}
        </CardHeader>
        <CardContent>
          {isFetching && data ? <RefreshOverlay /> : null}
          {isPending && !data ? (
            <TableSkeleton rows={4} />
          ) : isError && !data ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : visible.length === 0 ? (
            <EmptyState
              icon={<FlaskConical className="size-10" aria-hidden />}
              title={rows.length === 0 ? "No prodtest items" : "No prodtests match the filters"}
              description="Generate one with `workagent prodtest plan <issue>`, or enable Create Prod Test at cleanup."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Issue</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Plan</TableHead>
                  <TableHead>Release</TableHead>
                  <TableHead>Last run</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((r) => (
                  <TableRow key={r.issue_key}>
                    <TableCell className="font-mono text-[13px]">{r.issue_key}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={STATUS_TONE[r.status] ?? ""}
                        title={PRODTEST_STATUS_HELP[r.status] ? `${r.status} — ${PRODTEST_STATUS_HELP[r.status]}` : r.status}
                      >
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-[13px]">
                      {r.plan_path ? (
                        <div className="inline-flex max-w-full items-center gap-1">
                          <Badge
                            variant="secondary"
                            className="max-w-[240px] truncate font-mono text-xs font-normal"
                            title={r.plan_path}
                          >
                            {planBasename(r.plan_path)}
                            {r.plan_revision ? ` · rev${r.plan_revision}` : ""}
                            {r.plan_risk ? ` · ${r.plan_risk}` : ""}
                          </Badge>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="size-6 shrink-0 p-0"
                            title={`Copy full path: ${r.plan_path}`}
                            aria-label={`Copy plan path for ${r.issue_key}`}
                            onClick={() =>
                              copyToClipboard(r.plan_path!)
                                .then(() => toast.success("Plan path copied"))
                                .catch((err) => toast.error(errorText(err)))
                            }
                          >
                            <Copy className="size-3" aria-hidden />
                          </Button>
                        </div>
                      ) : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-[13px]" title={r.release_sha ?? ""}>
                      {r.release_sha ? (r.release_sha.length > 12 ? r.release_sha.slice(0, 12) : r.release_sha) : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-[13px]" title={r.run_results ?? ""}>
                      {r.last_run_id || "—"}
                    </TableCell>
                    <TableCell className="text-[13px] text-muted-foreground">
                      {r.updated_at ? relativeDate(r.updated_at) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="sm" onClick={() => navigate(`/prodtest-launch?ref=${encodeURIComponent(r.issue_key)}`)}>
                          Open
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleDelete(r.issue_key)}
                          title={`Delete prodtest item ${r.issue_key}`}
                        >
                          Delete
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
