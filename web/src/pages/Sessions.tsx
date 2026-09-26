import { useMemo } from "react"
import { Link, useParams, useSearchParams } from "react-router-dom"
import { SearchableSelect } from "@/components/SearchableSelect"
import { toast } from "sonner"
import { ArrowLeft, Copy, SquareTerminal } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { RunsTable, type RunsTableRow } from "@/components/RunsTable"
import { SessionResumeActions } from "@/components/RunActions"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { type SessionDetail, type SessionRow } from "@/lib/api"
import { useLinks, useRepos, useSession, useSessions } from "@/lib/queries"
import { repoKeyForPath, useRepoTabs } from "@/lib/useRepoTabs"
import { relativeTime, shortId } from "@/lib/format"

/** Every persisted session executed a real runtime session, so both
 * resume buttons always apply. Copy resolves the worktree path via
 * /api/path (falls back to the recorded ref). */

/** Branch → human title: drop `<type>/` prefix, first 50 chars, `-`→space, Title Case.
 * Refs shaped `<tracker>:<KEY>` (e.g. `jira:IPG-959`) carry no slug — callers
 * pass the linked normalized branch when known so the title reads from it. */
export function sessionTitle(ref: string, branch?: string): string {
  const src = branch?.trim()
    ? branch
    : ref.includes("/")
      ? ref.slice(ref.indexOf("/") + 1)
      : ref.replace(/^[^:]+:/, "")
  const words = src.slice(0, 50).replace(/-/g, " ").split(/\s+/).filter(Boolean)
  return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ") || ref
}

/** initiator_command → session kind label. */
export function sessionKind(cmd: string): string {
  const c = cmd.trim().toLowerCase()
  if (c === "start") return "Start (task)"
  if (c === "review") return "Review"
  if (c === "sync") return "Sync"
  return cmd || "—"
}
export function Sessions() {
  const {
    data, isPending, isError, error, refetch,
  } = useSessions()
  const [params, setParams] = useSearchParams()
  const worktreeFilter = (params.get("worktree") ?? "").trim()
  const titleFilter = (params.get("q") ?? "").trim().toLowerCase()
  const allRows: SessionRow[] = data?.sessions ?? []
  const { data: repos } = useRepos()
  const { data: links } = useLinks()
  const worktreePathOf = (ref: string) => links?.worktrees?.[ref]?.worktree ?? ref
  const repoTabs = useRepoTabs(allRows.map((r) => worktreePathOf(r.worktree_ref)), repos)
  const repoFilter = repoTabs.repoFilter
  const repoOf = (ref: string) => repoKeyForPath(worktreePathOf(ref), repos ?? [])
  const worktreeOptions = useMemo(
    () => ["(all worktrees)", ...new Set(allRows.map((r) => r.worktree_ref))].sort(),
    [allRows],
  )
  const rows: SessionRow[] = useMemo(() => {
    let out = allRows
    if (repoFilter) out = out.filter((r) => repoOf(r.worktree_ref) === repoFilter)
    if (worktreeFilter) out = out.filter((r) => r.worktree_ref === worktreeFilter)
    if (titleFilter) {
      out = out.filter((r) =>
        `${r.id} ${r.worktree_ref} ${sessionTitle(r.worktree_ref, links?.worktrees?.[r.worktree_ref]?.branch ?? "")} ${r.initiator_command} ${r.runtime_name} ${r.state}`.toLowerCase().includes(titleFilter),
      )
    }
    return out
  }, [allRows, worktreeFilter, titleFilter, repoFilter, links, repos])

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value.trim()) next.set(key, value.trim())
    else next.delete(key)
    setParams(next, { replace: true })
  }

  function clearWorktree() {
    setParam("worktree", "")
  }

  return (
    <div>
      <PageHeader
        title="Sessions"
        description="Persisted AI-harness sessions — one row per real launch, newest first."
      />
      {repos && repoTabs.tabs.names.length > 0 ? (
        <div role="group" aria-label="Filter by repo" className="mb-3 flex flex-wrap gap-1.5">
          <button
            type="button"
           
            aria-pressed={!repoFilter}
            onClick={() => repoTabs.setRepo("")}
            className={
              !repoFilter
                ? "min-h-11 rounded-full bg-primary px-3.5 text-sm font-medium text-primary-foreground"
                : "min-h-11 rounded-full border px-3.5 text-sm text-muted-foreground hover:text-foreground"
            }
          >
            All repos
          </button>
          {repoTabs.tabs.names.map((n) => (
            <button
              type="button"
              key={n}
             
              aria-pressed={repoFilter === n}
              onClick={() => repoTabs.setRepo(n)}
              className={
                repoFilter === n
                  ? "min-h-11 rounded-full bg-primary px-3.5 text-sm font-medium text-primary-foreground"
                  : "min-h-11 rounded-full border px-3.5 text-sm text-muted-foreground hover:text-foreground"
              }
            >
              {n} ({repoTabs.tabs.counts.get(n) ?? 0})
            </button>
          ))}
        </div>
      ) : null}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="min-w-52 flex-1 sm:max-w-xs">
          <SearchableSelect
            value={worktreeFilter}
            options={worktreeOptions}
            onChange={(v) => setParam("worktree", v === "(all worktrees)" ? "" : v)}
            placeholder="(all worktrees)"
          />
        </div>
        <div className="relative min-w-52 flex-1 sm:max-w-xs">
          <Input
            value={params.get("q") ?? ""}
            onChange={(e) => setParam("q", e.target.value)}
            placeholder="Filter sessions…"
            aria-label="Filter sessions"
          />
        </div>
        {worktreeFilter ? (
          <Button variant="outline" size="sm" onClick={clearWorktree}>
            Clear worktree
          </Button>
        ) : null}
      </div>
      {isPending ? (
        <TableSkeleton rows={5} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No sessions yet"
          description="Run workagent start, review, or sync to record a session."
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>ID</TableHead>
                  <TableHead>Worktree</TableHead>
                  <TableHead>Runtime</TableHead>
                  <TableHead>Command</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="w-20" aria-label="Session actions" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="max-w-64 truncate text-sm text-muted-foreground" title={sessionTitle(s.worktree_ref, links?.worktrees?.[s.worktree_ref]?.branch ?? "")}>
                      {sessionTitle(s.worktree_ref, links?.worktrees?.[s.worktree_ref]?.branch ?? "")}
                    </TableCell>
                    <TableCell className="font-mono text-[13px]">
                      <Link
                        to={`/sessions/${encodeURIComponent(s.id)}`}
                        className="underline decoration-dotted underline-offset-2"
                      >
                        {shortId(s.id)}
                      </Link>
                    </TableCell>
                    <TableCell className="font-mono text-[13px]">
                      {s.worktree_ref}
                    </TableCell>
                    <TableCell>{s.runtime_name}</TableCell>
                    <TableCell className="font-mono text-[13px]">
                      {sessionKind(s.initiator_command)}
                    </TableCell>
                    <TableCell>
                      <StateBadge state={s.state} />
                    </TableCell>
                    <TableCell title={s.created_at}>
                      {relativeTime(Date.parse(s.created_at) / 1000)}
                    </TableCell>
                    <TableCell>
                      <SessionResumeActions sessionId={s.id} worktree={links?.worktrees?.[s.worktree_ref]?.worktree ?? s.worktree_ref} sessionFile={s.file_path} variant="icon" />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export function SessionDetailPage() {
  const { sessionId = "" } = useParams()
  const sessionQ = useSession(sessionId)
  const { data: links } = useLinks()

  if (sessionQ.isPending) return <TableSkeleton rows={5} />
  if (sessionQ.isError || !sessionQ.data) {
    return (
      <ErrorState error={sessionQ.error} onRetry={() => void sessionQ.refetch()} />
    )
  }
  const s = sessionQ.data
  return (
    <div>
      <PageHeader
        title={`Session ${shortId(s.id)}`}
        description={`${s.worktree_ref} · ${s.runtime_name} · ${s.initiator_command}`}
        actions={
          <>
            <SessionResumeActions sessionId={s.id} worktree={links?.worktrees?.[s.worktree_ref]?.worktree ?? s.worktree_ref} sessionFile={s.file_path} variant="outline" />
            <Button variant="outline" size="sm" asChild>
              <Link to="/sessions">
                <ArrowLeft aria-hidden /> Sessions
              </Link>
            </Button>
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <StateBadge state={s.state} />
        {s.transcript === "missing" ? (
          <Badge className="bg-amber-500/15 text-amber-300">
            transcript: missing
          </Badge>
        ) : null}
        <span className="font-mono text-[13px] text-muted-foreground">
          {s.file_path}
        </span>
      </div>
      <Card className="mb-4">
        <CardContent className="pt-4">
          <p className="mb-2 text-sm font-medium">Injected prompt</p>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/40 p-3 font-mono text-[13px]">
            {s.prompt}
          </pre>
        </CardContent>
      </Card>
      <SessionRunsCard s={s} />
    </div>
  )
}

/** Persisted runs of one session, rendered with the shared Runs table
 * (same rows/columns as the Runs page; no live run id to link to). */
function SessionRunsCard({ s }: { s: SessionDetail }) {
  const rows: RunsTableRow[] = s.runs.map((r) => ({
    key: String(r.id),
    label: `#${r.id}`,
    command: r.command,
    args: r.args,
    target: s.worktree_ref,
    state: r.exit_code === null ? undefined : r.exit_code === 0 ? "succeeded" : "failed",
    exitCode: r.exit_code,
    started: Date.parse(r.created_at) / 1000,
    startedTitle: r.created_at,
  }))
  return (
    <Card>
      <CardHeader>
        <CardTitle>Runs ({rows.length})</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No session runs recorded.</p>
        ) : (
          <RunsTable rows={rows} />
        )}
      </CardContent>
    </Card>
  )
}
