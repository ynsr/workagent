import { Link } from "react-router-dom"
import { Badge } from "@/components/ui/badge"
import { exactDateTime, relativeDate } from "@/lib/format"
import type { MessageRow } from "@/lib/api"

/** Expandable detail: full content, timestamps, repo list, thread-sibling link. */
export function MessageDetail({ row }: { row: MessageRow }) {
  const repos = row.repos ?? []
  return (
    <div className="space-y-3">
      <div className="whitespace-pre-wrap rounded-md border bg-muted/40 p-3 text-sm">
        {row.content || <span className="text-muted-foreground">(no content)</span>}
      </div>
      <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Message</dt>
        <dd className="font-mono">{row.message_id}</dd>
        <dt className="text-muted-foreground">Thread</dt>
        <dd>
          <Link className="underline" to={`/messages?thread=${encodeURIComponent(row.thread_id)}`}>
            {row.thread_id || "—"}
          </Link>
        </dd>
        <dt className="text-muted-foreground">App</dt>
        <dd>{row.app_name}</dd>
        <dt className="text-muted-foreground">Sender</dt>
        <dd>{row.sender_username || "—"}</dd>
        <dt className="text-muted-foreground">Sent</dt>
        <dd title={exactDateTime(row.ts)}>{row.ts ? relativeDate(row.ts) : "—"}</dd>
        <dt className="text-muted-foreground">Recorded</dt>
        <dd title={exactDateTime(row.added_at)}>{relativeDate(row.added_at)}</dd>
      </dl>
      <div className="flex flex-wrap gap-1">
        {repos.length === 0 ? (
          <span className="text-sm text-muted-foreground">No linked repos</span>
        ) : (
          repos.map((r) => <Badge key={r} variant="secondary">{r}</Badge>)
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {(row.related_repos ?? []).length === 0 ? (
          <span className="text-sm text-muted-foreground">No related repos</span>
        ) : (
          (row.related_repos ?? []).map((r) => <Badge key={r} variant="outline">{r}</Badge>)
        )}
      </div>
      <div className="flex flex-wrap gap-1">
        {(row.worktrees ?? []).length === 0 ? (
          <span className="text-sm text-muted-foreground">No linked worktrees</span>
        ) : (
          (row.worktrees ?? []).map((w) => <Badge key={w} variant="outline">{w}</Badge>)
        )}
      </div>
    </div>
  )
}
