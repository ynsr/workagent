import { useMemo } from "react"
import type { WorktreeMap } from "@/lib/api"
import { matchesQuery } from "@/components/StatusCells"

export type PrStatusFilter = "" | "open" | "merged" | "closed" | "none"
export type IssueFilter = "" | "none" | "not-done" | string

export interface WorktreeFilterState {
  q: string
  pr: PrStatusFilter
  issue: IssueFilter
  author: string
}

export const EMPTY_WORKTREE_FILTERS: WorktreeFilterState = { q: "", pr: "", issue: "", author: "" }

/** PR state for filtering: lowercased pr_detail.state, "none" when no PR. */
export function prStatusOf(entry: WorktreeMap[string]): "open" | "merged" | "closed" | "none" {
  const s = (entry.pr_detail?.state ?? "").trim().toLowerCase()
  if (s === "open" || s === "opened") return "open"
  if (s === "merged") return "merged"
  if (s === "closed" || s === "close") return "closed"
  return "none"
}

/**
 * Issue status for filtering: lowercased `issue_status`, "" when the
 * worktree has no linked issue or the status is unknown.
 */
export function issueStatusOf(entry: WorktreeMap[string]): string {
  return (entry.issue_status ?? "").trim().toLowerCase()
}

/**
 * Distinct linked issue statuses across rows (sorted), for the filter
 * dropdown. Unknown/absent statuses are not listed.
 */
export function issueStatusOptions(worktrees: WorktreeMap): string[] {
  const names = new Set<string>()
  for (const entry of Object.values(worktrees)) {
    const s = issueStatusOf(entry)
    if (s) names.add(s)
  }
  return [...names].sort((a, b) => a.localeCompare(b))
}

/**
 * "Not Done" grouping: open or in-progress tracker states (To Do, In
 * Progress, Review, Product Test, ...); Done/Resolved/Closed and legacy
 * `released`, plus unknown, are done. `released`/`closed` stay accepted
 * for trackers that still report them.
 */
export function isIssueNotDone(status: string): boolean {
  const s = (status ?? "").trim().toLowerCase()
  if (!s || s === "done" || s === "released" || s === "resolved" || s === "closed") return false
  return true
}

/** Shared predicate (issue #102): one filter form drives every Dashboard table. */
export function worktreeMatchesFilter(
  key: string,
  entry: WorktreeMap[string],
  f: WorktreeFilterState,
  repoFilter = "",
  repoOf?: (entry: WorktreeMap[string]) => string,
): boolean {
  if (!entry) return false
  if (repoFilter && repoOf && repoOf(entry) !== repoFilter) return false
  if (f.pr && prStatusOf(entry) !== f.pr) return false
  const issueStatus = issueStatusOf(entry)
  if (f.issue === "none" && issueStatus) return false
  else if (f.issue === "not-done" && !isIssueNotDone(issueStatus)) return false
  else if (f.issue && f.issue !== "none" && f.issue !== "not-done" && issueStatus !== f.issue) return false
  const authorNeedle = f.author.trim().toLowerCase()
  if (authorNeedle && !((entry?.pr_detail?.author ?? "").toLowerCase().includes(authorNeedle)) return false
  const needle = f.q.trim().toLowerCase()
  return !needle || matchesQuery(key, entry, needle)
}

/** Filter a WorktreeMap by the shared filter state (pure, unit-testable). */
export function filterWorktreeKeys(
  worktrees: WorktreeMap,
  f: WorktreeFilterState,
  repoFilter = "",
  repoOf?: (entry: WorktreeMap[string]) => string,
): string[] {
  return Object.keys(worktrees).filter((key) => {
    const entry = worktrees[key]
    if (!entry) return false
    return worktreeMatchesFilter(key, entry, f, repoFilter, repoOf)
  })
}

/** React hook: memoized filtered key list for a WorktreeMap. */
export function useFilteredWorktreeKeys(
  worktrees: WorktreeMap,
  f: WorktreeFilterState,
  repoFilter = "",
  repoOf?: (entry: WorktreeMap[string]) => string,
): string[] {
  void useMemo
  return filterWorktreeKeys(worktrees, f, repoFilter, repoOf)
}
