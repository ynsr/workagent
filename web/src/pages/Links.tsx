import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { toast } from "sonner"
import { Link2, Plus, RefreshCw, Unlink, UserPlus } from "lucide-react"
import { CandidatesCard } from "@/components/CandidatesCard"
import { PageHeader } from "@/components/PageHeader"
import { StatusTable } from "@/components/StatusTable"
import { useRepoTabs } from "@/lib/useRepoTabs"
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
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { useConfirmedRun } from "@/components/RunActions"
import { useInfo, useRepos, useStatusAll } from "@/lib/queries"
import {
  LinkRemoveDialog,
  LinkSetDialog,
  RegisterDialog,
  TrackerMappingsCard,
} from "@/components/LinkCards"

export function Links() {
  const navigate = useNavigate()
  const runConfirmed = useConfirmedRun()
  const { data: worktrees, isPending, isError, error, refetch } = useStatusAll()
  const { data: info } = useInfo()
  const { data: repos } = useRepos()
  const repoTabs = useRepoTabs(
    Object.values(worktrees ?? {}),
    repos,
  )
  const [showWorktree, setShowWorktree] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [forceAll, setForceAll] = useState(false)
  const [activeForm, setActiveForm] = useState<null | "set" | "remove" | "register">(
    null,
  )

  async function handleRefreshPr() {
    setRefreshing(true)
    try {
      await refetch()
      toast.success("Worktrees refreshed")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshing(false)
    }
  }

  async function handleReviewAll() {
    setForceAll(false)
    await runConfirmed({
      action: "review",
      title: "Review all worktrees",
      description:
        "Reviews every not-reviewed linked worktree in parallel (non-TTY). Skips worktrees without a PR/MR, with a live harness, or with unresolved PR comments. Reviewed worktrees whose tip moved are reviewed again.",
      destructive: true,
      confirmLabel: "Review all",
      details: [{ label: "Scope", value: "Every linked worktree" }],
      extras: (
        <div className="flex items-start gap-2">
          <Checkbox
            id="reviewall-force-links"
            checked={forceAll}
            onCheckedChange={(v) => setForceAll(v === true)}
            className="mt-0.5"
          />
          <Label htmlFor="reviewall-force-links" className="text-sm font-normal leading-snug">
            <span className="font-mono text-[13px]">--force-all</span>
            {" — include already-reviewed and unresolved-comment worktrees too"}
          </Label>
        </div>
      ),
      command: "review",
      args: ["--all", ...(forceAll ? ["--force-all"] : [])],
      successMessage: "Review all started",
    })
  }
  async function handleCleanupMerged() {
    await runConfirmed({
      action: "cleanup",
      title: "Cleanup merged worktrees",
      description:
        "Removes every linked worktree whose PR/MR is merged or closed. Cleanup closes the tracker issue, removes the worktree, deletes the branch and closes the PR. Live harnesses and invalid worktrees are skipped, never torn down.",
      destructive: true,
      confirmLabel: "Cleanup merged",
      details: [{ label: "Scope", value: "Merged/closed PRs only" }],
      command: "cleanup",
      args: ["--merged"],
      successMessage: "Cleanup merged started",
    })
  }

  async function handleCleanup(key: string) {
    const invalid = worktrees?.[key]?.wt_valid === false
    await runConfirmed({
      action: "cleanup",
      ref: key,
      title: invalid ? `Delete invalid worktree ${key}` : `Remove worktree ${key}`,
      description: invalid
        ? "The recorded path is missing or not a live git worktree. --force skips state validation; the entry is removed either way. This cannot be undone."
        : "Closes the tracker issue, removes the worktree, deletes the branch and closes the PR. This cannot be undone.",
      destructive: true,
      confirmLabel: invalid ? "Delete worktree" : "Remove worktree",
      force: invalid || undefined,
      command: "cleanup",
      args: invalid ? [key, "--force"] : [key],
      successMessage: `Cleanup ${key} started`,
    })
  }

  async function handleOpenWorktree(key: string) {
    await runConfirmed({
      action: null,
      title: `Opening ${key}`,
      description: `Opens the worktree directory for ${key}.`,
      confirmLabel: "Open",
      confirm: false,
      command: "open",
      args: [key],
      successMessage: `Opening ${key}`,
    })
  }

  const tableActions = {
    onCleanup: handleCleanup,
    onOpenWorktree: handleOpenWorktree,
    onOpenRun: (key: string) => navigate(`/runs?target=${encodeURIComponent(key)}`),
    onOpenSessions: (key: string) => navigate(`/sessions?worktree=${encodeURIComponent(key)}`),
  }

  return (
    <div className="grid gap-6">
      <PageHeader
        title="Links"
        description="Tracker ↔ repo mappings, linked worktrees, and registering existing worktrees."
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
            <Button variant="outline" size="sm" onClick={handleReviewAll}>
              Review all
            </Button>
            <Button variant="outline" size="sm" onClick={handleCleanupMerged}>
              Cleanup merged
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
          <CardTitle>Linked worktrees</CardTitle>
          <CardDescription>
            Same table as the Dashboard, from GET /api/status (link list shape).
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isPending ? (
            <TableSkeleton rows={4} />
          ) : isError ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : !worktrees || Object.keys(worktrees).length === 0 ? (
            <EmptyState
              icon={<Link2 className="size-10" aria-hidden />}
              title="No linked worktrees"
              description="Worktrees appear once linked to a tracker."
            />
          ) : (
            <StatusTable
              worktrees={worktrees}
              actions={tableActions}
              showWorktree={showWorktree}
              networkExposed={info?.network_exposed ?? false}
              repoTabs={repos ? { ...repoTabs, repos } : undefined}
            />
          )}
        </CardContent>
      </Card>

      <CandidatesCard />
    </div>
  )
}
