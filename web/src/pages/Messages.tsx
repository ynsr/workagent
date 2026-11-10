import { useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { ChevronDown, ChevronRight, Copy, Download, MessageSquare } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { MessageDetail } from "@/components/MessageDetail"
import { MessageRowActions } from "@/components/MessageRowActions"
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
import { useCreateRun, useMessages } from "@/lib/queries"
import { useConfirm } from "@/lib/confirm"
import { toast } from "sonner"
import type { MessageRow } from "@/lib/api"

export interface MessageFilters {
  q: string
  sender: string
  app: string
  repo: string
  thread: string
}

export const EMPTY_FILTERS: MessageFilters = { q: "", sender: "", app: "", repo: "", thread: "" }

/** Pure helper (unit-tested): case-insensitive substring match over text/sender/app + exact repo/thread. */
export function filterMessages(rows: MessageRow[], f: MessageFilters): MessageRow[] {
  const q = f.q.trim().toLowerCase()
  return rows.filter((r) => {
    if (q && !`${r.message_id} ${r.content} ${r.sender_username}`.toLowerCase().includes(q)) return false
    if (f.sender.trim() && !(r.sender_username ?? "").toLowerCase().includes(f.sender.trim().toLowerCase())) return false
    if (f.app.trim() && (r.app_name ?? "").toLowerCase() !== f.app.trim().toLowerCase()) return false
    if (f.repo.trim() && !(r.repos ?? []).includes(f.repo.trim())) return false
    if (f.thread.trim() && (r.thread_id ?? "") !== f.thread.trim()) return false
    return true
  })
}

function groupByThread(rows: MessageRow[]): { thread: string; rows: MessageRow[] }[] {
  const map = new Map<string, MessageRow[]>()
  for (const r of rows) {
    const k = r.thread_id || "(no thread)"
    if (!map.has(k)) map.set(k, [])
    map.get(k)!.push(r)
  }
  return [...map.entries()].map(([thread, trows]) => ({ thread, rows: trows }))
}

export function Messages() {
  const [params, setParams] = useSearchParams()
  const threadParam = params.get("thread") ?? ""
  const [q, setQ] = useState("")
  const [sender, setSender] = useState("")
  const [app] = useState("")
  const [repo] = useState("")
  const [grouped, setGrouped] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const { data, isPending, isFetching, isError, error, refetch } = useMessages(threadParam || undefined)
  const confirm = useConfirm()
  const createRun = useCreateRun()

  const rows: MessageRow[] = data?.messages ?? []
  const filters: MessageFilters = { q, sender, app, repo, thread: threadParam }
  const visible = useMemo(() => filterMessages(rows, filters), [rows, q, sender, app, repo, threadParam])

  const senders = useMemo(() => [...new Set(rows.map((r) => r.sender_username).filter(Boolean))].sort(), [rows])
  const groups = useMemo(() => groupByThread(visible), [visible])

  async function handleExportJson() {
    try {
      await copyToClipboard(JSON.stringify(visible, null, 2))
      toast.success("Message list JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleDownloadCsv() {
    downloadText(
      "workagent-messages.csv",
      toCsv(
        ["id", "message_id", "thread_id", "app", "sender", "repos", "added_at"],
        visible.map((m) => [String(m.id), m.message_id, m.thread_id, m.app_name, m.sender_username, (m.repos ?? []).join(" "), m.added_at]),
      ),
      "text/csv",
    )
  }

  async function handleDelete(row: MessageRow) {
    const ok = await confirm({ title: `Delete message ${row.message_id}?`, confirmLabel: "Delete" })
    if (!ok) return
    try {
      await createRun.mutateAsync({ command: "message", args: ["remove", row.message_id, "--yes"] })
      toast.success("Message delete started")
      void refetch()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function setThread(t: string) {
    setParams(t ? { thread: t } : {}, { replace: true })
  }

  return (
    <div>
      <PageHeader
        title="Messages"
        description="Registered chat messages (message add / adhoc / validate / draft-reply). Runs go through POST /api/runs."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void handleExportJson()}>
              <Copy aria-hidden /> JSON
            </Button>
            <Button variant="outline" size="sm" onClick={handleDownloadCsv}>
              <Download aria-hidden /> CSV
            </Button>
          </>
        }
      />

      <Card className="mb-4">
        <CardContent className="flex flex-wrap items-end gap-3 pt-4">
          <div className="grid gap-1">
            <Label htmlFor="msg-q">Search</Label>
            <Input id="msg-q" className="h-8 w-56" placeholder="text, id, sender…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="msg-sender">Sender</Label>
            <Input id="msg-sender" className="h-8 w-40" placeholder="any" list="msg-senders" value={sender} onChange={(e) => setSender(e.target.value)} />
            <datalist id="msg-senders">{senders.map((s) => <option key={s} value={s} />)}</datalist>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="msg-thread">Thread</Label>
            <div className="flex items-center gap-1">
              <Input id="msg-thread" className="h-8 w-40" placeholder="any" value={threadParam} onChange={(e) => setThread(e.target.value)} />
              {threadParam && <Button variant="ghost" size="sm" onClick={() => setThread("")}>Clear</Button>}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
            Group by thread
          </label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Messages</CardTitle>
          <CardDescription>GET /api/messages{threadParam ? `?thread=${threadParam}` : ""}</CardDescription>
        </CardHeader>
        <CardContent>
          {isFetching && data ? <RefreshOverlay /> : null}
          {isPending && !data ? (
            <TableSkeleton rows={4} />
          ) : isError && !data ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : visible.length === 0 ? (
            <EmptyState
              icon={<MessageSquare className="size-10" aria-hidden />}
              title={rows.length === 0 ? "No registered messages" : "No messages match the filters"}
              description="Register one with `message add --message-id … --repo …`."
            />
          ) : grouped ? (
            <div className="space-y-4">
              {groups.map((g) => (
                <div key={g.thread}>
                  <div className="mb-1 text-sm font-medium">
                    Thread <span className="font-mono">{g.thread}</span>{" "}
                    <Badge variant="secondary">{g.rows.length}</Badge>
                  </div>
                  <MessageTable rows={g.rows} expanded={expanded} setExpanded={setExpanded} onDelete={(r) => void handleDelete(r)} onRefetch={() => void refetch()} />
                </div>
              ))}
            </div>
          ) : (
            <MessageTable rows={visible} expanded={expanded} setExpanded={setExpanded} onDelete={(r) => void handleDelete(r)} onRefetch={() => void refetch()} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function MessageTable({ rows, expanded, setExpanded, onDelete, onRefetch }: {
  rows: MessageRow[]
  expanded: number | null
  setExpanded: (id: number | null) => void
  onDelete: (row: MessageRow) => void
  onRefetch: () => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8" />
          <TableHead>App</TableHead>
          <TableHead>Sender</TableHead>
          <TableHead>Message</TableHead>
          <TableHead>Thread</TableHead>
          <TableHead>Repos</TableHead>
          <TableHead>Updated</TableHead>
          <TableHead className="w-24" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((m) => (
          <>
            <TableRow key={m.id}>
              <TableCell>
                <Button variant="ghost" size="sm" aria-label={expanded === m.id ? "Collapse" : "Expand"}
                  onClick={() => setExpanded(expanded === m.id ? null : m.id)}>
                  {expanded === m.id ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />}
                </Button>
              </TableCell>
              <TableCell>{m.app_name}</TableCell>
              <TableCell>{m.sender_username || "—"}</TableCell>
              <TableCell className="max-w-80 truncate" title={m.content}>
                <span className="font-mono text-xs text-muted-foreground">{m.message_id}</span>{" "}
                {m.content}
              </TableCell>
              <TableCell className="font-mono text-xs">{m.thread_id || "—"}</TableCell>
              <TableCell>
                <div className="flex max-w-48 flex-wrap gap-1">
                  {(m.repos ?? []).map((r) => <Badge key={r} variant="secondary">{r}</Badge>)}
                </div>
              </TableCell>
              <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={m.added_at}>
                {relativeDate(m.added_at)}
              </TableCell>
              <TableCell>
                <Button variant="ghost" size="sm" onClick={() => onDelete(m)}>Delete</Button>
              </TableCell>
            </TableRow>
            {expanded === m.id && (
              <TableRow key={`${m.id}-detail`}>
                <TableCell colSpan={8}>
                  <div className="space-y-3">
                    <MessageDetail row={m} />
                    <MessageRowActions row={m} onDone={onRefetch} />
                  </div>
                </TableCell>
              </TableRow>
            )}
          </>
        ))}
      </TableBody>
    </Table>
  )
}
