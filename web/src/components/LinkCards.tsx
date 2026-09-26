import { useMemo, useState } from "react"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Link2, Plus, Unlink, UserPlus, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { SearchableSelect } from "@/components/SearchableSelect"
import { useConfirm } from "@/lib/confirm"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { useCreateRun, useLinks, useRepos } from "@/lib/queries"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { ActionDialog, useLinkSubmit } from "@/components/LinkDialogs"

export function TrackerMappingsCard() {
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

function deriveTrackerId(raw: string): string | null {
  const v = raw.trim()
  if (!v) return null
  const jira = /^([A-Z][A-Z0-9_]*)(-\d+)?$/.exec(v)
  if (jira) return `jira:${jira[1]}`
  const ghPath = /^github\.com\/([^/]+\/[^/]+?)(?:[#/].*)?$/.exec(v)
    ?? /^([^/]+\/[^/]+?)(#\d+)?$/.exec(v)
  if (v.includes("github.com") && ghPath) return `github:${ghPath[1]}`
  if (!v.includes("://") && ghPath && !v.includes(" ")) return `github:${ghPath[1]}`
  try {
    const u = new URL(v)
    const path = u.pathname.replace(/^\/+|\/+$/g, "").replace(/\/-(\/|$)/g, "/")
    if (!path) return null
    if (u.hostname === "github.com") return `github:${path.split("/").slice(0, 2).join("/")}`
    return `gitlab:${u.hostname.toLowerCase()}/${path}`
  } catch {
    return null
  }
}

export function LinkSetDialog({ onClose }: { onClose: () => void }) {
  const { data: repos } = useRepos()
  const { data: links } = useLinks()
  const { submitting, run: runLink } = useLinkSubmit()
  const [tracker, setTracker] = useState("")
  const [repo, setRepo] = useState("")
  const [json, setJson] = useState(false)

  const repoOptions = useMemo(() => (repos ?? []).map((r) => r.name), [repos])
  const trackerOptions = useMemo(() => Object.keys(links?.trackers ?? {}).sort(), [links])

  function handleTrackerChange(v: string) {
    // Only derive for URL-looking input; canonical ids from the dropdown
    // (github:o/r, gitlab:host/g, jira:PREFIX) pass through untouched.
    const derived = v.includes("://") ? deriveTrackerId(v) : null
    setTracker(derived ?? v)
  }

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
        <Label>Tracker</Label>
        <SearchableSelect
          value={tracker}
          options={trackerOptions}
          onChange={handleTrackerChange}
          placeholder="jira:IPG, github:OWNER/REPO, or paste an issue URL…"
          allowCustom
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

export function LinkRemoveDialog({ onClose }: { onClose: () => void }) {
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
      description="link remove REF [--repo REPO] — tracker id, or a worktree key."
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

export function RegisterDialog({ onClose }: { onClose: () => void }) {
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
      description="register PATH [--key] [--issue] [--repo] [--force] — adds an existing git worktree to the links registry."
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

