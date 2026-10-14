import { useMemo, useState } from "react"
import { CheckRow } from "@/components/FieldHelp"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { EmptyState } from "@/components/StatusFeedback"
import { Ticket } from "lucide-react"
import type { IssueRow } from "@/lib/api"

/** Scope toggles for the Recent issues filter (independent; All wins). */
export interface IssuesScope {
  mine: boolean
  all: boolean
  createdByMe: boolean
}

export const DEFAULT_ISSUES_SCOPE: IssuesScope = {
  mine: false,
  all: false,
  createdByMe: true,
}

export type IssuesStatusFilter = "" | "not done" | "open" | "closed" | "in progress"

/** Predefined Created-after ranges; default Last 30 days. */
export const ISSUES_RANGES = [
  { value: "7", label: "Last 7 days", days: 7 },
  { value: "30", label: "Last 30 days", days: 30 },
  { value: "90", label: "Last 90 days", days: 90 },
  { value: "365", label: "Last year", days: 365 },
] as const
export type IssuesRange = (typeof ISSUES_RANGES)[number]["value"]
export const DEFAULT_ISSUES_RANGE: IssuesRange = "30"

/** Cutoff `YYYY-MM-DD` for a range value (days back from today). */
export function rangeCutoff(range: IssuesRange): string {
  const entry = ISSUES_RANGES.find((r) => r.value === range)
  const days = entry ? entry.days : 30
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

/**
 * Client-side filter over loaded issue rows. Scope derives from the
 * existing server queries, so no fetch change is needed:
 * - `mine`: rows reported/assigned to me — jira rows carry reporter=me
 *   (the server JQL is `reporter = currentUser()`); github rows authored
 *   by me also qualify. Off by default.
 * - `all`: every loaded row, ignoring scope. Off by default, wins when on.
 * - `createdByMe`: rows I authored/reported — jira (reporter) + github
 *   (author). On by default. This is what the server returned before any
 *   filter existed, so default-on preserves the old table contents.
 * `all` wins when combined with the others. Status matches loosely
 * (Open matches "open"/"to do", In Progress matches jira variants);
 * Closed matches "closed"/"done"/"resolved".
 */
export function filterIssues(
  rows: IssueRow[],
  scope: IssuesScope,
  after: string,
  query: string,
  status: IssuesStatusFilter,
): IssueRow[] {
  const q = query.trim().toLowerCase()
  const afterMs = after ? Date.parse(`${after}T00:00:00Z`) : NaN
  return rows.filter((issue) => {
    // Both server queries are me-scoped by construction (jira JQL
    // `reporter = currentUser()`, gh `--author=@me`), so key prefix is the
    // reliable me-signal — field presence fails for old plain-seam jira
    // rows that carry no reporter. Non-me rows (if any) qualify only via
    // an explicit author/reporter value.
    const lk = issue.key.toLowerCase()
    const meScoped = lk.startsWith("jira:") || lk.startsWith("github:")
      || Boolean((issue.author || issue.reporter || "").trim())
    const inScope = scope.all
      ? true
      : (scope.mine && meScoped) || (scope.createdByMe && meScoped)
    if (!inScope) return false
    if (!Number.isNaN(afterMs) && after) {
      const createdMs = Date.parse(issue.created || "")
      if (!Number.isNaN(createdMs) && createdMs < afterMs) return false
    }
    if (q) {
      const hay = `${issue.title || ""}\n${issue.description || ""}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    if (status) {
      const st = (issue.status || "").trim().toLowerCase()
      if (status === "open") {
        if (!(st === "open" || st === "to do" || st === "todo" || st === "opened")) return false
      } else if (status === "in progress") {
        if (!(st.includes("progress") || st.includes("in progress"))) return false
      } else if (status === "closed") {
        if (!(st === "closed" || st === "done" || st === "resolved")) return false
      } else if (status === "not done") {
        if (st === "closed" || st === "done" || st === "resolved") return false
      }
    }
    return true
  })
}

/** Filter form + filtered count for the Recent issues tab. */
export function useIssuesFilter(rows: IssueRow[]) {
  const [scope, setScope] = useState<IssuesScope>(DEFAULT_ISSUES_SCOPE)
  const [range, setRange] = useState<IssuesRange>(DEFAULT_ISSUES_RANGE)
  const after = rangeCutoff(range)
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState<IssuesStatusFilter>("not done")
  const filtered = useMemo(
    () => filterIssues(rows, scope, after, query, status),
    [rows, scope, after, query, status],
  )
  return { scope, setScope, range, setRange, after, query, setQuery, status, setStatus, filtered }
}

export function IssuesFilterForm({
  scope,
  setScope,
  range,
  setRange,
  query,
  setQuery,
  status,
  setStatus,
  total,
  shown,
}: {
  scope: IssuesScope
  setScope: (s: IssuesScope) => void
  range: IssuesRange
  setRange: (v: IssuesRange) => void
  query: string
  setQuery: (v: string) => void
  status: IssuesStatusFilter
  setStatus: (v: IssuesStatusFilter) => void
  total: number
  shown: number
}) {
  return (
    <form
      className="mb-3 grid gap-3 rounded-md border px-3 py-2.5"
      onSubmit={(e) => e.preventDefault()}
      aria-label="Filter recent issues"
    >
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        <CheckRow
          id="issues-scope-mine"
          checked={scope.mine}
          onChange={(v) => setScope({ ...scope, mine: v })}
          label="My Issues"
          flag="scope"
          description="Issues reported to me (jira)"
        />
        <CheckRow
          id="issues-scope-all"
          checked={scope.all}
          onChange={(v) => setScope({ ...scope, all: v })}
          label="All Issues"
          flag="scope"
          description="Every loaded row, ignoring scope"
        />
        <CheckRow
          id="issues-scope-created"
          checked={scope.createdByMe}
          onChange={(v) => setScope({ ...scope, createdByMe: v })}
          label="My Created Issues"
          flag="scope"
          description="Issues I authored (github)"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="grid gap-1">
          <Label htmlFor="issues-range">Created within</Label>
          <Select
            value={range}
            onValueChange={(v) => setRange(v as IssuesRange)}
          >
            <SelectTrigger id="issues-range">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ISSUES_RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="issues-search">Search title or description</Label>
          <Input
            id="issues-search"
            type="search"
            placeholder="Filter by title or description…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="issues-status">Status</Label>
          <Select
            value={status || "(any status)"}
            onValueChange={(v) => setStatus(v === "(any status)" ? "" : (v as IssuesStatusFilter))}
          >
            <SelectTrigger id="issues-status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.entries({
                "(any status)": "Any status",
                "not done": "Not Done",
                "open": "Open",
                "in progress": "In Progress",
                "closed": "Closed",
              }) as [IssuesStatusFilter | "(any status)", string][]).map(([v, label]) => (
                <SelectItem key={v} value={v}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        Showing {shown} of {total} loaded issues.
      </p>
    </form>
  )
}

export function NoFilteredIssues({ onClear }: { onClear: () => void }) {
  return (
    <div className="grid gap-2">
      <EmptyState
        icon={<Ticket className="size-10" aria-hidden />}
        title="No issues match the filters"
        description="Adjust the scope, date, search, or status above."
      />
      <div className="flex justify-center">
        <Button variant="outline" size="sm" onClick={onClear}>
          Clear filters
        </Button>
      </div>
    </div>
  )
}
