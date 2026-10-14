import * as React from "react"
import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Copy, Download, Pencil, Plus, Tags, Trash2 } from "lucide-react"
import { CopyCell } from "@/components/CopyCell"
import { PageHeader } from "@/components/PageHeader"
import {
  EmptyState,
  ErrorState,
  RefreshOverlay,
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
import { CheckRow } from "@/components/FieldHelp"
import { Label } from "@/components/ui/label"
import { SearchableSelect } from "@/components/SearchableSelect"
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
import { useCreateRun, useTrackerDomains, useTrackers, queryKeys } from "@/lib/queries"
import { api } from "@/lib/api"
import type { TrackerRow } from "@/lib/api"

function TrackerDomainsCard() {
  const qc = useQueryClient()
  const { data } = useTrackerDomains()
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const saved = (data?.disabled_domains ?? []).join("\n")
  const value = draft ?? saved

  async function handleSave() {
    const domains = value.split(/[\s,]+/).map((d: string) => d.trim()).filter(Boolean)
    setSaving(true)
    try {
      const res = await api.setTrackerDomains(domains)
      qc.setQueryData(queryKeys.trackerDomains, res)
      setDraft(null)
      toast.success(res.disabled_domains.length ? "Disabled domains saved" : "All tracker domains enabled")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle>Disabled domains</CardTitle>
        <CardDescription>
          One domain per line — trackers on these hosts are skipped by
          fetching (matches subdomains too). Empty = all enabled.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <textarea
          value={value}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setDraft(e.target.value)}
          placeholder={"example.com\njira.internal.corp"}
          rows={4}
          className="flex min-h-24 w-full rounded-md border border-input bg-transparent px-3 py-2 font-mono text-[13px] shadow-xs outline-none placeholder:text-muted-foreground/60 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
          aria-label="Disabled tracker domains"
        />
        <Button
          variant="outline"
          onClick={() => void handleSave()}
          disabled={saving || value === saved}
        >
          {saving ? "Saving…" : "Save domains"}
        </Button>
      </CardContent>
    </Card>
  )
}

export function Trackers() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data, isPending, isFetching, isError, error, refetch } = useTrackers()
  const trackers = data?.trackers ?? []

  const [key, setKey] = useState("")
  const [vendor, setVendor] = useState("")
  const [remoteUrl, setRemoteUrl] = useState("")
  const [fetchEnabled, setFetchEnabled] = useState(true)
  const [enabled, setEnabled] = useState(true)
  const [adding, setAdding] = useState(false)
  const existingKey = trackers.some((t) => t.key === key.trim()) ? key.trim() : null

  async function handleAdd() {
    if (!key.trim() || !remoteUrl.trim()) return
    setAdding(true)
    try {
      const isUpdate = trackers.some((t) => t.key === key.trim())
      const { run_id } = await createRun.mutateAsync({
        command: "tracker",
        args: isUpdate
          ? [
              "update",
              key.trim(),
              ...(vendor.trim() ? ["--vendor", vendor.trim()] : []),
              ...(remoteUrl.trim() ? ["--remote-url", remoteUrl.trim()] : []),
              ...(fetchEnabled ? ["--fetch"] : ["--no-fetch"]),
              ...(enabled ? ["--enable"] : ["--disable"]),
            ]
          : [
              "add",
              key.trim(),
              ...(vendor.trim() ? ["--vendor", vendor.trim()] : []),
              "--remote-url",
              remoteUrl.trim(),
              ...(fetchEnabled ? [] : ["--no-fetch"]),
              ...(enabled ? [] : ["--disable"]),
            ],
      })
      toast.success(`Tracker ${isUpdate ? "update" : "add"} started`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      setKey("")
      setVendor("")
      setRemoteUrl("")
      setFetchEnabled(true)
      setEnabled(true)
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

  async function handleToggleEnabled(row: TrackerRow) {
    const enable = row.is_enabled === 0 || row.is_enabled === false
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "tracker",
        args: ["update", row.key, ...(enable ? ["--enable"] : ["--disable"])],
      })
      toast.success(`Tracker ${enable ? "enable" : "disable"} started`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleEdit(row: TrackerRow) {
    setKey(row.key)
    setVendor(row.vendor ?? "")
    setRemoteUrl(row.remote_url ?? "")
    setFetchEnabled(row.fetch_enabled !== 0 && row.fetch_enabled !== false)
    setEnabled(row.is_enabled !== 0 && row.is_enabled !== false)
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
            {(isPending && !data) ? (
              <TableSkeleton rows={4} />
            ) : (isError && !data) ? (
              <ErrorState error={error} onRetry={() => void refetch()} />
            ) : trackers.length === 0 ? (
              <EmptyState
                icon={<Tags className="size-10" aria-hidden />}
                title="No trackers"
                description="Add one with the form — key, vendor, and web URL (vendor/URL derive from the key when blank)."
              />
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
                {/* Table ≥sm */}
                <div className="hidden sm:block">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Key</TableHead>
                        <TableHead>Vendor</TableHead>
                        <TableHead>Remote URL</TableHead>
                        <TableHead>Repos</TableHead>
                        <TableHead>Enabled</TableHead>
                        <TableHead className="w-24" aria-label="Actions" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {trackers.map((t) => (
                        <TableRow key={t.key}>
                          <TableCell className="max-w-48 font-mono text-[13px]">
                            <CopyCell text={t.key} />
                          </TableCell>
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
                            <input
                              type="checkbox"
                              checked={t.is_enabled !== 0 && t.is_enabled !== false}
                              onChange={() => void handleToggleEnabled(t)}
                              aria-label={`Enable tracker ${t.key}`}
                              title={t.is_enabled !== 0 && t.is_enabled !== false ? "Enabled — uncheck to disable" : "Disabled — check to enable"}
                              className="size-4 accent-primary"
                            />
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center">
                              <Button
                                variant="ghost"
                                size="icon"
                                aria-label={`Edit tracker ${t.key}`}
                                title={`Edit tracker ${t.key} (loads into the form)`}
                                onClick={() => handleEdit(t)}
                                className="size-11 text-muted-foreground hover:text-foreground"
                              >
                                <Pencil aria-hidden />
                              </Button>
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
                            </div>
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
                        {t.remote_url || "—"} · {t.repos} repos · {t.is_enabled !== 0 && t.is_enabled !== false ? "enabled" : "disabled"}
                      </p>
                      <div className="mt-3 flex items-center justify-between border-t pt-1">
                        <label className="flex min-h-11 items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={t.is_enabled !== 0 && t.is_enabled !== false}
                            onChange={() => void handleToggleEnabled(t)}
                            aria-label={`Enable tracker ${t.key}`}
                            className="size-4 accent-primary"
                          />
                          Enabled
                        </label>
                        <div className="flex">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleEdit(t)}
                            className="min-h-11"
                          >
                            <Pencil aria-hidden /> Edit
                          </Button>
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
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="order-1 grid h-fit gap-6 lg:order-2">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Add or update a tracker</CardTitle>
            <CardDescription>
              tracker add KEY --remote-url [--vendor] creates; submitting an
              existing key runs tracker update (only edited fields change).
              Use Edit on a row to preload it.
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
            <CheckRow
              id="tracker-fetch"
              checked={fetchEnabled}
              onChange={setFetchEnabled}
              label="Fetch issues for Candidates"
              flag="--no-fetch"
              description="Uncheck to skip this tracker's issue scan (github: trackers default off)"
            />
            <CheckRow
              id="tracker-enabled"
              checked={enabled}
              onChange={setEnabled}
              label="Enabled"
              flag="--disable"
              description="Uncheck to disable this tracker entirely (skipped by issue/PR fetching)"
            />
            <Button onClick={() => void handleAdd()} disabled={!key.trim() || !remoteUrl.trim() || adding}>
              <Plus aria-hidden />
              {adding ? "Saving…" : existingKey ? "Update tracker" : "Add tracker"}
            </Button>
          </CardContent>
        </Card>
        <TrackerDomainsCard />
        </div>
      </div>
    </div>
  )
}
