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
import { CleanupDialog } from "@/components/CleanupDialog"
import { PageHeader } from "@/components/PageHeader"
import { StatusTable } from "@/components/StatusTable"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { Button } from "@/components/ui/button"
import { CheckRow } from "@/components/FieldHelp"
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
  "reviews",
  "reviews_unresolved",
  "reviews_resolved",
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
    e.reviews ?? "",
    String(e.reviews_detail?.unresolved ?? ""),
    String(e.reviews_detail?.resolved ?? ""),
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
    Object.values(worktrees ?? {}),
    repos,
  )

  const [showWorktree, setShowWorktree] = useState(false)
  const [refreshingPr, setRefreshingPr] = useState(false)
  const [syncMerge, setSyncMerge] = useState(false)
  const [reviewForceAll, setReviewForceAll] = useState(false)
  const [cleanupTarget, setCleanupTarget] = useState<string | null>(null)
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
    setSyncMerge(false)
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
          <CheckRow
            id="syncall-merge"
            checked={syncMerge}
            onChange={setSyncMerge}
            label="Merge locally"
            flag="--merge (-m)"
            description="Merge locally instead of the remote rebase"
          />
        </div>
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "sync",
        args: ["--all", ...(syncMerge ? ["--merge"] : [])],
        confirm: true,
      })
      runCreated(run_id, "Sync all")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleReviewAll() {
    setReviewForceAll(false)
    const ok = await confirm({
      action: "review",
      title: "Review all worktrees",
      description:
        "Reviews every not-reviewed linked worktree in parallel (non-TTY). Skips worktrees without a PR/MR, with a live harness, or with unresolved PR comments. Reviewed worktrees whose tip moved are reviewed again.",
      destructive: true,
      confirmLabel: "Review all",
      details: [{ label: "Scope", value: "Every linked worktree" }],
      extras: (
        <div className="grid gap-2.5">
          <CheckRow
            id="reviewall-force"
            checked={reviewForceAll}
            onChange={setReviewForceAll}
            label="Include already-reviewed worktrees"
            flag="--force-all"
            description="Include already-reviewed and unresolved-comment worktrees too"
          />
        </div>
      ),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "review",
        args: ["--all", ...(reviewForceAll ? ["--force-all"] : [])],
        confirm: true,
      })
      runCreated(run_id, "Review all")
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleFixAll() {
    const ok = await confirm({
      action: "review",
      title: "Fix PR comments on all worktrees",
      description:
        "Fixes open (not-resolved) PR/MR review comments on every linked worktree with a PR/MR (non-TTY). Validates each finding against the code and PR/MR description, resolves/closes fixed comments (GitHub bot comments get a `Status: RESOLVED` second line), then commits and pushes.",
      destructive: true,
      confirmLabel: "Fix all",
      details: [{ label: "Scope", value: "Every linked worktree with a PR/MR" }],
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "review",
        args: ["--all", "--fix-comments"],
        confirm: true,
      })
      runCreated(run_id, "Fix all PR comments")
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
    // Local dialog owns force state (CleanupDialog);
    // the shared confirm() extras snapshot would go stale on toggle.
    setCleanupTarget(key)
  }

  async function submitCleanup(key: string, opts: { force: boolean } | { action: "deactivate" } | { action: "deleteFromDb" }) {
    setCleanupTarget(null)
    if ("action" in opts) {
      try {
        const cmd = opts.action === "deactivate" ? ["deactivate", key] : ["remove", key]
        const { run_id } = await createRun.mutateAsync({ command: "link", args: cmd, confirm: true })
        runCreated(run_id, opts.action === "deactivate" ? `Deactivated ${key}` : `Deleted ${key} from DB`)
      } catch (err) {
        toast.error(errorText(err))
      }
      return
    }
    try {
      const { run_id } = await createRun.mutateAsync({
        command: "cleanup",
        args: [key, ...(opts.force ? ["--force"] : [])],
        confirm: true,
        force: opts.force || undefined,
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
      "workagent-status.csv",
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
    onFixComments: (key: string) =>
      navigate("/launch?mode=review&fixComments=1&ref=" + encodeURIComponent(key)),
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
            <Button variant="outline" size="sm" onClick={handleFixAll}>
              Fix all PR comments
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
              <li><span className="font-mono">gh pr list</span> / <span className="font-mono">glab mr list</span> per branch — PR result cached 3d, no-PR result 30min (refresh: Refresh PR).</li>
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
      {cleanupTarget ? (
        <CleanupDialog
          worktreeKey={cleanupTarget}
          invalid={worktrees?.[cleanupTarget]?.wt_valid === false}
          onClose={(res) => {
            if (!res) {
              setCleanupTarget(null)
              return
            }
            void submitCleanup(cleanupTarget, res)
          }}
        />
      ) : null}
    </div>
  )
}
