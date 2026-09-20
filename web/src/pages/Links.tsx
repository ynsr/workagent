import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Link2, Plus, RefreshCw, Unlink, UserPlus, X } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { StatusTable } from "@/components/StatusTable"
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
import { Switch } from "@/components/ui/switch"
import { api } from "@/lib/api"
import { useConfirm } from "@/lib/confirm"
import { copyToClipboard } from "@/lib/format"
import { useCreateRun, useLinks, useRepos, useStatusAll } from "@/lib/queries"

function LinkRunButtons({ command, args }: { command: "link" | "register"; args: string[] }) {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  return (
    <Button
      onClick={() => {
        createRun
          .mutateAsync({ command, args })
          .then(({ run_id }) => {
            toast.success("Run started", {
              action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
            })
          })
          .catch((err: unknown) => toast.error(errorText(err)))
      }}
    >
      {command === "link" ? <Plus aria-hidden /> : <UserPlus aria-hidden />}
      Run
    </Button>
  )
}

function TrackerMappingsCard() {
  const { data: links, isPending, isError, error, refetch } = useLinks()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const navigate = useNavigate()
  const [removeJson, setRemoveJson] = useState(false)

  async function handleRemoveRepo(tracker: string, repo: string) {
    const ok = await confirm({
      action: "link remove",
      title: "Remove tracker mapping",
      description: "Drops the tracker ↔ repo relation (or just this repo from the mapping).",
      destructive: true,
      confirmLabel: "Remove",
      details: [
        { label: "Tracker", value: tracker, mono: true },
        { label: "Repo", value: repo, mono: true },
      ],
      extras: (
        <div className="flex items-center gap-2">
          <Checkbox
            id="mapping-remove-json"
            checked={removeJson}
            onCheckedChange={(v) => setRemoveJson(v === true)}
          />
          <Label htmlFor="mapping-remove-json" className="font-normal">
            <span className="font-mono text-[13px]">--json</span> output
          </Label>
        </div>
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "link",
        args: ["remove", tracker, "--repo", repo, ...(removeJson ? ["--json"] : [])],
        confirm: true,
      })
      toast.success("Link remove started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const trackers = links?.trackers ?? {}
  const entries = Object.entries(trackers).sort(([a], [b]) => a.localeCompare(b))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tracker mappings</CardTitle>
        <CardDescription>GET /api/links → trackers (link list)</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <TableSkeleton rows={3} />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : entries.length === 0 ? (
          <EmptyState
            icon={<Link2 className="size-8" aria-hidden />}
            title="No tracker mappings"
            description="Link a tracker to a repo with the form below."
          />
        ) : (
          <div className="space-y-4">
            {entries.map(([tracker, mapping]) => (
              <div key={tracker}>
                <p className="font-mono text-[13px] font-semibold">{tracker}</p>
                <ul className="mt-1.5 space-y-1">
                  {mapping.repos.map((repo) => (
                    <li
                      key={repo}
                      className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                    >
                      <span className="min-w-0 truncate font-mono text-[13px]" title={repo}>
                        {repo}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Remove ${repo} from ${tracker}`}
                        title={`Remove ${repo} from ${tracker}`}
                        onClick={() => void handleRemoveRepo(tracker, repo)}
                        className="size-9 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                      >
                        <X aria-hidden />
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function LinkSetCard() {
  const { data: repos } = useRepos()
  const [tracker, setTracker] = useState("")
  const [repo, setRepo] = useState("")
  const [json, setJson] = useState(false)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Link a tracker to a repo</CardTitle>
        <CardDescription>link set TRACKER REPO — persists the relation.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="linkset-tracker">Tracker</Label>
          <Input
            id="linkset-tracker"
            value={tracker}
            onChange={(e) => setTracker(e.target.value)}
            placeholder="jira:IPG or github:OWNER/REPO"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="linkset-repo">Repo</Label>
          <Input
            id="linkset-repo"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="/home/you/projects/projectx or a registered name"
            autoComplete="off"
            spellCheck={false}
            list="linkset-repo-options"
          />
          <datalist id="linkset-repo-options">
            {(repos ?? []).map((r) => (
              <option key={r.name} value={r.name}>
                {r.path}
              </option>
            ))}
          </datalist>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="linkset-json"
            checked={json}
            onCheckedChange={(v) => setJson(v === true)}
          />
          <Label htmlFor="linkset-json" className="font-normal">
            <span className="font-mono text-[13px]">--json</span> output
          </Label>
        </div>
        <LinkRunButtons
          command="link"
          args={["set", tracker.trim(), repo.trim(), ...(json ? ["--json"] : [])]}
        />
      </CardContent>
    </Card>
  )
}

function LinkRemoveCard() {
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const navigate = useNavigate()
  const [ref, setRef] = useState("")
  const [repo, setRepo] = useState("")
  const [json, setJson] = useState(false)

  const repoFilter = repo.trim()

  async function handleRemove() {
    const args = [
      "remove",
      ref.trim(),
      ...(repoFilter ? ["--repo", repoFilter] : []),
      ...(json ? ["--json"] : []),
    ]
    const ok = await confirm({
      action: "link remove",
      title: "Remove link",
      description:
        "Drops the tracker mapping (or just the --repo entry), or removes a session link entirely.",
      destructive: true,
      confirmLabel: "Remove link",
      details: [
        { label: "Ref", value: ref.trim(), mono: true },
        ...(repoFilter ? [{ label: "Repo", value: repoFilter, mono: true }] : []),
      ],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "link",
        args,
        confirm: true,
      })
      toast.success("Link remove started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      setRef("")
      setRepo("")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Remove a link</CardTitle>
        <CardDescription>
          link remove REF [--repo REPO] — tracker id, or a session key.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="linkremove-ref">Ref</Label>
          <Input
            id="linkremove-ref"
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            placeholder="jira:IPG or jira:IPG-932"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="linkremove-repo">Repo (optional)</Label>
          <Input
            id="linkremove-repo"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="only remove this repo from the mapping"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="linkremove-json"
            checked={json}
            onCheckedChange={(v) => setJson(v === true)}
          />
          <Label htmlFor="linkremove-json" className="font-normal">
            <span className="font-mono text-[13px]">--json</span> output
          </Label>
        </div>
        <Button
          variant="destructive"
          disabled={!ref.trim()}
          onClick={() => void handleRemove()}
        >
          <Unlink aria-hidden /> Remove link
        </Button>
      </CardContent>
    </Card>
  )
}

function RegisterCard() {
  const [path, setPath] = useState("")
  const [key, setKey] = useState("")
  const [issue, setIssue] = useState("")
  const [repo, setRepo] = useState("")
  const [force, setForce] = useState(false)
  const [json, setJson] = useState(false)
  const createRun = useCreateRun()
  const navigate = useNavigate()

  async function handleRegister() {
    const args = [
      "register",
      path.trim(),
      ...(key.trim() ? ["--key", key.trim()] : []),
      ...(issue.trim() ? ["--issue", issue.trim()] : []),
      ...(repo.trim() ? ["--repo", repo.trim()] : []),
      ...(force ? ["--force"] : []),
      ...(json ? ["--json"] : []),
    ]
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "register",
        args,
        confirm: true,
        force: force || undefined,
      })
      toast.success("Register started", {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Register an existing worktree</CardTitle>
        <CardDescription>
          register PATH [--key] [--issue] [--repo] [--force] — links an existing
          git worktree as a session.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label htmlFor="reg-path">Worktree path</Label>
          <Input
            id="reg-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="/home/you/dev/worktrees/projectx/feat/IPG-999--x"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label htmlFor="reg-key">Key (optional)</Label>
            <Input
              id="reg-key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="jira:IPG-999"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="reg-issue">Issue ref (optional)</Label>
            <Input
              id="reg-issue"
              value={issue}
              onChange={(e) => setIssue(e.target.value)}
              placeholder="IPG-999"
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="reg-repo">Main checkout (optional)</Label>
          <Input
            id="reg-repo"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
            placeholder="derived from git metadata"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="flex flex-wrap gap-x-6 gap-y-3">
          <div className="flex items-center gap-2">
            <Checkbox
              id="reg-force"
              checked={force}
              onCheckedChange={(v) => setForce(v === true)}
            />
            <Label htmlFor="reg-force" className="font-normal">
              <span className="font-mono text-[13px]">--force</span> — overwrite an existing link
            </Label>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id="reg-json"
              checked={json}
              onCheckedChange={(v) => setJson(v === true)}
            />
            <Label htmlFor="reg-json" className="font-normal">
              <span className="font-mono text-[13px]">--json</span> output
            </Label>
          </div>
        </div>
        <Button disabled={!path.trim()} onClick={() => void handleRegister()}>
          <UserPlus aria-hidden /> Register worktree
        </Button>
      </CardContent>
    </Card>
  )
}

export function Links() {
  const navigate = useNavigate()
  const { data: sessions, isPending, isError, error, refetch } = useStatusAll()
  const [showWorktree, setShowWorktree] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  async function handleRefreshPr() {
    setRefreshing(true)
    try {
      await refetch()
      toast.success("Sessions refreshed")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshing(false)
    }
  }

  const tableActions = {
    onCopyPath: async (key: string) => {
      try {
        const p = await api.path(key)
        await copyToClipboard(p.worktree)
        toast.success(`Copied ${p.worktree}`)
      } catch (err) {
        toast.error(errorText(err))
      }
    },
    onOpenRun: (key: string) => navigate(`/runs?target=${encodeURIComponent(key)}`),
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Links"
        description="Tracker ↔ repo mappings, linked sessions, and registering existing worktrees."
        actions={
          <>
            <label className="flex min-h-9 items-center gap-2 rounded-md border px-3 text-sm">
              <Switch
                checked={showWorktree}
                onCheckedChange={setShowWorktree}
                aria-label="Show worktree paths"
              />
              Worktree paths
            </label>
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefreshPr}
              disabled={refreshing}
            >
              <RefreshCw className={refreshing ? "animate-spin" : undefined} aria-hidden />
              Refresh PR
            </Button>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <TrackerMappingsCard />
        <LinkSetCard />
        <LinkRemoveCard />
        <RegisterCard />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Linked sessions</CardTitle>
          <CardDescription>
            Same table as the Dashboard, from GET /api/status (link list shape).
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isPending ? (
            <TableSkeleton rows={4} />
          ) : isError ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : !sessions || Object.keys(sessions).length === 0 ? (
            <EmptyState
              icon={<Link2 className="size-10" aria-hidden />}
              title="No linked sessions"
              description="Sessions appear once a worktree is linked to a tracker."
            />
          ) : (
            <StatusTable
              sessions={sessions}
              actions={tableActions}
              showWorktree={showWorktree}
            />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
