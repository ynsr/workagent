import { Search, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { PrStatusFilter, IssueFilter } from "@/components/StatusTableMain"
import { issueStatusOptions, type WorktreeFilterState } from "@/lib/worktreeFilter"

export { EMPTY_WORKTREE_FILTERS, filterWorktreeKeys, prStatusOf, issueStatusOf, issueStatusOptions, isIssueNotDone } from "@/lib/worktreeFilter"
export type { WorktreeFilterState } from "@/lib/worktreeFilter"

/**
 * Shared worktree filter toolbar (issue #102): one form above the Dashboard
 * tables driving StatusTable + DeletedTable + DeactivatedTable, and reusable
 * on Runs/Flows where the same predicate logic applies.
 *
 * Controlled: the parent owns the state (URL-backed on Dashboard); StatusTable
 * renders this same form internally when no external filter is supplied.
 */
export function WorktreeFilterForm({
  filter,
  onChange,
  worktrees,
  showAuthor = true,
  idPrefix = "wt",
}: {
  filter: WorktreeFilterState
  onChange: (next: WorktreeFilterState) => void
  worktrees: Parameters<typeof issueStatusOptions>[0]
  showAuthor?: boolean
  idPrefix?: string
}) {
  const statusOptions = issueStatusOptions(worktrees)
  const authorOptions = (() => {
    const names = new Set<string>()
    for (const entry of Object.values(worktrees ?? {})) {
      const a = (entry?.pr_detail?.author ?? "").trim()
      if (a) names.add(a)
    }
    return [...names].sort((a, b) => a.localeCompare(b))
  })()
  const hasActive = Boolean(filter.q.trim() || filter.pr || filter.issue || filter.author.trim())

  function clear() {
    onChange({ q: "", pr: "", issue: "", author: "" })
  }

  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <div className="relative min-w-52 flex-1 sm:max-w-xs">
        <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={filter.q}
          onChange={(e) => onChange({ ...filter, q: e.target.value })}
          placeholder="Filter by key, branch, PR, path…"
          aria-label="Filter worktrees"
          className="pl-8"
        />
      </div>
      <Select value={filter.pr || "all"} onValueChange={(v) => onChange({ ...filter, pr: (v === "all" ? "" : v) as PrStatusFilter })}>
        <SelectTrigger size="sm" aria-label="Filter by PR status" className="w-36">
          <SelectValue placeholder="PR status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">PR: all</SelectItem>
          <SelectItem value="open">PR: open</SelectItem>
          <SelectItem value="merged">PR: merged</SelectItem>
          <SelectItem value="closed">PR: closed</SelectItem>
          <SelectItem value="none">PR: none</SelectItem>
        </SelectContent>
      </Select>
      <Select value={filter.issue || "all"} onValueChange={(v) => onChange({ ...filter, issue: (v === "all" ? "" : v) as IssueFilter })}>
        <SelectTrigger size="sm" aria-label="Filter by issue status" className="w-44">
          <SelectValue placeholder="Issue status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">Issue: all</SelectItem>
          <SelectItem value="not-done">Issue: not done</SelectItem>
          {statusOptions.map((s) => (
            <SelectItem key={s} value={s}>
              {`Issue: ${s}`}
            </SelectItem>
          ))}
          <SelectItem value="none">Issue: none</SelectItem>
        </SelectContent>
      </Select>
      {showAuthor ? (
        <Select value={filter.author || "all"} onValueChange={(v) => onChange({ ...filter, author: v === "all" ? "" : v })}>
          <SelectTrigger size="sm" aria-label="Filter by PR author" className="w-40">
            <SelectValue placeholder="Author" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">Author: all</SelectItem>
            {authorOptions.map((a) => (
              <SelectItem key={a} value={a}>
                {a}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : null}
      {hasActive ? (
        <button
          type="button"
          onClick={clear}
          aria-label={`${idPrefix}-clear-filters`}
          className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-sm text-muted-foreground hover:text-foreground"
        >
          Clear filters
          <XCircle aria-hidden className="size-4" />
        </button>
      ) : null}
    </div>
  )
}

/** Selected-count pill + visible-key summary shared by bulk-action toolbars. */
export function WorktreeSelectionPill({
  selected,
  visibleKeys,
  onClear,
}: {
  selected: Set<string>
  visibleKeys?: string[]
  onClear: () => void
}) {
  if (selected.size === 0) return null
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border bg-muted px-3 py-1 text-sm">
      <span>
        {selected.size} selected
        {visibleKeys ? ` · ${visibleKeys.length} visible` : ""}
      </span>
      <button
        aria-label={`Clear selection (${selected.size} worktrees)`}
        onClick={onClear}
        className="text-muted-foreground hover:text-foreground"
      >
        <XCircle aria-hidden className="size-4" />
      </button>
      <span className="sr-only">
        <Button variant="ghost" size="sm" tabIndex={-1}>x</Button>
      </span>
    </span>
  )
}
