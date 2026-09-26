import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, Plus, Tags, Trash2 } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import {
  EmptyState,
  ErrorState,
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
import { Input } from "@/components/ui/input"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Label } from "@/components/ui/label"
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
import { copyToClipboard, downloadText, toCsv } from "@/lib/format"
import { useCreateRun, useTrackers } from "@/lib/queries"
import type { TrackerRow } from "@/lib/api"

export function Trackers() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data, isPending, isError, error, refetch } = useTrackers()
  const trackers = data?.trackers ?? []

  const [key, setKey] = useState("")
  const [vendor, setVendor] = useState("")
  const [remoteUrl, setRemoteUrl] = useState("")
  const [adding, setAdding] = useState(false)

  async function handleAdd() {
    if (!key.trim() || !remoteUrl.trim()) return
    setAdding(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "tracker",
        args: [
          "add",
          key.trim(),
          ...(vendor.trim() ? ["--vendor", vendor.trim()] : []),
          "--remote-url",
          remoteUrl.trim(),
        ],
      })
      toast.success("Tracker add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      setKey("")
      setVendor("")
      setRemoteUrl("")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(row: TrackerRow) {
    const ok = await confirm({
      action: "tracker remove",
      title: `Remove tracker ${row.key}`,
      description: "Deletes the tracker row; its repo links cascade.",
      destructive: true,
      confirmLabel: "Remove tracker",
      details: [
        { label: "Key", value: row.key, mono: true },
        ...(row.vendor ? [{ label: "Vendor", value: row.vendor, mono: true }] : []),
        ...(row.remote_url ? [{ label: "URL", value: row.remote_url, mono: true }] : []),
      ],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "tracker",
        args: ["remove", row.key],
      })
      toast.success("Tracker remove started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCopyJson() {
    if (!trackers) return
    try {
      await copyToClipboard(JSON.stringify(trackers, null, 2))
      toast.success("Tracker list JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleDownloadCsv() {
    downloadText(
      "workagent-trackers.csv",
      toCsv(
        ["key", "vendor", "remote_url", "repos"],
        trackers.map((t) => [t.key, t.vendor, t.remote_url, String(t.repos)]),
      ),
      "text/csv",
    )
  }

  return (
    <div>
      <PageHeader
        title="Trackers"
        description="Issue trackers (tracker list / tracker add / tracker remove). Repos link to these rows."
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
            <CardTitle>Issue trackers</CardTitle>
            <CardDescription>GET /api/trackers</CardDescription>
          </CardHeader>
          <CardContent>
            {isPending ? (
              <TableSkeleton rows={4} />
            ) : isError ? (
              <ErrorState error={error} onRetry={() => void refetch()} />
            ) : trackers.length === 0 ? (
              <EmptyState
                icon={<Tags className="size-10" aria-hidden />}
                title="No trackers"
                description="Add one with the form — key, vendor, and web URL (vendor/URL derive from the key when blank)."
              />
            ) : (
              <>
                {/* Table ≥sm */}
                <div className="hidden sm:block">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Key</TableHead>
                        <TableHead>Vendor</TableHead>
                        <TableHead>Remote URL</TableHead>
                        <TableHead>Repos</TableHead>
                        <TableHead className="w-12" aria-label="Remove" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {trackers.map((t) => (
                        <TableRow key={t.key}>
                          <TableCell className="font-mono text-[13px]">{t.key}</TableCell>
                          <TableCell>
                            {t.vendor ? (
                              <Badge variant="secondary" className="font-mono">
                                {t.vendor}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell className="max-w-72 truncate font-mono text-[13px]" title={t.remote_url}>
                            {t.remote_url ? (
                              <a
                                href={t.remote_url}
                                target="_blank"
                                rel="noreferrer"
                                className="underline decoration-muted-foreground/50 underline-offset-2 hover:decoration-foreground"
                              >
                                {t.remote_url}
                              </a>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell>{t.repos}</TableCell>
                          <TableCell>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Remove tracker ${t.key}`}
                              title={`Remove tracker ${t.key}`}
                              onClick={() => void handleRemove(t)}
                              className="size-11 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                            >
                              <Trash2 aria-hidden />
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                {/* Cards <sm */}
                <div className="space-y-3 sm:hidden">
                  {trackers.map((t) => (
                    <div key={t.key} className="rounded-xl border bg-card p-4">
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-mono text-sm">{t.key}</p>
                        {t.vendor ? (
                          <Badge variant="secondary" className="font-mono">
                            {t.vendor}
                          </Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 break-all font-mono text-[13px] text-muted-foreground">
                        {t.remote_url || "—"} · {t.repos} repos
                      </p>
                      <div className="mt-3 flex justify-end border-t pt-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleRemove(t)}
                          className="min-h-11 text-destructive hover:bg-destructive/10"
                        >
                          <Trash2 aria-hidden /> Remove
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card className="order-1 h-fit lg:order-2">
          <CardHeader>
            <CardTitle>Add a tracker</CardTitle>
            <CardDescription>
              tracker add KEY --remote-url [--vendor] — vendor derives from the key when blank.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="tracker-key">Key</Label>
              <Input
                id="tracker-key"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="jira:IPG or github:OWNER/REPO"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tracker-vendor">Vendor</Label>
              <SearchableSelect
                id="tracker-vendor"
                value={vendor}
                options={["jira", "github"]}
                onChange={setVendor}
                placeholder="auto-detect from key"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tracker-url">Remote URL</Label>
              <Input
                id="tracker-url"
                value={remoteUrl}
                onChange={(e) => setRemoteUrl(e.target.value)}
                placeholder="https://…"
                autoComplete="off"
                spellCheck={false}
                required
              />
            </div>
            <Button onClick={() => void handleAdd()} disabled={!key.trim() || !remoteUrl.trim() || adding}>
              <Plus aria-hidden />
              {adding ? "Adding…" : "Add tracker"}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
