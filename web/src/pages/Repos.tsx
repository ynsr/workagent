import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, FolderGit2, Plus, Trash2 } from "lucide-react"
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
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
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
import { useCreateRun, useRepos } from "@/lib/queries"
import type { Repo } from "@/lib/api"

export function Repos() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: repos, isPending, isError, error, refetch } = useRepos()

  const [name, setName] = useState("")
  const [path, setPath] = useState("")
  const [tracker, setTracker] = useState("")
  const [addJson, setAddJson] = useState(false)
  const [removeJson, setRemoveJson] = useState(false)
  const [adding, setAdding] = useState(false)

  const [trackerError, setTrackerError] = useState("")
  async function handleAdd() {
    if (!name.trim() || !path.trim()) return
    if (!tracker.trim()) {
      setTrackerError("Tracker is required (e.g. IPG or github:OWNER/REPO).")
      return
    }
    setTrackerError("")
    setAdding(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "repo",
        args: [
          "add",
          "--name",
          name.trim(),
          "--path",
          path.trim(),
          "--tracker",
          tracker.trim(),
          ...(addJson ? ["--json"] : []),
        ],
      })
      toast.success("Repo add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      setName("")
      setPath("")
      setTracker("")
      setAddJson(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(repo: Repo) {
    const ok = await confirm({
      action: "repo remove",
      title: `Remove repo ${repo.name}`,
      description: "Unregisters the repo from the harness registry.",
      destructive: true,
      confirmLabel: "Remove repo",
      details: [
        { label: "Name", value: repo.name, mono: true },
        { label: "Path", value: repo.path, mono: true },
        ...(typeof repo.tracker === "string" && repo.tracker
          ? [{ label: "Tracker", value: repo.tracker, mono: true }]
          : []),
      ],
      extras: (
        <div className="flex items-center gap-2">
          <Checkbox
            id="repo-remove-json"
            checked={removeJson}
            onCheckedChange={(v) => setRemoveJson(v === true)}
          />
          <Label htmlFor="repo-remove-json" className="font-normal">
            <span className="font-mono text-[13px]">--json</span> — JSON output in the run log
          </Label>
        </div>
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "repo",
        args: ["remove", repo.name, ...(removeJson ? ["--json"] : [])],
        confirm: true,
      })
      toast.success("Repo remove started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCopyJson() {
    if (!repos) return
    try {
      await copyToClipboard(JSON.stringify(repos, null, 2))
      toast.success("Repo list JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleDownloadCsv() {
    if (!repos) return
    downloadText(
      "harness-repos.csv",
      toCsv(
        ["name", "path", "tracker"],
        repos.map((r) => [r.name, r.path, typeof r.tracker === "string" ? r.tracker : ""]),
      ),
      "text/csv",
    )
  }

  return (
    <div>
      <PageHeader
        title="Repos"
        description="Registered repositories (repo list / repo add / repo remove)."
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
            <CardTitle>Registered repos</CardTitle>
            <CardDescription>GET /api/repos</CardDescription>
          </CardHeader>
          <CardContent>
            {isPending ? (
              <TableSkeleton rows={4} />
            ) : isError ? (
              <ErrorState error={error} onRetry={() => void refetch()} />
            ) : !repos || repos.length === 0 ? (
              <EmptyState
                icon={<FolderGit2 className="size-10" aria-hidden />}
                title="No repos registered"
                description="Add one with the form — name, local path, optional tracker mapping."
              />
            ) : (
              <>
                {/* Table ≥sm */}
                <div className="hidden sm:block">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Path</TableHead>
                        <TableHead>Tracker</TableHead>
                        <TableHead className="w-12" aria-label="Remove" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {repos.map((r) => (
                        <TableRow key={r.name}>
                          <TableCell className="font-medium">{r.name}</TableCell>
                          <TableCell className="max-w-72 truncate font-mono text-[13px]" title={r.path}>
                            {r.path}
                          </TableCell>
                          <TableCell>
                            {typeof r.tracker === "string" && r.tracker ? (
                              <Badge variant="secondary" className="font-mono">
                                {r.tracker}
                              </Badge>
                            ) : (
                              <span className="text-muted-foreground">—</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Remove repo ${r.name}`}
                              title={`Remove repo ${r.name}`}
                              onClick={() => void handleRemove(r)}
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
                  {repos.map((r) => (
                    <div key={r.name} className="rounded-xl border bg-card p-4">
                      <div className="flex items-start justify-between gap-2">
                        <p className="font-medium">{r.name}</p>
                        {typeof r.tracker === "string" && r.tracker ? (
                          <Badge variant="secondary" className="font-mono">
                            {r.tracker}
                          </Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 break-all font-mono text-[13px] text-muted-foreground">
                        {r.path}
                      </p>
                      <div className="mt-3 flex justify-end border-t pt-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => void handleRemove(r)}
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
            <CardTitle>Add a repo</CardTitle>
            <CardDescription>
              repo add --name --path [--tracker] — writes the registry config.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="repo-name">Name</Label>
              <Input
                id="repo-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="projectx"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="repo-path">Path</Label>
              <Input
                id="repo-path"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder="/home/you/projects/projectx"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="repo-tracker">Tracker project (required)</Label>
              <Input
                id="repo-tracker"
                value={tracker}
                onChange={(e) => { setTracker(e.target.value); if (trackerError) setTrackerError("") }}
                placeholder="IPG or github:OWNER/REPO"
                autoComplete="off"
                spellCheck={false}
                aria-invalid={trackerError ? true : undefined}
              />
              {trackerError ? <p className="text-xs text-destructive">{trackerError}</p> : null}
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="repo-add-json"
                checked={addJson}
                onCheckedChange={(v) => setAddJson(v === true)}
              />
              <Label htmlFor="repo-add-json" className="font-normal">
                <span className="font-mono text-[13px]">--json</span> output
              </Label>
            </div>
            <Button onClick={() => void handleAdd()} disabled={!name.trim() || !path.trim() || !tracker.trim() || adding}>
              <Plus aria-hidden />
              {adding ? "Adding…" : "Add repo"}
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
