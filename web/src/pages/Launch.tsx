import { useEffect, useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import { RefreshCw, Rocket, ShieldAlert } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { SearchableSelect } from "@/components/SearchableSelect"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useConfirm } from "@/lib/confirm"
import { errorText } from "@/components/StatusFeedback"
import { queryKeys, useCreateRun, useLinks, useRepos } from "@/lib/queries"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const AUTO_REPO = "__auto__"
const AUTO_LABEL = "Registry default (auto)"
const DEFAULT_HARNESS = "__default__"
const HARNESS_DEFAULT_LABEL = "Configured default"
/** Issue #26: Launch never defaults to the serve CWD. The backend
 * `GET /api/default-repo?ref=` returns the linked-worktree repo for the
 * typed ref, else the single-linked repo — else no default. */

type Mode = "start" | "review" | "sync"

const MODES: readonly Mode[] = ["start", "review", "sync"]

const COPY: Record<
  Mode,
  {
    label: string
    title: string
    description: string
    refHint: string
    refPlaceholder: string
    /** Confirm-dialog description, kept per mode so label edits can never
     * silently change the confirm text (replaces title string matching). */
    confirmDesc: string
  }
> = {
  start: {
    label: "Start",
    title: "Start from an issue",
    description: "Issue key, OWNER/REPO#22, or a full issue URL.",
    refHint: "Issue key, OWNER/REPO#22, or a full issue URL.",
    refPlaceholder: "IPG-932, OWNER/REPO#22, or https://…/issues/22",
    confirmDesc: "Creates a worktree from the issue and launches the coding agent.",
  },
  review: {
    label: "Review",
    title: "Review a PR/MR",
    description: "PR/MR URL, OWNER/REPO#33, or a worktree ref (key/branch/path).",
    refHint: "PR/MR URL, OWNER/REPO#33, or a worktree ref (key/branch/path).",
    refPlaceholder: "https://…/pull/33, OWNER/REPO#33, or jira:IPG-929",
    confirmDesc: "Creates a worktree from the PR/MR and launches a review agent.",
  },
  sync: {
    label: "Sync",
    title: "Sync a worktree",
    description: "Worktree key, issue/PR ref, or branch — bring its branch up to date with the base.",
    refHint: "Worktree key, issue/PR ref, or branch.",
    refPlaceholder: "jira:IPG-1, github:OWNER/REPO#33, or feat/IPG-929--x",
    confirmDesc: "Brings the worktree's branch up to date with its base branch (remote rebase by default; --merge merges locally).",
  },
}

interface LaunchForm {
  ref: string
  repo: string
  depth: string
  base: string
  harness: string
  noRuntime: boolean
  fixComments: boolean
  merge: boolean
  dryRun: boolean
  json: boolean
}
const INITIAL: LaunchForm = {
  ref: "",
  repo: AUTO_REPO,
  depth: "7",
  base: "",
  harness: DEFAULT_HARNESS,
  // Issue #24: Start/Review launches should default to printing the
  // runtime command for manual execution instead of auto-running.
  noRuntime: true,
  fixComments: false,
  merge: false,
  dryRun: false,
  json: false,
}

export function Launch() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const qc = useQueryClient()
  const { data: repos } = useRepos()
  const { data: links } = useLinks()
  const modeParam = params.get("mode")
  const refParam = params.get("ref") ?? ""
  const modeKnown = modeParam === null || MODES.includes(modeParam as Mode)

  const [mode, setMode] = useState<Mode>(
    modeKnown && modeParam !== null ? (modeParam as Mode) : "start",
  )
  const [form, setForm] = useState<LaunchForm>(() => ({
    ...INITIAL,
    ref: modeKnown ? refParam : "",
    fixComments: modeParam === "review" && params.get("fixComments") === "1",
  }))
  const [submitting, setSubmitting] = useState(false)
  const [refreshingIssues, setRefreshingIssues] = useState(false)
  const [issuesNonce, setIssuesNonce] = useState(0)
  const [prefillChecked, setPrefillChecked] = useState(false)
  const [defaultRepo, setDefaultRepo] = useState("")
  const [defaultRepoReady, setDefaultRepoReady] = useState(false)

  async function handleRefreshIssues() {
    setRefreshingIssues(true)
    try {
      const fresh = await qc.fetchQuery({
        queryKey: [...queryKeys.issues, true],
        queryFn: () => api.issues({ force: true }),
        staleTime: 0,
      })
      qc.setQueryData(queryKeys.issues, fresh)
      // Remount the Issue ref dropdown so its first-open fetch reloads
      // the now-fresh server cache immediately.
      setIssuesNonce((n) => n + 1)
      toast.success("Issues re-fetched live")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshingIssues(false)
    }
  }
  // Unknown prefill → blank form + warning, never a crash: an unrecognized
  // mode, or a sync key matching no linked worktree (stale Dashboard link).
  useEffect(() => {
    if (prefillChecked) return
    if (modeParam !== null && !MODES.includes(modeParam as Mode)) {
      setPrefillChecked(true)
      setForm((f) => ({ ...f, ref: "" }))
      toast.warning(`Unknown launch mode "${modeParam}" — form left blank`)
      return
    }
    if (modeParam === "sync" && refParam && links) {
      setPrefillChecked(true)
      if (!(refParam in links.worktrees)) {
        setForm((f) => (f.ref === refParam ? { ...f, ref: "" } : f))
        toast.warning(`No linked worktree for "${refParam}" — form left blank`)
      }
    }
  }, [prefillChecked, modeParam, refParam, links])
  const refValue = form.ref.trim()
  // Issue #26 default-repo prefill: linked-worktree repo wins, else the
  // single linked repo. For review (and start) the selected worktree's
  // repo auto-fills here via /api/default-repo — the backend resolves
  // worktree keys/branches/paths first. Sync takes no --repo (the linked
  // worktree already pins it), so the lookup is skipped. Debounced on
  // the typed ref; blanks mean "pick".
  useEffect(() => {
    if (mode === "sync") return
    const ref = refValue
    if (!ref) {
      setDefaultRepo("")
      setDefaultRepoReady(true)
      return
    }
    setDefaultRepoReady(false)
    let live = true
    const t = setTimeout(() => {
      api
        .defaultRepo(ref)
        .then((r) => {
          if (!live) return
          setDefaultRepo(r.repo ?? "")
          setDefaultRepoReady(true)
        })
        .catch(() => {
          if (!live) return
          setDefaultRepo("")
          setDefaultRepoReady(true)
        })
    }, 250)
    return () => {
      live = false
      clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refValue, mode])

  function update<K extends keyof LaunchForm>(key: K, value: LaunchForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function switchMode(next: Mode) {
    setMode(next)
    setForm((f) => ({ ...f, base: "" }))
  }

  const autoRepo = defaultRepoReady && defaultRepo ? defaultRepo : ""
  const repoValue = form.repo === AUTO_REPO ? (autoRepo || undefined) : form.repo
  const copy = COPY[mode]
  const repoHint =
    mode === "sync"
      ? ""
      : !refValue
        ? "Type a ref — the default repo appears once the ref matches a linked worktree or a single linked repo."
        : !defaultRepoReady
          ? "Looking up the default repo for this ref…"
          : autoRepo
            ? `Default for this ref: ${autoRepo}. Pick another repo to override, or leave auto.`
            : "No default repo for this ref — pick a repo (the server CWD is never used)."
  const repoOptions = useMemo(
    () => [AUTO_LABEL, ...(repos ?? []).map((r) => r.name)],
    [repos],
  )
  const repoDisplay = form.repo === AUTO_REPO ? AUTO_LABEL : form.repo
  const harnessValue = form.harness === DEFAULT_HARNESS ? undefined : form.harness
  const harnessOptions = useMemo(() => [HARNESS_DEFAULT_LABEL, "omp"], [])
  const harnessDisplay = form.harness === DEFAULT_HARNESS ? HARNESS_DEFAULT_LABEL : form.harness

  function buildArgs(): string[] {
    if (mode === "sync") {
      return [
        refValue,
        ...(form.merge ? ["--merge"] : []),
        ...(form.dryRun ? ["--dry-run"] : []),
        ...(form.json ? ["--json"] : []),
      ]
    }
    const args = [refValue, "--no-tty"]
    if (mode === "review" && form.fixComments) args.push("--fix-comments")
    if (repoValue) args.push("--repo", repoValue)
    if (form.depth.trim()) args.push("--depth", form.depth.trim())
    if (mode === "start" && form.base.trim()) args.push("--base", form.base.trim())
    if (harnessValue) args.push("--harness", harnessValue)
    if (form.noRuntime) args.push("--no-runtime")
    if (form.dryRun) args.push("--dry-run")
    if (form.json) args.push("--json")
    return args
  }

  const flagList = mode === "sync"
    ? [
        form.merge ? "--merge (local merge)" : "remote rebase (default)",
        form.dryRun ? "--dry-run" : null,
        form.json ? "--json" : null,
      ].filter((v): v is string => v !== null)
    : [
        "headless (--no-tty)",
        mode === "review" && form.fixComments ? "--fix-comments (fix open review comments)" : null,
        repoValue ? `--repo ${repoValue}` : "repo: registry default",
        `--depth ${form.depth.trim() || "7"}`,
        mode === "start" && form.base.trim() ? `--base ${form.base.trim()}` : "base: repo default",
        harnessValue ? `--harness ${harnessValue}` : "harness: configured default",
        form.noRuntime ? "--no-runtime" : null,
        form.dryRun ? "--dry-run" : null,
        form.json ? "--json" : null,
      ].filter((v): v is string => v !== null)

  async function handleSubmit() {
    if (!refValue) return
    if (!repoValue && defaultRepoReady && !autoRepo) {
      toast.error(
        "No default repo for this ref — pick a repo. The tracker is linked to multiple repos, or none.",
      )
      return
    }
    const ok = await confirm({
      action: mode,
      title: `Launch ${copy.label}`,
      description: copy.confirmDesc,
      warning:
        mode === "sync"
          ? "The server appends --yes: sync runs without prompts (AI-assisted conflict resolution if the rebase/merge conflicts)."
          : "The agent runs headless with auto-approve (--no-tty): it can commit, push and open MRs/PRs without further prompts. The server appends --yes.",
      confirmLabel: "Launch",
      details: [
        { label: "Ref", value: refValue, mono: true },
        ...(repoValue ? [{ label: "Repo", value: repoValue, mono: true }] : []),
        { label: "Flags", value: flagList.join(", ") },
      ],
    })
    if (!ok) return
    setSubmitting(true)
    try {
      const { run_id } = await createRun.mutateAsync({
        command: mode,
        args: buildArgs(),
        confirm: true,
      })
      toast.success(`${copy.label} launched`, {
        action: { label: "View", onClick: () => navigate(`/runs/${run_id}`) },
      })
      navigate(`/runs/${run_id}`)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Launch"
        description="Start an agent from an issue ref/URL, review a PR/MR ref/URL, or sync a linked worktree. Runs headless as a child process of workagent serve."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleRefreshIssues()}
              disabled={refreshingIssues}
            >
              <RefreshCw className={refreshingIssues ? "animate-spin" : undefined} aria-hidden />
              Refresh issues
            </Button>
            <div
              role="tablist"
              aria-label="Launch mode"
              className="inline-flex rounded-lg border p-1"
            >
              {MODES.map((m) => (
                <button
                  key={m}
                  role="tab"
                  aria-selected={mode === m}
                  onClick={() => switchMode(m)}
                  className={cn(
                    "min-h-9 rounded-md px-4 text-sm font-medium transition-colors",
                    mode === m
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {COPY[m].label}
                </button>
              ))}
            </div>
          </>
        }
      />
      <Card className="mx-auto max-w-2xl">
        <CardHeader>
          <CardTitle>{copy.title}</CardTitle>
          <CardDescription>{copy.refHint}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="grid gap-2">
            <Label htmlFor="launch-ref">
              {mode === "start" ? "Issue ref" : mode === "review" ? "PR/MR ref" : "Worktree ref"}
            </Label>
            {mode === "start" && !refParam ? (
              <>
                <SearchableSelect
                  key={issuesNonce}
                  id="launch-ref"
                  value={form.ref}
                  options={[]}
                  onChange={(v) => update("ref", v.trim())}
                  placeholder={copy.refPlaceholder}
                  allowCustom
                  mapOption={(o) => (o.split(" — ")[0] ?? o).trim()}
                  fetchOptions={async () => {
                    const res = await api.issues()
                    return {
                      options: res.issues.map((i) =>
                        i.title ? `${i.key} — ${i.title}` : i.key,
                      ),
                      warning: res.warning ?? undefined,
                    }
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  Pick a recent issue or type any ref; free text is kept.
                </p>
              </>
            ) : (
              <Input
                id="launch-ref"
                value={form.ref}
                onChange={(e) => update("ref", e.target.value)}
                placeholder={copy.refPlaceholder}
                autoComplete="off"
                spellCheck={false}
              />
            )}
          </div>

          {mode === "sync" ? (
            <div className="flex items-center gap-2">
              <Checkbox
                id="launch-merge"
                checked={form.merge}
                onCheckedChange={(v) => update("merge", v === true)}
              />
              <Label htmlFor="launch-merge" className="font-normal">
                <span className="font-mono text-[13px]">--merge</span> — merge locally instead of the remote rebase
              </Label>
            </div>
          ) : (
            <>
              <div className="grid gap-5 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="launch-repo">Repo</Label>
                  <SearchableSelect
                    value={repoDisplay}
                    options={repoOptions}
                    onChange={(v) => update("repo", v === AUTO_LABEL ? AUTO_REPO : v)}
                    placeholder="Filter repos, or type a path/URL…"
                    allowCustom
                  />
                  <p className="text-xs text-muted-foreground">
                    {repoHint || "--repo accepts a registered name, a local path, or a clone URL."}
                  </p>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="launch-depth">Clone depth</Label>
                  <Input
                    id="launch-depth"
                    type="number"
                    min={1}
                    value={form.depth}
                    onChange={(e) => update("depth", e.target.value)}
                    placeholder="7"
                  />
                </div>
              </div>

              {mode === "start" ? (
                <div className="grid gap-5 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="launch-base">Base branch</Label>
                    <Input
                      id="launch-base"
                      value={form.base}
                      onChange={(e) => update("base", e.target.value)}
                      placeholder="repo default"
                      spellCheck={false}
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="launch-harness">Harness</Label>
                    <SearchableSelect
                      value={harnessDisplay}
                      options={harnessOptions}
                      onChange={(v) => update("harness", v === HARNESS_DEFAULT_LABEL ? DEFAULT_HARNESS : v)}
                      placeholder="Filter harnesses…"
                    />
                  </div>
                </div>
              ) : null}
            </>
          )}

          <div className="flex flex-wrap gap-x-6 gap-y-3">
            {mode !== "sync" ? (
              <div className="flex items-center gap-2">
                <Checkbox
                  id="launch-no-runtime"
                  checked={form.noRuntime}
                  onCheckedChange={(v) => update("noRuntime", v === true)}
                />
                <Label htmlFor="launch-no-runtime" className="font-normal">
                  <span className="font-mono text-[13px]">--no-runtime</span> — skip the agent: print the command and hand over the worktree
                </Label>
              </div>
            ) : null}
            {mode === "review" ? (
              <div className="flex items-center gap-2">
                <Checkbox
                  id="launch-fix-comments"
                  checked={form.fixComments}
                  onCheckedChange={(v) => update("fixComments", v === true)}
                />
                <Label htmlFor="launch-fix-comments" className="font-normal">
                  <span className="font-mono text-[13px]">--fix-comments</span> — fix open review comments: validate, apply, resolve/close, commit and push
                </Label>
              </div>
            ) : null}
            <div className="flex items-center gap-2">
              <Checkbox
                id="launch-dry"
                checked={form.dryRun}
                onCheckedChange={(v) => update("dryRun", v === true)}
              />
              <Label htmlFor="launch-dry" className="font-normal">
                <span className="font-mono text-[13px]">--dry-run</span> — print the plan without acting
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="launch-json"
                checked={form.json}
                onCheckedChange={(v) => update("json", v === true)}
              />
              <Label htmlFor="launch-json" className="font-normal">
                <span className="font-mono text-[13px]">--json</span> — JSON output in the run log
              </Label>
            </div>
          </div>

          <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm text-amber-800 dark:text-amber-200">
            <p className="flex items-start gap-2">
              <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
              <span>
                {mode === "sync" ? (
                  <>
                    The web server appends <span className="font-mono text-[13px]">--yes</span>, so sync runs
                    without prompts (AI-assisted conflict resolution if the rebase/merge conflicts).
                  </>
                ) : (
                  <>
                    Headless auto-approve: the web server always runs{" "}
                    <span className="font-mono text-[13px]">--no-tty</span> (
                    <span className="font-mono text-[13px]">omp -p --auto-approve</span>
                    ), so the agent can commit, push and open MRs/PRs on its own.
                  </>
                )}
              </span>
            </p>
          </div>

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={
                !refValue ||
                submitting ||
                (mode !== "sync" && !repoValue && defaultRepoReady && !autoRepo)
              }
              title={
                mode !== "sync" && refValue && !repoValue && defaultRepoReady && !autoRepo
                  ? "No default repo for this ref — pick a repo (multiple or no linked repos)"
                  : undefined
              }
            >
              <Rocket aria-hidden />
              {submitting ? "Launching…" : `Launch ${copy.label}`}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
