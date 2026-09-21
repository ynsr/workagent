import { useMemo, useState, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { Link2, Plus, RefreshCw, Unlink, UserPlus, X } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { SearchableSelect } from "@/components/SearchableSelect"
import { StatusTable } from "@/components/StatusTable"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
import {
  queryKeys,
  useCreateRun,
  useLinks,
  useRepos,
  useStatusAll,
} from "@/lib/queries"

/**
 * Submit a link/register run from a modal form: toasts the outcome (with a
 * View-run action) and refreshes the shared reads the command may change.
 * Resolves to true when the run was created, so the caller can close.
 * `submitting` guards the dialog buttons while the request is in flight.
 */
function useLinkSubmit() {
  const navigate = useNavigate()
  const createRun = useCreateRun()
  const qc = useQueryClient()
  const [submitting, setSubmitting] = useState(false)
  const run = async (input: {
    command: "link" | "register"
    args: string[]
    confirm?: boolean
    force?: boolean
    label: string
  }): Promise<boolean> => {
    setSubmitting(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: input.command,
        args: input.args,
        confirm: input.confirm,
        force: input.force,
      })
      toast.success(`${input.label} started`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      void qc.invalidateQueries({ queryKey: queryKeys.links })
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
      return true
    } catch (err) {
      toast.error(errorText(err))
      return false
    } finally {
      setSubmitting(false)
    }
  }
  return { submitting, run }
}

/** Modal shell for the link forms (same AlertDialog pattern as useConfirm). */
function ActionDialog({
  title,
  description,
  onClose,
  footer,
  children,
  busy = false,
}: {
  title: string
  description: string
  onClose: () => void
  footer: ReactNode
  children: ReactNode
  /** While true, Esc/overlay/Cancel cannot close the dialog mid-submit. */
  busy?: boolean
}) {
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose()
      }}
    >
      <AlertDialogContent className="max-w-lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="grid gap-4">{children}</div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
          {footer}
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
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
            description="Link a tracker to a repo with the Link tracker action."
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

function LinkSetDialog({ onClose }: { onClose: () => void }) {
  const { data: repos } = useRepos()
  const { submitting, run: runLink } = useLinkSubmit()
  const [tracker, setTracker] = useState("")
  const [repo, setRepo] = useState("")
  const [json, setJson] = useState(false)

  const repoOptions = useMemo(() => (repos ?? []).map((r) => r.name), [repos])

  async function handleSubmit() {
    const ok = await runLink({
      command: "link",
      args: ["set", tracker.trim(), repo.trim(), ...(json ? ["--json"] : [])],
      label: "Link set",
    })
    if (ok) onClose()
  }

  return (
    <ActionDialog
      title="Link a tracker to a repo"
      description="link set TRACKER REPO — persists the relation."
      onClose={onClose}
      busy={submitting}
      footer={
        <Button
          disabled={!tracker.trim() || !repo.trim() || submitting}
          onClick={() => void handleSubmit()}
        >
          <Plus aria-hidden /> {submitting ? "Linking…" : "Link"}
        </Button>
      }
    >
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
        <Label>Repo</Label>
        <SearchableSelect
          value={repo}
          options={repoOptions}
          onChange={setRepo}
          placeholder="Filter registered repos, or type a path…"
          allowCustom
        />
        {repo ? (
          <p className="truncate text-xs text-muted-foreground">
            {(repos ?? []).find((r) => r.name === repo)?.path ?? repo}
          </p>
        ) : null}
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
    </ActionDialog>
  )
}

function LinkRemoveDialog({ onClose }: { onClose: () => void }) {
  const { data: repos } = useRepos()
  const { submitting, run: runLink } = useLinkSubmit()
  const [ref, setRef] = useState("")
  const [repo, setRepo] = useState("")
  const [json, setJson] = useState(false)

  const repoOptions = useMemo(
    () => ["(any repo)", ...(repos ?? []).map((r) => r.name)],
    [repos],
  )

  async function handleSubmit() {
    const ok = await runLink({
      command: "link",
      args: [
        "remove",
        ref.trim(),
        ...(repo ? ["--repo", repo] : []),
        ...(json ? ["--json"] : []),
      ],
      confirm: true,
      label: "Link remove",
    })
    if (ok) onClose()
  }

  return (
    <ActionDialog
      title="Remove a link"
      description="link remove REF [--repo REPO] — tracker id, or a session key."
      onClose={onClose}
      busy={submitting}
      footer={
        <Button
          variant="destructive"
          disabled={!ref.trim() || submitting}
          onClick={() => void handleSubmit()}
        >
          <Unlink aria-hidden /> {submitting ? "Removing…" : "Remove link"}
        </Button>
      }
    >
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
        <Label>Repo (optional)</Label>
        <SearchableSelect
          value={repo}
          options={repoOptions}
          onChange={(v) => setRepo(v === "(any repo)" ? "" : v)}
          placeholder="Filter repos — empty removes the whole mapping"
        />
        <p className="text-xs text-muted-foreground">
          {repo ? (
            <>
              Only removing <span className="font-mono">{repo}</span> from the mapping.
            </>
          ) : (
            "No repo filter — removes the whole mapping."
          )}
        </p>
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
    </ActionDialog>
  )
}

function RegisterDialog({ onClose }: { onClose: () => void }) {
  const { data: repos } = useRepos()
  const { submitting, run: runLink } = useLinkSubmit()
  const [path, setPath] = useState("")
  const [key, setKey] = useState("")
  const [issue, setIssue] = useState("")
  const [repo, setRepo] = useState("")
  const [force, setForce] = useState(false)
  const [json, setJson] = useState(false)

  const repoOptions = useMemo(
    () => ["(auto)", ...(repos ?? []).map((r) => r.name)],
    [repos],
  )

  async function handleSubmit() {
    const ok = await runLink({
      command: "register",
      args: [
        "register",
        path.trim(),
        ...(key.trim() ? ["--key", key.trim()] : []),
        ...(issue.trim() ? ["--issue", issue.trim()] : []),
        ...(repo ? ["--repo", repo] : []),
        ...(force ? ["--force"] : []),
        ...(json ? ["--json"] : []),
      ],
      confirm: true,
      force: force || undefined,
      label: "Register",
    })
    if (ok) onClose()
  }

  return (
    <ActionDialog
      title="Register an existing worktree"
      description="register PATH [--key] [--issue] [--repo] [--force] — links an existing git worktree as a session."
      onClose={onClose}
      busy={submitting}
      footer={
        <Button
          disabled={!path.trim() || submitting}
          onClick={() => void handleSubmit()}
        >
          <UserPlus aria-hidden /> {submitting ? "Registering…" : "Register worktree"}
        </Button>
      }
    >
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
        <Label>Main checkout (optional)</Label>
        <SearchableSelect
          value={repo}
          options={repoOptions}
          onChange={(v) => setRepo(v === "(auto)" ? "" : v)}
          placeholder="Filter repos — empty derives from git"
        />
        <p className="text-xs text-muted-foreground">
          {repo ? (
            <>
              Using registered repo <span className="font-mono">{repo}</span>.
            </>
          ) : (
            "Derived from git metadata when unset."
          )}
        </p>
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
    </ActionDialog>
  )
}

export function Links() {
  const navigate = useNavigate()
  const { data: sessions, isPending, isError, error, refetch } = useStatusAll()
  const [showWorktree, setShowWorktree] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [activeForm, setActiveForm] = useState<null | "set" | "remove" | "register">(
    null,
  )

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
            <Button size="sm" onClick={() => setActiveForm("set")}>
              <Plus aria-hidden /> Link tracker
            </Button>
            <Button variant="outline" size="sm" onClick={() => setActiveForm("remove")}>
              <Unlink aria-hidden /> Remove link
            </Button>
            <Button variant="outline" size="sm" onClick={() => setActiveForm("register")}>
              <UserPlus aria-hidden /> Register
            </Button>
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

      <TrackerMappingsCard />

      {activeForm === "set" ? <LinkSetDialog onClose={() => setActiveForm(null)} /> : null}
      {activeForm === "remove" ? (
        <LinkRemoveDialog onClose={() => setActiveForm(null)} />
      ) : null}
      {activeForm === "register" ? (
        <RegisterDialog onClose={() => setActiveForm(null)} />
      ) : null}

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
