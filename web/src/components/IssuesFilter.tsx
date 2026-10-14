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

export type IssuesStatusFilter = "" | "open" | "closed" | "in progress"

/** Default `created after` cutoff: 30 days ago, as `YYYY-MM-DD`. */
export function defaultIssuesAfter(): string {
  const d = new Date()
  d.setDate(d.getDate() - 30)
  return d.toISOString().slice(0, 10)
}

/**
 * Client-side filter over loaded issue rows. Scope derives from the
 * existing server queries (jira rows are reporter=me, github rows are
 * author=@me), so no fetch change is needed:
 * - `mine`: jira rows (reported to me) — off by default
 * - `all`: every other row (not mine, not created by me) — off by default
 * - `createdByMe`: github rows (I authored them) — on by default
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
    const isJira = issue.key.toLowerCase().startsWith("jira:")
    const isGh = issue.key.toLowerCase().startsWith("github:")
    const mine = isJira
    const created = isGh
    const inScope = scope.all
      ? true
      : (scope.mine && mine) || (scope.createdByMe && created) || (!mine && !created)
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
      }
    }
    return true
  })
}

/** Filter form + filtered count for the Recent issues tab. */
export function useIssuesFilter(rows: IssueRow[]) {
  const [scope, setScope] = useState<IssuesScope>(DEFAULT_ISSUES_SCOPE)
  const [after, setAfter] = useState<string>(defaultIssuesAfter)
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState<IssuesStatusFilter>("")
  const filtered = useMemo(
    () => filterIssues(rows, scope, after, query, status),
    [rows, scope, after, query, status],
  )
  return { scope, setScope, after, setAfter, query, setQuery, status, setStatus, filtered }
}

export function IssuesFilterForm({
  scope,
  setScope,
  after,
  setAfter,
  query,
  setQuery,
  status,
  setStatus,
  total,
  shown,
}: {
  scope: IssuesScope
  setScope: (s: IssuesScope) => void
  after: string
  setAfter: (v: string) => void
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
          <Label htmlFor="issues-after">Created after</Label>
          <Input
            id="issues-after"
            type="date"
            value={after}
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => setAfter(e.target.value)}
          />
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
              {["(any status)", "open", "in progress", "closed"].map((s) => (
                <SelectItem key={s} value={s}>
                  {s === "(any status)" ? "Any status" : s.charAt(0).toUpperCase() + s.slice(1)}
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
