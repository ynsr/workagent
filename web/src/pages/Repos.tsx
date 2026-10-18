import { useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Copy, Download, FolderGit2, Plus, Trash2 } from "lucide-react"
import { CopyCell } from "@/components/CopyCell"
import { PageHeader } from "@/components/PageHeader"
import { ReleaseButton } from "@/components/ReleaseButton"
import { ReleasingDot } from "@/components/ReleaseCard"
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
import { CheckRow } from "@/components/FieldHelp"
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
  const { data: repos, isPending, isFetching, isError, error, refetch } = useRepos()

  const [name, setName] = useState("")
  const [path, setPath] = useState("")
  const [tracker, setTracker] = useState("")
  const [fetchEnabled, setFetchEnabled] = useState(true)
  const [releaseBranch, setReleaseBranch] = useState("")
  const [preRelease, setPreRelease] = useState("")
  const [preReleaseTouched, setPreReleaseTouched] = useState(false)
  const [detecting, setDetecting] = useState(false)
  const [removeForce, setRemoveForce] = useState(false)
  const [adding, setAdding] = useState(false)
  const [trackerError, setTrackerError] = useState("")
  const editDefaultRef = useRef("")
  const editReleaseRef = useRef("")
  const editPreReleaseRef = useRef("")

  async function handlePathBlur() {
    if (!path.trim() || preReleaseTouched || preRelease) return
    setDetecting(true)
    try {
      const res = await fetch(`/api/repos/detect?path=${encodeURIComponent(path.trim())}`)
      if (!res.ok) return
      const data = (await res.json()) as { "pre-release"?: string | null }
      if (data["pre-release"]) setPreRelease(data["pre-release"])
    } catch {
      /* detection is best-effort; the field stays user-editable */
    } finally {
      setDetecting(false)
    }
  }

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
        args: ["add", "--name", name.trim(), "--path", path.trim(), "--tracker", tracker.trim(),
          ...(fetchEnabled ? [] : ["--no-fetch"]),
          ...(releaseBranch.trim() ? ["--release-branch", releaseBranch.trim()] : []),
          ...(preRelease.trim() ? ["--pre-release", preRelease.trim()] : [])],
      })
      toast.success("Repo add started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      setName("")
      setPath("")
      setTracker("")
      setFetchEnabled(true)
      setReleaseBranch("")
      setPreRelease("")
      setPreReleaseTouched(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setAdding(false)
    }
  }

  async function handleEditBranches(repo: Repo) {
    const ok = await confirm({
      title: `Edit branches for ${repo.name}`,
      description: "Updates the repo's default/release branches (empty release = no release support).",
      confirmLabel: "Update branches",
      details: [
        { label: "Default", value: repo.default_branch || "—", mono: true },
        { label: "Release", value: repo.release_branch || "—", mono: true },
      ],
      extras: (
        <BranchEditFields
          initialDefault={repo.default_branch || ""}
          initialRelease={repo.release_branch || ""}
          onChange={(d, r) => {
            editDefaultRef.current = d
            editReleaseRef.current = r
          }}
        />
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "repo",
        args: ["update", repo.name,
          "--default-branch", editDefaultRef.current,
          "--release-branch", editReleaseRef.current],
        confirm: true,
      })
      toast.success("Repo update started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }
  async function handleEditPreRelease(repo: Repo) {
    editPreReleaseRef.current = repo.commands?.["pre-release"] || ""
    const ok = await confirm({
      title: `Edit pre-release gate for ${repo.name}`,
      description: "Runs before the release MR opens; a failing gate aborts the release (empty clears).",
      confirmLabel: "Update gate",
      details: [
        { label: "Pre-release", value: repo.commands?.["pre-release"] || "—", mono: true },
      ],
      extras: (
        <div className="grid gap-2">
          <Label htmlFor="repo-edit-prerelease">Pre-release gate (empty clears)</Label>
          <Input
            id="repo-edit-prerelease"
            defaultValue={repo.commands?.["pre-release"] || ""}
            onChange={(e) => { editPreReleaseRef.current = e.target.value }}
            placeholder="npm test — empty = no gate"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "repo",
        args: ["update", repo.name, "--pre-release", editPreReleaseRef.current],
        confirm: true,
      })
      toast.success("Repo update started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleRemove(repo: Repo) {
    const ok = await confirm({
      action: "repo remove",
      title: `Remove repo ${repo.name}`,
      description: "Unregisters the repo from the workagent registry.",
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
        <CheckRow
          id="repo-remove-force"
          checked={removeForce}
          onChange={setRemoveForce}
          label="Remove linked worktrees too"
          flag="--force"
          description="Remove linked worktrees (and their sessions) too"
        />
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "repo",
        args: ["remove", repo.name],
        confirm: true,
        force: removeForce,
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
      "workagent-repos.csv",
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
            {(isPending && !repos) ? (
              <TableSkeleton rows={4} />
            ) : (isError && !repos) ? (
              <ErrorState error={error} onRetry={() => void refetch()} />
            ) : !repos || repos.length === 0 ? (
              <EmptyState
                icon={<FolderGit2 className="size-10" aria-hidden />}
                title="No repos registered"
                description="Add one with the form — name, local path, optional tracker mapping."
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
                        <TableHead>Name</TableHead>
                        <TableHead>Path</TableHead>
                        <TableHead>Tracker</TableHead>
                        <TableHead>Branches</TableHead>
                        <TableHead>Pre-release</TableHead>
                        <TableHead className="w-12" aria-label="Release" />
                        <TableHead className="w-12" aria-label="Remove" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {repos.map((r) => (
                        <TableRow key={r.name}>
                          <TableCell className="max-w-48 font-medium">
                            <span className="flex items-center gap-1.5">
                              <span className="min-w-0 flex-1"><CopyCell text={r.name} /></span>
                              <ReleasingDot repo={r.name} />
                            </span>
                          </TableCell>
                          <TableCell className="max-w-72 font-mono text-[13px]">
                            <CopyCell text={r.path} />
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
                          <TableCell className="font-mono text-[13px]">
                            <button
                              type="button"
                              onClick={() => void handleEditBranches(r)}
                              title={`Edit branches for ${r.name}`}
                              className="rounded underline decoration-dotted underline-offset-4 hover:text-foreground"
                            >
                              {r.default_branch || r.release_branch ? (
                                <span>{r.default_branch || "—"} → {r.release_branch || "—"}</span>
                              ) : (
                                <span className="text-muted-foreground">set branches</span>
                              )}
                            </button>
                          </TableCell>
                          <TableCell className="max-w-48 font-mono text-[13px]">
                            <button
                              type="button"
                              onClick={() => void handleEditPreRelease(r)}
                              title={`Edit pre-release gate for ${r.name}`}
                              className="rounded underline decoration-dotted underline-offset-4 hover:text-foreground"
                            >
                              {r.commands?.["pre-release"] ? (
                                <span>{r.commands["pre-release"]}</span>
                              ) : (
                                <span className="text-muted-foreground">set gate</span>
                              )}
                            </button>
                          </TableCell>
                          <TableCell>
                            <ReleaseButton repo={r.name} compact />
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
                        <span className="flex items-center gap-1.5">
                          <p className="font-medium">{r.name}</p>
                          <ReleasingDot repo={r.name} />
                        </span>
                        {typeof r.tracker === "string" && r.tracker ? (
                          <Badge variant="secondary" className="font-mono">
                            {r.tracker}
                          </Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 break-all font-mono text-[13px] text-muted-foreground">
                        {r.path}
                      </p>
                      <button
                        type="button"
                        onClick={() => void handleEditPreRelease(r)}
                        className="mt-1 block max-w-full truncate text-left font-mono text-[13px] text-muted-foreground underline decoration-dotted underline-offset-4"
                      >
                        {r.commands?.["pre-release"] ? `gate: ${r.commands["pre-release"]}` : "set gate"}
                      </button>
                      <div className="mt-3 flex items-center justify-between gap-2 border-t pt-1">
                        <span className="font-mono text-xs text-muted-foreground">
                          {r.default_branch || r.release_branch
                            ? `${r.default_branch || "—"} → ${r.release_branch || "—"}`
                            : "no release branch"}
                        </span>
                        <span className="flex items-center gap-1">
                          <ReleaseButton repo={r.name} compact />
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => void handleRemove(r)}
                            className="min-h-11 text-destructive hover:bg-destructive/10"
                          >
                            <Trash2 aria-hidden /> Remove
                          </Button>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
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
                onBlur={() => void handlePathBlur()}
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
            <CheckRow
              id="repo-fetch"
              checked={fetchEnabled}
              onChange={setFetchEnabled}
              label="Fetch MRs/PRs for Candidates"
              flag="--no-fetch"
              description="Uncheck to skip this repo's live host-CLI scan (GitHub repos default off)"
            />
            <div className="grid gap-2">
              <Label htmlFor="repo-release">Release branch (optional)</Label>
              <Input
                id="repo-release"
                value={releaseBranch}
                onChange={(e) => setReleaseBranch(e.target.value)}
                placeholder="master — empty = no release support"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="repo-prerelease">Pre-release gate (optional)</Label>
              <Input
                id="repo-prerelease"
                value={preRelease}
                onChange={(e) => { setPreRelease(e.target.value); setPreReleaseTouched(true) }}
                placeholder={detecting ? "detecting…" : "auto-detected on path blur — empty = no gate"}
                autoComplete="off"
                spellCheck={false}
              />
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
function BranchEditFields({
  initialDefault,
  initialRelease,
  onChange,
}: {
  initialDefault: string
  initialRelease: string
  onChange: (d: string, r: string) => void
}) {
  const [d, setD] = useState(initialDefault)
  const [r, setR] = useState(initialRelease)
  return (
    <div className="grid gap-2">
      <Label htmlFor="repo-edit-default">Default branch</Label>
      <Input
        id="repo-edit-default"
        value={d}
        onChange={(e) => { setD(e.target.value); onChange(e.target.value, r) }}
        placeholder="develop"
        autoComplete="off"
        spellCheck={false}
      />
      <Label htmlFor="repo-edit-release">Release branch (empty clears)</Label>
      <Input
        id="repo-edit-release"
        value={r}
        onChange={(e) => { setR(e.target.value); onChange(d, e.target.value) }}
        placeholder="master — empty = no release support"
        autoComplete="off"
        spellCheck={false}
      />
    </div>
  )
}
