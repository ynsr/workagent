import { Fragment, useMemo, useState, type ReactNode } from "react"
import { useSearchParams } from "react-router-dom"
import { FolderOpen, GitPullRequest, History, Info, RefreshCw, Rocket, Search, Trash2, XCircle } from "lucide-react"
import type { Repo, WorktreeMap } from "@/lib/api"
import { repoKeyForPath } from "@/lib/useRepoTabs"
import { prLabel, prUrl } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { WorktreeDetail } from "@/components/WorktreeDetail"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { CiBadge, PrBadge } from "@/components/StateBadge"
import { EmptyState } from "@/components/StatusFeedback"
import { cn } from "@/lib/utils"

export interface StatusTableActions {
  /** Navigate to Launch with mode=sync&ref=key (prefill contract). */
  onSync?: (key: string) => void
  /** Navigate to Launch with mode=review&ref=key (prefill contract). */
  onReview?: (key: string) => void
  onCleanup?: (key: string) => void
  /** Open the worktree folder locally (`open` RunCommand; disabled when network-exposed). */
  onOpenWorktree: (key: string) => void
  onOpenRun: (key: string) => void
  /** Navigate to the Sessions page filtered to this worktree. */
  onOpenSessions?: (key: string) => void
}
function ActionIcon({
  title,
  onClick,
  children,
  destructive,
  disabled,
}: {
  title: string
  onClick: () => void
  children: ReactNode
  destructive?: boolean
  disabled?: boolean
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={title}
      title={title}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className={cn("size-11 text-muted-foreground hover:text-foreground", destructive && "hover:bg-destructive/10 hover:text-destructive")}
    >
      {children}
    </Button>
  )
}

function RowActions({
  worktreeKey,
  entry,
  actions,
  detailOpen,
  onToggleDetail,
  networkExposed,
  overlay = false,
}: {
  worktreeKey: string
  entry: WorktreeMap[string]
  actions: StatusTableActions
  detailOpen: boolean
  onToggleDetail: () => void
  networkExposed: boolean
  overlay?: boolean
}) {
  const invalid = entry.wt_valid === false
  return (
    <div className={overlay ? "flex items-center justify-end gap-0.5" : "flex items-center justify-end gap-0.5 opacity-100 transition-opacity focus-within:opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:hover:opacity-100"}>
      <ActionIcon
        title={detailOpen ? `Hide details of ${worktreeKey}` : `Details of ${worktreeKey}`}
        onClick={onToggleDetail}
      >
        <Info aria-hidden />
      </ActionIcon>
      {actions.onSync ? (
        <ActionIcon title={`Sync ${worktreeKey}`} onClick={() => actions.onSync?.(worktreeKey)}>
          <RefreshCw aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onReview ? (
        <ActionIcon
          title={`Review ${worktreeKey}`}
          onClick={() => actions.onReview?.(worktreeKey)}
        >
          <GitPullRequest aria-hidden />
        </ActionIcon>
      ) : null}
      {actions.onCleanup ? (
        <ActionIcon
          title={invalid ? `Delete invalid worktree ${worktreeKey}` : `Cleanup ${worktreeKey}`}
          onClick={() => actions.onCleanup?.(worktreeKey)}
          destructive
        >
          <Trash2 aria-hidden />
        </ActionIcon>
      ) : null}
      <ActionIcon
        title={
          networkExposed
            ? `Cannot open ${worktreeKey}: disabled while the server is network-exposed`
            : `Open worktree folder of ${worktreeKey}`
        }
        onClick={() => actions.onOpenWorktree(worktreeKey)}
        disabled={networkExposed}
      >
        <FolderOpen aria-hidden />
      </ActionIcon>
      <ActionIcon
        title={`Open runs for ${worktreeKey}`}
        onClick={() => actions.onOpenRun(worktreeKey)}
      >
        <Rocket aria-hidden />
      </ActionIcon>
      {actions.onOpenSessions ? (
        <ActionIcon
          title={`Worktree sessions for ${worktreeKey}`}
          onClick={() => actions.onOpenSessions?.(worktreeKey)}
        >
          <History aria-hidden />
        </ActionIcon>
      ) : null}
    </div>
  )
}

function CommitsCell({ entry }: { entry: WorktreeMap[string] }) {
  const behind = entry.commits_detail?.behind
  const ahead = entry.commits_detail?.ahead
  return (
    <span
      className="font-mono text-[13px]"
      title="commits behind|ahead vs the base branch"
    >
      {typeof behind === "number" ? (
        <span className={behind > 0 ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground"}>
          {behind}
        </span>
      ) : (
        <span className="text-muted-foreground">—</span>
      )}
      <span className="text-muted-foreground">|</span>
      {typeof ahead === "number" ? (
        <span className={ahead > 0 ? "text-sky-600 dark:text-sky-400" : "text-muted-foreground"}>
          {ahead}
        </span>
      ) : null}
    </span>
  )
}

/** First-seen stamp → locale date; missing/unparseable → "—". */
function formatAdded(addedAt: string | undefined): string {
  if (!addedAt) return "—"
  const d = new Date(addedAt)
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString()
}

function matchesQuery(key: string, entry: WorktreeMap[string], q: string): boolean {
  const hay = [
    key,
    entry.branch ?? "",
    entry.pr ?? "",
    entry.pr_detail?.title ?? "",
    entry.worktree ?? "",
  ]
    .join("\n")
    .toLowerCase()
  return hay.includes(q)
}

function RepoTab({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={
        active
          ? "min-h-11 rounded-full bg-primary px-3.5 text-sm font-medium text-primary-foreground"
          : "min-h-11 rounded-full border px-3.5 text-sm text-muted-foreground hover:text-foreground"
      }
    >
      {label}
    </button>
  )
}

/**
 * Worktrees table (GET /api/status or /api/links worktrees).
 * Table at ≥640px, cards below. Search is `?q=`-backed (Runs pattern);
 * default sort is added_at desc, entries without a stamp last.
 */
export function StatusTable({
  worktrees,
  actions,
  showWorktree = false,
  networkExposed = false,
  className,
  repoTabs,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  showWorktree?: boolean
  networkExposed?: boolean
  className?: string
  repoTabs?: { repoFilter: string; setRepo: (v: string) => void; tabs: { names: string[]; counts: Map<string, number>; other: number }; repos: Repo[] }
}) {
  const [params, setParams] = useSearchParams()
  const q = (params.get("q") ?? "").trim()
  const [expanded, setExpanded] = useState<string | null>(null)
  const repoFilter = repoTabs?.repoFilter ?? ""

  const keys = useMemo(() => {
    const needle = q.toLowerCase()
    return Object.keys(worktrees)
      .filter((key) => {
        const entry = worktrees[key]
        if (!entry) return false
        if (repoFilter && repoTabs) {
          const k = repoKeyForPath(entry.worktree ?? "", repoTabs.repos)
          const want = repoFilter === "(other)" ? "(other)" : repoFilter
          if (k !== want) return false
        }
        return !needle || matchesQuery(key, entry, needle)
      })
      .sort((a, b) =>
        (worktrees[b]?.added_at ?? "").localeCompare(worktrees[a]?.added_at ?? ""),
      )
  }, [worktrees, q, repoFilter, repoTabs])

  function setQuery(next: string) {
    const p = new URLSearchParams(params)
    if (next.trim()) p.set("q", next.trim())
    else p.delete("q")
    setParams(p, { replace: true })
  }

  function clearQuery() {
    const p = new URLSearchParams(params)
    p.delete("q")
    setParams(p, { replace: true })
  }

  return (
    <div className={className}>
      {repoTabs && (repoTabs.tabs.names.length > 0 || repoTabs.tabs.other > 0) ? (
        <div role="group" aria-label="Filter by repo" className="mb-3 flex flex-wrap gap-1.5">
          <RepoTab active={!repoFilter} label={`All repos (${repoTabs.tabs.names.reduce((n, name) => n + (repoTabs.tabs.counts.get(name) ?? 0), 0) + repoTabs.tabs.other})`} onClick={() => repoTabs.setRepo("")} />
          {repoTabs.tabs.names.map((n) => (
            <RepoTab
              key={n}
              active={repoFilter === n}
              label={`${n} (${repoTabs.tabs.counts.get(n) ?? 0})`}
              onClick={() => repoTabs.setRepo(n)}
            />
          ))}
          {repoTabs.tabs.other > 0 ? (
            <RepoTab
              active={repoFilter === "(other)"}
              label={`(other) (${repoTabs.tabs.other})`}
              onClick={() => repoTabs.setRepo("(other)")}
            />
          ) : null}
        </div>
      ) : null}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-52 flex-1 sm:max-w-xs">
          <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={params.get("q") ?? ""}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by key, branch, PR, path…"
            aria-label="Filter worktrees"
            className="pl-8"
          />
        </div>
        {q ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-sm">
            <span className="font-mono text-[13px]">{q}</span>
            <button
              aria-label={`Clear worktree filter ${q}`}
              onClick={clearQuery}
              className="text-muted-foreground hover:text-foreground"
            >
              <XCircle aria-hidden className="size-4" />
            </button>
          </span>
        ) : null}
      </div>

      {keys.length === 0 && q ? (
        <EmptyState
          title="No worktrees match this filter"
          description={`Nothing matches "${q}".`}
        >
          <Button variant="outline" size="sm" onClick={clearQuery}>
            Clear filter
          </Button>
        </EmptyState>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden sm:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Worktree</TableHead>
                  <TableHead>Branch</TableHead>
                  <TableHead>Harness</TableHead>
                  <TableHead className="w-20">Behind|Ahead</TableHead>
                  <TableHead>PR / MR</TableHead>
                  <TableHead className="w-14 text-center">CI</TableHead>
                  <TableHead>Added</TableHead>
                  {showWorktree ? <TableHead>Path</TableHead> : null}
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((key) => {
                  const entry = worktrees[key]
                  if (!entry) return null
                  const detailOpen = expanded === key
                  const invalid = entry.wt_valid === false
                  return (
                    <Fragment key={key}>
                      <TableRow className={cn("group relative", invalid ? "bg-destructive/5" : undefined)}>
                        <TableCell className="font-medium">
                          <span className="flex items-center gap-2">
                            <WorktreeKeyLink worktreeKey={key} entry={entry} />
                            {invalid ? (
                              <Badge variant="destructive" title="Recorded path is missing or not a live git worktree">
                                invalid
                              </Badge>
                            ) : null}
                          </span>
                        </TableCell>
                        <TableCell
                          className="max-w-52 truncate font-mono text-[13px]"
                          title={entry.branch}
                        >
                          {entry.branch ?? "—"}
                        </TableCell>
                        <TableCell className="font-mono text-[13px]">
                          {entry.harness ? (
                            <span
                              className="text-muted-foreground"
                              title="live harness (name, pid)"
                            >
                              {entry.harness}
                            </span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </TableCell>
                        <TableCell>
                          <CommitsCell entry={entry} />
                        </TableCell>
                        <TableCell>
                          <div className="flex min-w-0 items-center gap-2">
                            <PrBadge pr={entry.pr_detail ?? null} />
                            {prUrl(entry) ? (
                              <a
                                href={prUrl(entry)}
                                target="_blank"
                                rel="noreferrer"
                                className="truncate text-muted-foreground underline-offset-2 hover:underline"
                                title={`${entry.pr_detail ? entry.pr_detail.title + "\n" : ""}${prUrl(entry)}`}
                              >
                                {entry.pr_detail ? entry.pr_detail.title : prLabel(prUrl(entry)) || entry.pr || "—"}
                              </a>
                            ) : (
                              <span className="truncate text-muted-foreground" title={entry.pr}>
                                {entry.pr_detail ? entry.pr_detail.title : entry.pr || "—"}
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <CiBadge ci={entry.ci} />
                        </TableCell>
                        <TableCell
                          className="whitespace-nowrap text-[13px] text-muted-foreground"
                          title={entry.added_at ?? "first-seen stamp missing"}
                        >
                          {formatAdded(entry.added_at)}
                          {showWorktree ? null : (
                            <span className="pointer-events-none absolute inset-y-1 right-1 hidden items-center justify-end gap-0.5 rounded-md border bg-card/95 px-1 shadow-sm backdrop-blur transition-opacity focus-within:pointer-events-auto focus-within:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100 [@media(hover:hover)]:flex [@media(hover:hover)]:opacity-0 [@media(hover:none)]:flex">
                              <RowActions
                                worktreeKey={key}
                                entry={entry}
                                actions={actions}
                                detailOpen={detailOpen}
                                onToggleDetail={() =>
                                  setExpanded((cur) => (cur === key ? null : key))
                                }
                                networkExposed={networkExposed}
                                overlay
                              />
                            </span>
                          )}
                        </TableCell>
                        {showWorktree ? (
                          <TableCell
                            className="relative max-w-52 truncate pr-24 font-mono text-[13px]"
                            title={entry.worktree}
                          >
                            {entry.worktree ?? "—"}
                            <span className="pointer-events-none absolute inset-y-1 right-1 hidden items-center justify-end gap-0.5 rounded-md border bg-card/95 px-1 shadow-sm backdrop-blur transition-opacity focus-within:pointer-events-auto focus-within:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100 [@media(hover:hover)]:flex [@media(hover:hover)]:opacity-0 [@media(hover:none)]:flex">
                              <RowActions
                                worktreeKey={key}
                                entry={entry}
                                actions={actions}
                                detailOpen={detailOpen}
                                onToggleDetail={() =>
                                  setExpanded((cur) => (cur === key ? null : key))
                                }
                                networkExposed={networkExposed}
                                overlay
                              />
                            </span>
                          </TableCell>
                        ) : null}
                      </TableRow>
                      {detailOpen ? (
                        <TableRow className="hover:bg-transparent">
                          <TableCell
                            colSpan={showWorktree ? 8 : 7}
                          >
                            <div className="mx-auto w-full max-w-2xl py-1">
                              <WorktreeDetail
                                entry={entry}
                                mode="view"
                                onSave={() => undefined}
                                onClose={() => setExpanded(null)}
                              />
                            </div>
                          </TableCell>
                        </TableRow>
                      ) : null}
                    </Fragment>
                  )
                })}
              </TableBody>
            </Table>
          </div>

          {/* Mobile cards */}
          <div className="space-y-3 sm:hidden">
            {keys.map((key) => {
              const entry = worktrees[key]
              if (!entry) return null
              const detailOpen = expanded === key
              const invalid = entry.wt_valid === false
              return (
                <div
                  key={key}
                  className={cn(
                    "rounded-xl border bg-card p-4",
                    invalid && "border-destructive/40 bg-destructive/5",
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="flex min-w-0 flex-wrap items-center gap-2">
                      <WorktreeKeyLink worktreeKey={key} entry={entry} />
                      {invalid ? <Badge variant="destructive">invalid</Badge> : null}
                    </span>
                    <PrBadge pr={entry.pr_detail ?? null} />
                  </div>
                  <dl className="mt-3 space-y-1.5 text-sm">
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">Branch</dt>
                      <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.branch}>
                        {entry.branch ?? "—"}
                      </dd>
                    </div>
                    {entry.harness ? (
                      <div className="flex items-baseline gap-2">
                        <dt className="w-16 shrink-0 text-xs text-muted-foreground">Harness</dt>
                        <dd
                          className="min-w-0 truncate font-mono text-[13px] text-muted-foreground"
                          title="live harness (name, pid)"
                        >
                          {entry.harness}
                        </dd>
                      </div>
                    ) : null}
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">Commits</dt>
                      <dd>
                        <CommitsCell entry={entry} />
                      </dd>
                    </div>
                    {showWorktree && entry.worktree ? (
                      <div className="flex items-baseline gap-2">
                        <dt className="w-16 shrink-0 text-xs text-muted-foreground">Path</dt>
                        <dd className="min-w-0 truncate font-mono text-[13px]" title={entry.worktree}>
                          {entry.worktree}
                        </dd>
                      </div>
                    ) : null}
                    {prUrl(entry) ? (
                      <div className="flex items-baseline gap-2">
                        <dt className="w-16 shrink-0 text-xs text-muted-foreground">PR</dt>
                        <dd className="min-w-0 truncate" title={prUrl(entry)}>
                          <a href={prUrl(entry)} target="_blank" rel="noreferrer" className="underline-offset-2 hover:underline">
                            {prLabel(prUrl(entry))}
                          </a>
                        </dd>
                      </div>
                    ) : null}
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">CI</dt>
                      <dd>
                        <CiBadge ci={entry.ci} />
                      </dd>
                    </div>
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">Added</dt>
                      <dd
                        className="text-[13px] text-muted-foreground"
                        title={entry.added_at ?? "first-seen stamp missing"}
                      >
                        {formatAdded(entry.added_at)}
                      </dd>
                    </div>
                  </dl>
                  <div className="mt-3 border-t pt-1">
                    <RowActions
                      worktreeKey={key}
                      entry={entry}
                      actions={actions}
                      detailOpen={detailOpen}
                      onToggleDetail={() => setExpanded((cur) => (cur === key ? null : key))}
                      networkExposed={networkExposed}
                    />
                  </div>
                  {detailOpen ? (
                    <div className="mt-3">
                      <WorktreeDetail
                        entry={entry}
                        mode="view"
                        onSave={() => undefined}
                        onClose={() => setExpanded(null)}
                      />
                    </div>
                  ) : null}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

function WorktreeKeyLink({
  worktreeKey,
  entry,
}: {
  worktreeKey: string
  entry: WorktreeMap[string]
}) {
  const inner = (
    <span className="font-mono text-[13px] font-semibold">{worktreeKey}</span>
  )
  if (!entry.issue_url) return inner
  return (
    <a
      href={entry.issue_url}
      target="_blank"
      rel="noreferrer"
      className="underline-offset-2 hover:underline"
      onClick={(e) => e.stopPropagation()}
    >
      {inner}
    </a>
  )
}
