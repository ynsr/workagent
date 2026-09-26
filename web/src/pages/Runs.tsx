import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { toast } from "sonner"
import { SquareTerminal, XCircle } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { RunsTable, type RunsTableRow } from "@/components/RunsTable"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { type Run } from "@/lib/api"
import { useCancelRun, useRepos, useResumeRun, useRuns } from "@/lib/queries"
import { repoKeyForPath, useRepoTabs } from "@/lib/useRepoTabs"
import { copyToClipboard, resumeCommand, shortId } from "@/lib/format"
import { Copy } from "lucide-react"

/** Resume-in-terminal + copy-resume-command buttons; only runs that
 * executed a runtime session carry a session_file. */
export function SessionActions({ run }: { run: Run }) {
  const resume = useResumeRun()
  if (!run.session_file) return null
  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Resume session for run ${run.id} in terminal`}
        title="Resume session in terminal"
        disabled={resume.isPending}
        onClick={(e) => {
          e.preventDefault()
          resume
            .mutateAsync(run.id)
            .then(() => toast.success("Terminal opened on the session"))
            .catch((err: unknown) => toast.error(errorText(err)))
        }}
        className="size-9 text-muted-foreground hover:text-foreground"
      >
        <SquareTerminal aria-hidden />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Copy resume command for run ${run.id}`}
        title="Copy resume command"
        onClick={(e) => {
          e.preventDefault()
          copyToClipboard(resumeCommand(run.worktree, run.session_file))
            .then(() => toast.success("Resume command copied"))
            .catch((err: unknown) => toast.error(errorText(err)))
        }}
        className="size-9 text-muted-foreground hover:text-foreground"
      >
        <Copy aria-hidden />
      </Button>
    </>
  )
}

const FILTERS = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "succeeded", label: "Succeeded" },
  { value: "failed", label: "Failed" },
  { value: "needs_input", label: "Needs input" },
  { value: "cancelled", label: "Cancelled" },
] as const

type Filter = (typeof FILTERS)[number]["value"]

function RepoTabsRow({ repoTabs }: { repoTabs: { repoFilter: string; setRepo: (v: string) => void; tabs: { names: string[]; counts: Map<string, number>; other: number } } }) {
  const btn = (active: boolean) =>
    active
      ? "min-h-11 rounded-full bg-primary px-3.5 text-sm font-medium text-primary-foreground"
      : "min-h-11 rounded-full border px-3.5 text-sm text-muted-foreground hover:text-foreground"
  return (
    <div role="group" aria-label="Filter by repo" className="mb-4 flex flex-wrap gap-1.5">
      <button type="button" aria-pressed={!repoTabs.repoFilter} onClick={() => repoTabs.setRepo("")} className={btn(!repoTabs.repoFilter)}>
        All repos
      </button>
      {repoTabs.tabs.names.map((n) => (
        <button type="button" key={n} aria-pressed={repoTabs.repoFilter === n} onClick={() => repoTabs.setRepo(n)} className={btn(repoTabs.repoFilter === n)}>
          {n} ({repoTabs.tabs.counts.get(n) ?? 0})
        </button>
      ))}
      {repoTabs.tabs.other > 0 ? (
        <button type="button" aria-pressed={repoTabs.repoFilter === "(other)"} onClick={() => repoTabs.setRepo("(other)")} className={btn(repoTabs.repoFilter === "(other)")}>
          (other) ({repoTabs.tabs.other})
        </button>
      ) : null}
    </div>
  )
}

function filterRuns(runs: Run[] | undefined, filter: Filter, target: string, repoOf: (r: Run) => string, repo: string): Run[] {
  if (!runs) return []
  let out = runs
  if (filter !== "all") out = out.filter((r) => r.state === filter)
  if (repo) out = out.filter((r) => repoOf(r) === repo)
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
  const { data: repos } = useRepos()
  const [copied, setCopied] = useState(false)

  const target = params.get("target") ?? ""
  const filter = (params.get("state") as Filter | null) ?? "all"

  const repoTabs = useRepoTabs((runs ?? []).map((r) => r.worktree || r.target), repos)
  const repoOf = (r: Run) => repoKeyForPath(r.worktree || r.target, repos ?? [])
  const filtered = useMemo(() => filterRuns(runs, filter, target, repoOf, repoTabs.repoFilter), [runs, filter, target, repoTabs.repoFilter, repos])

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
        description="Child-process runs of workagent commands. Follow a run for its live log."
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
      {repos && repoTabs.tabs.names.length > 0 ? (
        <RepoTabsRow repoTabs={repoTabs} />
      ) : null}

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
        <RunsTable
          rows={filtered.map(
            (run): RunsTableRow => ({
              key: run.id,
              href: `/runs/${run.id}`,
              label: shortId(run.id),
              command: run.command,
              args: run.args,
              target: run.target,
              state: run.state,
              exitCode: run.exit_code,
              started: run.created,
              actions: (
                <>
                  <SessionActions run={run} />
                  {run.state === "running" ? <CancelButton run={run} /> : null}
                </>
              ),
            }),
          )}
        />
      )}
    </div>
  )
}
