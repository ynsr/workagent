import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { Copy, SquareTerminal, XCircle } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { RunStateBadge } from "@/components/StateBadge"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { type Run } from "@/lib/api"
import { useCancelRun, useRuns } from "@/lib/queries"
import { argsText, copyToClipboard, relativeTime, shortId } from "@/lib/format"

const FILTERS = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
  { value: "needs_input", label: "Needs input" },
  { value: "cancelled", label: "Cancelled" },
] as const

type Filter = (typeof FILTERS)[number]["value"]

function filterRuns(runs: Run[] | undefined, filter: Filter, target: string): Run[] {
  if (!runs) return []
  let out = runs
  if (filter !== "all") out = out.filter((r) => r.state === filter)
  if (target) {
    const t = target.toLowerCase()
    out = out.filter(
      (r) =>
        r.target.toLowerCase().includes(t) ||
        r.args.some((a) => a.toLowerCase().includes(t)),
    )
  }
  return out.sort((a, b) => b.created - a.created)
}

function CancelButton({ run }: { run: Run }) {
  const cancel = useCancelRun()
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Cancel run ${run.id}`}
      title={`Cancel run ${run.id}`}
      disabled={cancel.isPending}
      onClick={(e) => {
        e.preventDefault()
        cancel
          .mutateAsync(run.id)
          .then(() => toast.success(`Cancelled ${shortId(run.id)}`))
          .catch((err: unknown) => toast.error(errorText(err)))
      }}
      className="size-9 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
    >
      <XCircle aria-hidden />
    </Button>
  )
}

export function Runs() {
  const [params, setParams] = useSearchParams()
  const { data: runs, isPending, isError, error, refetch } = useRuns()
  const [copied, setCopied] = useState(false)

  const target = params.get("target") ?? ""
  const filter = (params.get("state") as Filter | null) ?? "all"

  const filtered = useMemo(() => filterRuns(runs, filter, target), [runs, filter, target])

  function setFilter(next: Filter) {
    const p = new URLSearchParams(params)
    if (next === "all") p.delete("state")
    else p.set("state", next)
    setParams(p, { replace: true })
  }

  function clearTarget() {
    const p = new URLSearchParams(params)
    p.delete("target")
    setParams(p, { replace: true })
  }

  async function handleCopyJson() {
    if (!runs) return
    try {
      await copyToClipboard(JSON.stringify(runs, null, 2))
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
      toast.success("Runs JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div>
      <PageHeader
        title="Runs"
        description="Child-process runs of harness commands. Follow a run for its live log."
        actions={
          <Button variant="outline" size="sm" onClick={handleCopyJson}>
            <Copy aria-hidden /> {copied ? "Copied" : "JSON"}
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="Filter by state" className="flex flex-wrap gap-1.5">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              role="tab"
              aria-selected={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={
                filter === f.value
                  ? "min-h-11 rounded-full bg-primary px-3.5 text-sm font-medium text-primary-foreground"
                  : "min-h-11 rounded-full border px-3.5 text-sm text-muted-foreground hover:text-foreground"
              }
            >
              {f.label}
            </button>
          ))}
        </div>
        {target ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 text-sm">
            <span className="font-mono text-[13px]">{target}</span>
            <button
              aria-label={`Clear target filter ${target}`}
              onClick={clearTarget}
              className="text-muted-foreground hover:text-foreground"
            >
              <XCircle aria-hidden className="size-4" />
            </button>
          </span>
        ) : null}
      </div>

      {isPending ? (
        <TableSkeleton rows={5} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<SquareTerminal className="size-10" aria-hidden />}
          title={runs && runs.length > 0 ? "No runs match this filter" : "No runs yet"}
          description={
            runs && runs.length > 0
              ? "Try another state filter or clear the target filter."
              : "Launch a command from the Dashboard or Launch page — every run appears here with its live log."
          }
        >
          <Button asChild size="sm">
            <Link to="/launch">Launch</Link>
          </Button>
        </EmptyState>
      ) : (
        <>
          {/* Table ≥sm */}
          <div className="hidden rounded-xl border sm:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run</TableHead>
                  <TableHead>Command</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead className="text-right">Exit</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead className="w-12" aria-label="Cancel" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="font-mono text-[13px]">
                      <Link
                        to={`/runs/${run.id}`}
                        className="underline-offset-2 hover:underline"
                      >
                        {shortId(run.id)}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <span className="font-medium">{run.command}</span>{" "}
                      <span className="font-mono text-[13px] text-muted-foreground">
                        {argsText(run.args)}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-[13px]">{run.target}</TableCell>
                    <TableCell>
                      <RunStateBadge state={run.state} />
                    </TableCell>
                    <TableCell className="text-right font-mono text-[13px]">
                      {run.exit_code ?? "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {relativeTime(run.created)}
                    </TableCell>
                    <TableCell>
                      {run.state === "running" ? <CancelButton run={run} /> : null}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {/* Cards <sm */}
          <div className="space-y-3 sm:hidden">
            {filtered.map((run) => (
              <Card key={run.id} className="py-4">
                <CardContent className="space-y-2 px-4">
                  <div className="flex items-center justify-between gap-2">
                    <Link
                      to={`/runs/${run.id}`}
                      className="font-mono text-[13px] font-semibold underline-offset-2 hover:underline"
                    >
                      {shortId(run.id)}
                    </Link>
                    <RunStateBadge state={run.state} />
                  </div>
                  <p className="text-sm">
                    <span className="font-medium">{run.command}</span>{" "}
                    <span className="font-mono text-[13px] text-muted-foreground">
                      {argsText(run.args)}
                    </span>
                  </p>
                  <p className="truncate font-mono text-[13px] text-muted-foreground" title={run.target}>
                    {run.target}
                  </p>
                  <div className="flex items-center justify-between border-t pt-2 text-xs text-muted-foreground">
                    <span>
                      exit {run.exit_code ?? "—"} · {relativeTime(run.created)}
                    </span>
                    {run.state === "running" ? <CancelButton run={run} /> : null}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
