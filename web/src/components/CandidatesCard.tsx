import { useState } from "react"
import { Copy, GitPullRequest, RefreshCw, Ticket } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
  errorText,
} from "@/components/StatusFeedback"
import { copyToClipboard } from "@/lib/format"
import { useCandidates } from "@/lib/queries"
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
 * Read-only: per-row copy-ref, refresh button. Never auto-starts or links.
 */
export function CandidatesCard() {
  const { data, isPending, isError, error, refetch, isFetching } = useCandidates()
  const [tab, setTab] = useState<Tab>("prs")
  const [copied, setCopied] = useState<string | null>(null)

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
              created in the last 7 days.
            </CardDescription>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refetch()}
            disabled={isFetching}
          >
            <RefreshCw className={isFetching ? "animate-spin" : undefined} aria-hidden />
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
                    onClick={() => void handleCopyRef(pr.url)}
                    title={`Copy ref ${pr.url}`}
                  >
                    <Copy aria-hidden /> {copied === pr.url ? "Copied" : "Copy ref"}
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
                      {issue.key} · {issue.status}
                    </p>
                  </div>
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
