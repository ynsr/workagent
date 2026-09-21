import { Link, useParams } from "react-router-dom"
import { ArrowLeft } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from "@/components/StatusFeedback"
import { Badge } from "@/components/ui/badge"
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
import { type SessionRow } from "@/lib/api"
import { useSession, useSessions } from "@/lib/queries"
import { relativeTime, shortId } from "@/lib/format"

function StateBadge({ state }: { state: string }) {
  const tone =
    state === "finished"
      ? "bg-emerald-500/15 text-emerald-300"
      : state === "failed"
        ? "bg-red-500/15 text-red-300"
        : "bg-amber-500/15 text-amber-300"
  return <Badge className={tone}>{state}</Badge>
}

export function Sessions() {
  const {
    data, isPending, isError, error, refetch,
  } = useSessions()
  const rows: SessionRow[] = data?.sessions ?? []

  return (
    <div>
      <PageHeader
        title="Sessions"
        description="Persisted AI-harness sessions — one row per real launch, newest first."
      />
      {isPending ? (
        <TableSkeleton rows={5} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="No sessions yet"
          description="Run harness start, review, or sync to record a session."
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>Worktree</TableHead>
                  <TableHead>Runtime</TableHead>
                  <TableHead>Command</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((s) => (
                  <TableRow key={s.id}>
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
                      {s.initiator_command}
                    </TableCell>
                    <TableCell>
                      <StateBadge state={s.state} />
                    </TableCell>
                    <TableCell title={s.created_at}>
                      {relativeTime(Date.parse(s.created_at) / 1000)}
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
          <Button variant="outline" size="sm" asChild>
            <Link to="/sessions">
              <ArrowLeft aria-hidden /> Sessions
            </Link>
          </Button>
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
      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>#</TableHead>
                <TableHead>Command</TableHead>
                <TableHead>Args</TableHead>
                <TableHead>Exit</TableHead>
                <TableHead>Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {s.runs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    No session runs recorded.
                  </TableCell>
                </TableRow>
              ) : (
                s.runs.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{r.id}</TableCell>
                    <TableCell className="font-mono text-[13px]">
                      {r.command}
                    </TableCell>
                    <TableCell className="font-mono text-[13px]">
                      {r.args.join(" ")}
                    </TableCell>
                    <TableCell>
                      {r.exit_code ?? "—"}
                    </TableCell>
                    <TableCell title={r.created_at}>
                      {relativeTime(Date.parse(r.created_at) / 1000)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
