import { Fragment, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { Search, XCircle } from "lucide-react"
import type { Repo, WorktreeMap } from "@/lib/api"
import { repoKeyForItem } from "@/lib/useRepoTabs"
import { prLabel, prUrl } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
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
import type { StatusTableActions, StatusTableSelection } from "@/components/StatusTable"

import { RowActions } from "@/components/StatusActions"
import { CopyCell } from "@/components/CopyCell"
import { CommitsCell, ReviewsCell, formatAdded, matchesQuery } from "@/components/StatusCells"
import { shortWorktreePath } from "@/lib/format"
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
 * Fast open/close reveal for row details (~160ms). Grid-rows animation
 * (no max-height guessing, no unmount jump); hidden stays mounted to
 * animate the close. `motion-reduce` skips animation entirely.
 */
function DetailReveal({
  open,
  children,
}: {
  open: boolean
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "grid transition-[grid-template-rows,opacity] duration-150 ease-out motion-reduce:transition-none",
        open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
      )}
    >
      <div className="overflow-hidden">
        <div
          className={cn(
            "transition-transform duration-150 ease-out motion-reduce:transition-none",
            open ? "translate-y-0" : "-translate-y-1",
          )}
        >
          {children}
        </div>
      </div>
    </div>
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
  selection,
}: {
  worktrees: WorktreeMap
  actions: StatusTableActions
  showWorktree?: boolean
  networkExposed?: boolean
  className?: string
  repoTabs?: { repoFilter: string; setRepo: (v: string) => void; tabs: { names: string[]; counts: Map<string, number>; other: number }; repos: Repo[] }
  selection?: StatusTableSelection
}) {
  const [params, setParams] = useSearchParams()
  const q = (params.get("q") ?? "").trim()
  const [expanded, setExpanded] = useState<string | null>(null)
  const repoFilter = repoTabs?.repoFilter ?? ""
  // Selection: controlled when the parent passes `selected`, else internal.
  // `?sel=` carries the keys across refresh/share; unknown keys dropped.
  const [internalSel, setInternalSel] = useState<Set<string>>(new Set<string>())
  const sel = selection?.selected ?? internalSel
  function writeSel(next: Set<string>) {
    if (selection?.onSelectionChange) selection.onSelectionChange(next)
    else setInternalSel(next)
    const p = new URLSearchParams(params)
    if (next.size > 0) p.set("sel", [...next].sort().join(","))
    else p.delete("sel")
    setParams(p, { replace: true })
  }

  const keys = useMemo(() => {
    const needle = q.toLowerCase()
    return Object.keys(worktrees)
      .filter((key) => {
        const entry = worktrees[key]
        if (!entry) return false
        if (repoFilter && repoTabs) {
          const k = repoKeyForItem(entry, repoTabs.repos)
          const want = repoFilter === "(other)" ? "(other)" : repoFilter
          if (k !== want) return false
        }
        return !needle || matchesQuery(key, entry, needle)
      })
      .sort((a, b) =>
        (worktrees[b]?.added_at ?? "").localeCompare(worktrees[a]?.added_at ?? ""),
      )
  }, [worktrees, q, repoFilter, repoTabs])
  // Hydrate `?sel=` once per worktree-set (unknown keys dropped).
  const hydratedFor = useRef<string>("")
  useEffect(() => {
    const known = Object.keys(worktrees).sort().join(",")
    if (hydratedFor.current === known) return
    hydratedFor.current = known
    const raw = (params.get("sel") ?? "").split(",").map((s) => s.trim()).filter(Boolean)
    const valid = raw.filter((k) => k in worktrees)
    if (valid.length === 0) return
    const next = new Set(valid)
    if (selection?.onSelectionChange) selection.onSelectionChange(next)
    else setInternalSel(next)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [worktrees])

  // Report filtered visible keys (select-all scope); join-guard avoids loops.
  const visibleJoined = keys.join("\0")
  const lastVisible = useRef<string>("")
  useEffect(() => {
    if (lastVisible.current === visibleJoined) return
    lastVisible.current = visibleJoined
    selection?.onVisibleKeys?.(keys)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleJoined])

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
    <div className={cn("min-w-0", className)}>
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
        {sel.size > 0 ? (
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-sm">
            <span>{sel.size} selected</span>
            <button
              aria-label={`Clear selection (${sel.size} worktrees)`}
              onClick={() => writeSel(new Set())}
              className="text-muted-foreground hover:text-foreground"
            >
              <XCircle aria-hidden className="size-4" />
            </button>
          </span>
        ) : null}
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
          <div className="hidden min-w-0 sm:block">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">
                    <Checkbox
                      checked={keys.length > 0 && keys.every((k) => sel.has(k)) ? true : keys.some((k) => sel.has(k)) ? "indeterminate" : false}
                      onCheckedChange={(v) => {
                        const next = new Set(sel)
                        if (v === true) keys.forEach((k) => next.add(k))
                        else keys.forEach((k) => next.delete(k))
                        writeSel(next)
                      }}
                      aria-label="Select all visible worktrees"
                    />
                  </TableHead>
                  <TableHead>Worktree</TableHead>
                  <TableHead>{showWorktree ? "Path" : "Branch"}</TableHead>
                  <TableHead className="w-20">Behind|Ahead</TableHead>
                  <TableHead>PR / MR</TableHead>
                  <TableHead className="w-14 text-center">CI</TableHead>
                  <TableHead className="w-20 text-center">Reviews</TableHead>
                  <TableHead>Added</TableHead>
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
                      <TableRow aria-selected={sel.has(key)} data-state={sel.has(key) ? "selected" : undefined} onClick={(e) => { const t = e.target as HTMLElement; if (t.closest("a,button,[role=checkbox],input")) return; setExpanded((cur) => (cur === key ? null : key)) }} className={cn("group relative cursor-pointer", sel.has(key) ? "data-[state=selected]:bg-muted/50" : undefined, invalid ? "bg-destructive/5" : undefined)}>
                        <TableCell className="w-10" onClick={(e) => e.stopPropagation()}>
                          <Checkbox
                            checked={sel.has(key)}
                            onCheckedChange={() => {
                              const next = new Set(sel)
                              if (next.has(key)) next.delete(key)
                              else next.add(key)
                              writeSel(next)
                            }}
                            aria-label={`Select ${key}`}
                          />
                        </TableCell>
                        <TableCell className="max-w-48 font-medium">
                          <span className="flex min-w-0 items-center gap-1.5">
                            <span className="min-w-0 flex-1">
                              {entry.issue_url ? (
                                <a
                                  href={entry.issue_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="block truncate font-mono text-[13px] font-semibold underline-offset-2 hover:underline"
                                  onClick={(e) => e.stopPropagation()}
                                  title={key}
                                >
                                  {key}
                                </a>
                              ) : (
                                <CopyCell text={key} className="font-mono text-[13px] font-semibold" />
                              )}
                            </span>
                            {entry.harness ? (
                              <span
                                aria-label={`Live harness: ${entry.harness}`}
                                title={`Live harness: ${entry.harness}`}
                                className="size-2 shrink-0 animate-pulse rounded-full bg-emerald-500"
                              />
                            ) : null}
                          </span>
                          {invalid ? (
                            <Badge variant="destructive" title="Recorded path is missing or not a live git worktree">
                              invalid
                            </Badge>
                          ) : null}
                        </TableCell>
                        {showWorktree ? (
                          <TableCell className="max-w-48 font-mono text-[13px]">
                            <CopyCell text={shortWorktreePath(entry.worktree)} copyText={entry.worktree} />
                          </TableCell>
                        ) : (
                          <TableCell className="max-w-48 font-mono text-[13px]">
                            <CopyCell text={entry.branch} />
                          </TableCell>
                        )}
                        <TableCell>
                          <CommitsCell entry={entry} />
                        </TableCell>
                        <TableCell className="max-w-64">
                          <div className="flex min-w-0 items-center gap-2">
                            <PrBadge pr={entry.pr_detail ?? null} />
                            {prUrl(entry) ? (
                              <a
                                href={prUrl(entry)}
                                target="_blank"
                                rel="noreferrer"
                                className="min-w-0 flex-1 truncate text-muted-foreground underline-offset-2 hover:underline"
                                title={`${entry.pr_detail ? entry.pr_detail.title + "\n" : ""}${prUrl(entry)}`}
                              >
                                {entry.pr_detail ? entry.pr_detail.title : prLabel(prUrl(entry)) || entry.pr || "—"}
                              </a>
                            ) : (
                              <span className="min-w-0 flex-1 truncate text-muted-foreground" title={entry.pr}>
                                {entry.pr_detail ? entry.pr_detail.title : entry.pr || "—"}
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-center">
                          <CiBadge ci={entry.ci} ciUrl={entry.ci_url} />
                        </TableCell>
                        <TableCell className="text-center">
                          <ReviewsCell entry={entry} />
                        </TableCell>
                        <TableCell
                          className="whitespace-nowrap text-[13px] text-muted-foreground"
                          title={entry.added_at ?? "first-seen stamp missing"}
                        >
                          {formatAdded(entry.added_at)}
                          <span className="pointer-events-none absolute inset-y-1 right-1 hidden items-center justify-end gap-0.5 rounded-md border bg-card/95 px-1 shadow-sm backdrop-blur transition-opacity focus-within:pointer-events-auto focus-within:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100 [@media(hover:hover)]:flex [@media(hover:hover)]:opacity-0 [@media(hover:none)]:flex">
                            <RowActions
                              worktreeKey={key}
                              entry={entry}
                              actions={actions}
                              overlay
                            />
                          </span>
                        </TableCell>
                      </TableRow>
                      <TableRow className="hover:bg-transparent">
                        <TableCell
                          colSpan={8}
                          className={detailOpen ? "py-1" : "border-0 !p-0"}
                        >
                          <DetailReveal open={detailOpen}>
                            <div className="mx-auto w-full max-w-2xl py-1">
                              <WorktreeDetail
                                entry={entry}
                                worktreeKey={key}
                                mode="view"
                                onSave={() => undefined}
                                onClose={() => setExpanded(null)}
                                onOpenWorktree={actions.onOpenWorktree}
                                onOpenRun={actions.onOpenRun}
                                onOpenSessions={actions.onOpenSessions}
                                networkExposed={networkExposed}
                              />
                            </div>
                          </DetailReveal>
                        </TableCell>
                      </TableRow>
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
                      <Checkbox
                        checked={sel.has(key)}
                        onCheckedChange={() => {
                          const next = new Set(sel)
                          if (next.has(key)) next.delete(key)
                          else next.add(key)
                          writeSel(next)
                        }}
                        aria-label={`Select ${key}`}
                      />
                      <WorktreeKeyLink worktreeKey={key} entry={entry} />
                      {invalid ? <Badge variant="destructive">invalid</Badge> : null}
                    </span>
                    <PrBadge pr={entry.pr_detail ?? null} />
                  </div>
                  <dl className="mt-3 space-y-1.5 text-sm">
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">{showWorktree ? "Path" : "Branch"}</dt>
                      <dd className="min-w-0 truncate font-mono text-[13px]" title={showWorktree ? entry.worktree : entry.branch}>
                        {showWorktree ? shortWorktreePath(entry.worktree) || "—" : entry.branch ?? "—"}
                      </dd>
                    </div>
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">Commits</dt>
                      <dd>
                        <CommitsCell entry={entry} />
                      </dd>
                    </div>
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
                        <CiBadge ci={entry.ci} ciUrl={entry.ci_url} />
                      </dd>
                    </div>
                    <div className="flex items-baseline gap-2">
                      <dt className="w-16 shrink-0 text-xs text-muted-foreground">Reviews</dt>
                      <dd>
                        <ReviewsCell entry={entry} />
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
                    />
                  </div>
                  <div className="mt-1">
                    <DetailReveal open={detailOpen}>
                      <div className="pt-2">
                        <WorktreeDetail
                          entry={entry}
                          worktreeKey={key}
                          mode="view"
                          onSave={() => undefined}
                          onClose={() => setExpanded(null)}
                          onOpenWorktree={actions.onOpenWorktree}
                          onOpenRun={actions.onOpenRun}
                          onOpenSessions={actions.onOpenSessions}
                          networkExposed={networkExposed}
                        />
                      </div>
                    </DetailReveal>
                  </div>
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
