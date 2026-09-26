import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { Copy, GitPullRequest, Play, RefreshCw, Ticket, UserPlus } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { useConfirm } from "@/lib/confirm"
import { copyToClipboard } from "@/lib/format"
import { api } from "@/lib/api"
import { queryKeys, useCandidates, useCreateRun } from "@/lib/queries"
import { cn } from "@/lib/utils"
import { toast } from "sonner"

type Tab = "prs" | "issues" | "worktrees"

const TABS: readonly { value: Tab; label: (n: number) => string }[] = [
  { value: "prs", label: (n) => `Unlinked PR/MRs (${n})` },
  { value: "issues", label: (n) => `Recent issues (${n})` },
  { value: "worktrees", label: (n) => `Worktrees (${n})` },
]

/**
 * Unlinked PR/MRs + recent issues + unregistered worktrees from
 * GET /api/candidates.
 * Per-row copy-ref plus explicit Start/Review/Register actions through the
 * run pipeline (confirm modal for start/review). Never auto-starts or links.
 */
export function CandidatesCard() {
  const navigate = useNavigate()
  const confirm = useConfirm()
  const createRun = useCreateRun()
  const qc = useQueryClient()
  const { data, isPending, isError, error, refetch, isFetching } = useCandidates()
  const [tab, setTab] = useState<Tab>("prs")
  const [copied, setCopied] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  async function handleCopyRef(ref: string) {
    try {
      await copyToClipboard(ref)
      setCopied(ref)
      window.setTimeout(() => setCopied((c) => (c === ref ? null : c)), 1200)
      toast.success(`Copied ${ref}`)
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  async function handleRefresh() {
    setRefreshing(true)
    try {
      const fresh = await qc.fetchQuery({
        queryKey: [...queryKeys.candidates, true],
        queryFn: () => api.candidates({ force: true }),
        staleTime: 0,
      })
      qc.setQueryData(queryKeys.candidates, fresh)
      toast.success("Candidates re-fetched live")
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setRefreshing(false)
    }
  }

  async function handleAction(input: {
    command: "start" | "review" | "register"
    args: string[]
    label: string
    confirmText: string
  }) {
    const ok = await confirm({
      action: input.command === "register" ? null : input.command,
      title: input.label,
      description: input.confirmText,
      confirmLabel: input.label,
      details: input.args.map((a, i) => ({ label: i === 0 ? "Ref" : `Arg ${i}`, value: a, mono: true })),
    })
    if (!ok) return
    try {
      const { run_id } = await createRun.mutateAsync({
        command: input.command,
        args: input.args,
        confirm: true,
      })
      toast.success(`${input.label} started`, {
        action: { label: "View run", onClick: () => navigate(`/runs/${run_id}`) },
      })
      void qc.invalidateQueries({ queryKey: queryKeys.links })
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const prs = data?.prs ?? []
  const issues = data?.issues ?? []
  const scanned = data?.worktrees ?? []
  const warnings = data?.warnings ?? []
  const counts: Record<Tab, number> = {
    prs: prs.length,
    issues: issues.length,
    worktrees: scanned.length,
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle>Candidates</CardTitle>
            <CardDescription>
              Unlinked open PR/MRs across every registered repo, plus my issues
              created in the last 7 days. Issue rows show the Launch default
              repo when one resolves.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleRefresh()}
            disabled={isFetching || refreshing}
          >
            <RefreshCw className={isFetching || refreshing ? "animate-spin" : undefined} aria-hidden />
            Refresh
          </Button>
        </div>
        <div role="tablist" aria-label="Candidates" className="flex gap-1.5 pt-1">
          {TABS.map((t) => (
            <button
              key={t.value}
              role="tab"
              aria-selected={tab === t.value}
              onClick={() => setTab(t.value)}
              className={cn(
                "min-h-9 rounded-full px-3.5 text-sm font-medium",
                tab === t.value
                  ? "bg-primary text-primary-foreground"
                  : "border text-muted-foreground hover:text-foreground",
              )}
            >
              {t.label(counts[t.value])}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {warnings.length > 0 ? (
          <p className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
            {warnings.join("; ")}
          </p>
        ) : null}
        {isPending ? (
          <TableSkeleton rows={3} />
        ) : isError ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : tab === "prs" ? (
          prs.length === 0 ? (
            <EmptyState
              icon={<GitPullRequest className="size-10" aria-hidden />}
              title="No unlinked PRs"
              description="Every open PR/MR is already linked to a worktree."
            />
          ) : (
            <ul className="divide-y rounded-md border">
              {prs.map((pr) => (
                <li key={pr.url} className="flex items-center gap-2 px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={pr.title}>
                      {pr.title || pr.url}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground" title={pr.url}>
                      {pr.key} · {pr.branch} · {pr.repo}
                    </p>
                  </div>
                  <Badge variant="outline">{pr.state}</Badge>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleAction({
                      command: "review", args: [pr.url],
                      label: `Review ${pr.key}`,
                      confirmText: `Run review for ${pr.url} in a worktree.`,
                    })}
                    title={`Review ${pr.url}`}
                  >
                    <Play aria-hidden /> Review
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : tab === "issues" ? (
          issues.length === 0 ? (
            <EmptyState
              icon={<Ticket className="size-10" aria-hidden />}
              title="No recent issues"
              description="No issues created in the last 7 days."
            />
          ) : (
            <ul className="divide-y rounded-md border">
              {issues.map((issue) => (
                <li key={issue.key} className="flex items-center gap-2 px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={issue.title}>
                      {issue.title || issue.key}
                    </p>
                    <p className="truncate font-mono text-xs text-muted-foreground" title={issue.url}>
                      {issue.key} · {issue.status}{issue.repo_hint ? ` · ${issue.repo_hint}` : ""}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleAction({
                      command: "start", args: [issue.key],
                      label: `Start ${issue.key}`,
                      confirmText: `Start a worktree for ${issue.key}.`,
                    })}
                    title={`Start ${issue.key}`}
                  >
                    <Play aria-hidden /> Start
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void handleCopyRef(issue.key)}
                    title={`Copy ref ${issue.key}`}
                  >
                    <Copy aria-hidden /> {copied === issue.key ? "Copied" : "Copy ref"}
                  </Button>
                </li>
              ))}
            </ul>
          )
        ) : scanned.length === 0 ? (
          <EmptyState
            icon={<GitPullRequest className="size-10" aria-hidden />}
            title="No unregistered worktrees"
            description="Every worktree under the scan root is already linked."
          />
        ) : (
          <ul className="divide-y rounded-md border">
            {scanned.map((wt) => (
              <li key={wt.path} className="flex items-center gap-2 px-3 py-2">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium" title={wt.branch}>
                    {wt.branch}
                  </p>
                  <p className="truncate font-mono text-xs text-muted-foreground" title={wt.path}>
                    {wt.key_guess} · {wt.repo} · {wt.path}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleAction({
                    command: "register", args: [wt.path],
                    label: `Register ${wt.branch}`,
                    confirmText: `Register worktree ${wt.path} (key ${wt.key_guess}).`,
                  })}
                  title={`Register ${wt.path}`}
                >
                  <UserPlus aria-hidden /> Register
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleCopyRef(wt.path)}
                  title={`Copy path ${wt.path}`}
                >
                  <Copy aria-hidden /> {copied === wt.path ? "Copied" : "Copy path"}
                </Button>
              </li>
            ))}
          </ul>
         )}
      </CardContent>
    </Card>
  )
}
