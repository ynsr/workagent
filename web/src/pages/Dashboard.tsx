import { useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"
import {
  Copy,
  Download,
  FolderGit2,
  RefreshCw,
  Rocket,
  SearchX,
  SquareTerminal,
} from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import { StatusTable } from "@/components/StatusTable"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { api, type WorktreeMap } from "@/lib/api"
import { useConfirm } from "@/lib/confirm"
import { copyToClipboard, downloadText, toCsv } from "@/lib/format"
import { queryKeys, useCreateRun, useInfo, useRepos, useStatusAll } from "@/lib/queries"
import { useRepoTabs } from "@/lib/useRepoTabs"

const CSV_HEADERS = [
  "key",
  "issue",
  "repo",
  "branch",
  "worktree",
  "commits",
  "pr",
  "pr_url",
  "pr_state",
]

function worktreeRows(worktrees: WorktreeMap): string[][] {
  return Object.entries(worktrees).map(([key, e]) => [
    key,
    e.issue ?? "",
    e.repo ?? "",
    e.branch ?? "",
    e.worktree ?? "",
    e.commits_detail
      ? `${e.commits_detail.behind}|${e.commits_detail.ahead}`
      : e.commits ?? "",
    e.pr ?? "",
    e.pr_detail?.url ?? "",
    e.pr_detail?.state ?? "",
  ])
}

export function Dashboard() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const { data: worktrees, isPending, isError, error, refetch } = useStatusAll()
  const { data: info } = useInfo()
  const { data: repos } = useRepos()
  const repoTabs = useRepoTabs(
    Object.values(worktrees ?? {}).map((e) => e.worktree ?? ""),
    repos,
  )

  const [showWorktree, setShowWorktree] = useState(false)
  const [refreshingPr, setRefreshingPr] = useState(false)
  const [syncOpts, setSyncOpts] = useState({ merge: false, dryRun: false, json: false })
  const [cleanupOpts, setCleanupOpts] = useState({ force: false, dryRun: false, json: false })

  async function handleRefreshPr() {
    setRefreshingPr(true)
    try {
      const fresh = await qc.fetchQuery({
        queryKey: [...queryKeys.statusAll, true],
        queryFn: () => api.statusAll({ refresh: true }),
        staleTime: 0,
      })
      qc.setQueryData([...queryKeys.statusAll, false], fresh)
      toast.success("PR status re-queried")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshingPr(false)
    }
  }

  function runCreated(runId: string, label: string) {
    toast.success(`${label} started`, {
      action: { label: "View", onClick: () => navigate(`/runs/${runId}`) },
    })
    navigate(`/runs/${runId}`)
  }

  async function handleSyncAll() {
    setSyncOpts({ merge: false, dryRun: false, json: false })
    const ok = await confirm({
      action: "sync",
      title: "Sync all worktrees",
      description:
        "Runs sync for every linked worktree (implies --yes). Same strategy as a single sync: remote rebase by default, local merge with -m.",
      destructive: true,
      confirmLabel: "Sync all",
      details: [{ label: "Scope", value: "Every linked worktree" }],
      extras: (
        <div className="grid gap-2.5">
          <OptRow
            id="syncall-merge"
            checked={syncOpts.merge}
            onChange={(v) => setSyncOpts((o) => ({ ...o, merge: v }))}
            label="-m — merge locally instead of the remote rebase"
          />
          <OptRow
            id="syncall-dry"
            checked={syncOpts.dryRun}
            onChange={(v) => setSyncOpts((o) => ({ ...o, dryRun: v }))}
            label="--dry-run — show what would run"
          />
          <OptRow
            id="syncall-json"
            checked={syncOpts.json}
            onChange={(v) => setSyncOpts((o) => ({ ...o, json: v }))}
            label="--json — JSON output in the run log"
          />
        </div>
      ),
    })
    if (!ok) return
    const dry = syncOpts.dryRun
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "sync",
        args: [
          "--all",
          ...(syncOpts.merge ? ["--merge"] : []),
          ...(dry ? ["--dry-run"] : []),
          ...(syncOpts.json ? ["--json"] : []),
        ],
        confirm: dry ? undefined : true,
      })
      runCreated(run_id, "Sync all")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleReviewAll() {
    const ok = await confirm({
      action: "review",
      title: "Review all worktrees",
      description:
        "Reviews every not-reviewed linked worktree in parallel (non-TTY). Reviewed worktrees whose tip moved are reviewed again.",
      destructive: true,
      confirmLabel: "Review all",
      details: [{ label: "Scope", value: "Every linked worktree" }],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "review",
        args: ["--all"],
        confirm: true,
      })
      runCreated(run_id, "Review all")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCleanupMerged() {
    const ok = await confirm({
      action: "cleanup",
      title: "Cleanup merged worktrees",
      description:
        "Removes every linked worktree whose PR/MR is merged or closed. Cleanup closes the tracker issue, removes the worktree, deletes the branch and closes the PR. Live harnesses and invalid worktrees are skipped, never torn down.",
      destructive: true,
      confirmLabel: "Cleanup merged",
      details: [{ label: "Scope", value: "Merged/closed PRs only" }],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "cleanup",
        args: ["--merged"],
        confirm: true,
      })
      runCreated(run_id, "Cleanup merged")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleCleanup(key: string) {
    setCleanupOpts({ force: false, dryRun: false, json: false })
    const invalid = worktrees?.[key]?.wt_valid === false
    const ok = await confirm({
      action: "cleanup",
      ref: key,
      title: invalid ? `Delete invalid worktree ${key}` : `Remove worktree ${key}`,
      description: invalid
        ? "The recorded path is missing or not a live git worktree. --force skips state validation; the entry is removed either way. This cannot be undone."
        : "Closes the tracker issue, removes the worktree, deletes the branch and closes the PR. This cannot be undone.",
      destructive: true,
      confirmLabel: invalid ? "Delete worktree" : "Remove worktree",
      force: invalid || undefined,
      extras: (
        <div className="grid gap-2.5">
          <OptRow
            id="cleanup-force"
            checked={invalid || (cleanupOpts.force && !cleanupOpts.dryRun)}
            disabled={invalid || cleanupOpts.dryRun}
            onChange={(v) => setCleanupOpts((o) => ({ ...o, force: v }))}
            label="--force — skip state validation (always requires this confirmation)"
          />
          <OptRow
            id="cleanup-dry"
            checked={cleanupOpts.dryRun}
            onChange={(v) =>
              setCleanupOpts((o) => ({ ...o, dryRun: v, force: v ? false : o.force }))
            }
            label="--dry-run — print the plan without acting"
          />
          <OptRow
            id="cleanup-json"
            checked={cleanupOpts.json}
            onChange={(v) => setCleanupOpts((o) => ({ ...o, json: v }))}
            label="--json — JSON output in the run log"
          />
        </div>
      ),
    })
    if (!ok) return
    const dry = cleanupOpts.dryRun
    const force = (invalid || cleanupOpts.force) && !dry
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "cleanup",
        args: [
          key,
          ...(force ? ["--force"] : []),
          ...(dry ? ["--dry-run"] : []),
          ...(cleanupOpts.json ? ["--json"] : []),
        ],
        confirm: dry ? undefined : true,
        force: force || undefined,
      })
      runCreated(run_id, `Cleanup ${key}`)
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleOpenWorktree(key: string) {
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "open",
        args: [key],
        confirm: false,
      })
      toast.success(`Opening ${key}`)
      navigate(`/runs/${run_id}`)
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleOpenRun(key: string) {
    navigate(`/runs?target=${encodeURIComponent(key)}`)
  }

  async function handleCopyJson() {
    if (!worktrees) return
    try {
      await copyToClipboard(JSON.stringify(worktrees, null, 2))
      toast.success("Status JSON copied")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  function handleDownloadCsv() {
    if (!worktrees) return
    downloadText(
      "harness-status.csv",
      toCsv(CSV_HEADERS, worktreeRows(worktrees)),
      "text/csv",
    )
  }

  // Row Sync/Review go to Launch, which renders the flow prefilled with this
  // worktree key (exact query contract Launch reads back).
  const tableActions = {
    onSync: (key: string) =>
      navigate("/launch?mode=sync&ref=" + encodeURIComponent(key)),
    onReview: (key: string) =>
      navigate("/launch?mode=review&ref=" + encodeURIComponent(key)),
    onCleanup: handleCleanup,
    onOpenWorktree: handleOpenWorktree,
    onOpenRun: handleOpenRun,
    onOpenSessions: (key: string) =>
      navigate("/sessions?worktree=" + encodeURIComponent(key)),
  }
  const invalidCount = worktrees
    ? Object.values(worktrees).filter((e) => e.wt_valid === false).length
    : 0

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Linked issue ↔ PR ↔ worktrees. Refreshes every 15 s while the tab is visible."
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefreshPr}
              disabled={refreshingPr}
            >
              <RefreshCw className={refreshingPr ? "animate-spin" : undefined} aria-hidden />
              Refresh PR
            </Button>
            <Button variant="secondary" size="sm" onClick={handleSyncAll}>
              Sync all
            </Button>
            <Button variant="outline" size="sm" onClick={handleReviewAll}>
              Review all
            </Button>
            <Button variant="outline" size="sm" onClick={handleCleanupMerged}>
              Cleanup merged
            </Button>
            <Button variant="outline" size="sm" onClick={handleCopyJson}>
              <Copy aria-hidden /> JSON
            </Button>
            <Button variant="outline" size="sm" onClick={handleDownloadCsv}>
              <Download aria-hidden /> CSV
            </Button>
            <label className="flex min-h-9 items-center gap-2 rounded-md border px-3 text-sm">
              <Switch
                checked={showWorktree}
                onCheckedChange={setShowWorktree}
                aria-label="Show worktree paths"
              />
              Worktree paths
            </label>
          </>
        }
      />

      {isPending ? (
        <TableSkeleton rows={6} />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !worktrees || Object.keys(worktrees).length === 0 ? (
        <EmptyState
          icon={<FolderGit2 className="size-10" aria-hidden />}
          title="No linked worktrees yet"
          description="Register a repo, link a tracker, then launch start from an issue ref — worktrees appear here."
        >
          <Button asChild size="sm">
            <Link to="/launch">
              <Rocket aria-hidden /> Launch
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to="/repos">
              <FolderGit2 aria-hidden /> Repos
            </Link>
          </Button>
        </EmptyState>
      ) : (
        <>
          {invalidCount > 0 ? (
            <p
              role="alert"
              className="mb-3 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2.5 text-sm text-destructive"
            >
              <SearchX aria-hidden className="mt-0.5 size-4 shrink-0" />
              <span>
                {invalidCount === 1
                  ? "1 worktree is invalid (path missing or not a live git worktree)."
                  : `${invalidCount} worktrees are invalid (path missing or not a live git worktree).`}{" "}
                Use the Delete action to remove them.
              </span>
            </p>
          ) : null}
          <StatusTable
            worktrees={worktrees}
            actions={tableActions}
            showWorktree={showWorktree}
            networkExposed={info?.network_exposed ?? false}
            repoTabs={repos ? { ...repoTabs, repos } : undefined}
          />
          <details className="mt-3 rounded-md border px-3 py-2 text-xs text-muted-foreground">
            <summary className="cursor-pointer font-medium text-foreground">
              Remote calls &amp; cache TTLs
            </summary>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li><span className="font-mono">gh pr list</span> / <span className="font-mono">glab mr list</span> per branch — PR result cached 3h, no-PR result 30min (refresh: Refresh PR).</li>
              <li>CI pipeline lookup per PR branch — cached 10min.</li>
              <li>Tracker issue lists (jira-cli / gh) — cached 1h (Issues/Candidates pages).</li>
            </ul>
          </details>
          <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
            <SquareTerminal className="size-3.5" aria-hidden />
            Sync, Review and Cleanup run as child processes — follow them under Runs.
          </p>
        </>
      )}
    </div>
  )
}

function OptRow({
  id,
  checked,
  onChange,
  label,
  disabled,
}: {
  id: string
  checked: boolean
  onChange: (v: boolean) => void
  label: string
  disabled?: boolean
}) {
  const [flag, ...rest] = label.split(" — ")
  const description = rest.join(" — ")
  return (
    <div className="flex items-start gap-2">
      <Checkbox
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(v) => onChange(v === true)}
        className="mt-0.5"
      />
      <Label htmlFor={id} className="text-sm font-normal leading-snug">
        <span className="font-mono text-[13px]">{flag}</span>
        {description ? ` — ${description}` : ""}
      </Label>
    </div>
  )
}
