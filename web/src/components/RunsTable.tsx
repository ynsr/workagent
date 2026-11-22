import { Fragment, useMemo, useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { ChevronDown, ChevronRight } from "lucide-react"
import { CopyCell } from "@/components/CopyCell"
import { RunStateBadge } from "@/components/StateBadge"
import { Card, CardContent } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { RunState } from "@/lib/api"
import { argsText, formatCostUsd, formatDurationMs, formatTokens, relativeTime } from "@/lib/format"
import { cn } from "@/lib/utils"

/** One table row: live runs link to /runs/:id, persisted rows
 * (`db-<id>`) render their stored label and the transcript kind. */
export interface RunsTableRow {
  key: string
  /** Link target for the run cell; plain text when absent. */
  href?: string
  label: string
  command: string
  args: string[]
  /** Transcript kind label (start/review/fix comments/custom action/…). */
  kind: string
  target: string
  state?: RunState
  exitCode: number | null
  /** Epoch seconds for the Started column. */
  started: number
  startedTitle?: string
  actions?: ReactNode
  /** Parent run key when spawned by a flow / review --all / --then-fix-comments. */
  parentRunId?: string | null
  /** 0-based step index within the parent run. */
  stepIndex?: number | null
  durationMs?: number | null
  modelId?: string
  effort?: string
  inputTokens?: number | null
  outputTokens?: number | null
  cachedTokens?: number | null
  costUsd?: number | null
}

function TelemetryPills({ row }: { row: RunsTableRow }) {
  const dur = formatDurationMs(row.durationMs)
  const totalTok =
    row.inputTokens != null || row.outputTokens != null
      ? (row.inputTokens ?? 0) + (row.outputTokens ?? 0)
      : null
  const tokLabel = formatTokens(totalTok)
  const costLabel = formatCostUsd(row.costUsd)
  const hasAny = Boolean(dur || row.modelId || row.effort || tokLabel || costLabel)
  if (!hasAny) return <span className="text-muted-foreground">—</span>
  const tokTitle = [
    row.inputTokens != null ? `in: ${row.inputTokens.toLocaleString()}` : "",
    row.outputTokens != null ? `out: ${row.outputTokens.toLocaleString()}` : "",
    row.cachedTokens != null ? `cached: ${row.cachedTokens.toLocaleString()}` : "",
  ]
    .filter(Boolean)
    .join(" · ")
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs">
      {dur ? (
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground">
          {dur}
        </span>
      ) : null}
      {row.modelId ? (
        <span
          className="max-w-32 truncate rounded border bg-background px-1.5 py-0.5 font-mono text-[11px]"
          title={row.effort ? `${row.modelId} (${row.effort})` : row.modelId}
        >
          {row.modelId}
          {row.effort ? ` · ${row.effort}` : ""}
        </span>
      ) : row.effort ? (
        <span className="rounded border bg-background px-1.5 py-0.5 font-mono text-[11px]">
          {row.effort}
        </span>
      ) : null}
      {tokLabel || costLabel ? (
        <span
          className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
          title={tokTitle || undefined}
        >
          {[tokLabel ? `${tokLabel} tok` : "", costLabel].filter(Boolean).join(" · ")}
        </span>
      ) : null}
    </div>
  )
}

function parentSummaryLabel(parent: RunsTableRow, children: RunsTableRow[]): string {
  const total = children.length
  const runningIdx = children.findIndex((c) => c.state === "running")
  const activeStep = runningIdx >= 0 ? runningIdx + 1 : total
  const suffix = runningIdx >= 0 ? `step ${activeStep}/${total} running` : `${total} step${total === 1 ? "" : "s"}`
  if (parent.command === "flow" && parent.args[0] === "run" && parent.args[1]) {
    return `Flow: ${parent.args[1]} (${suffix})`
  }
  return `${parent.command} (${suffix})`
}

export function RunsTable({
  rows,
  activeIndex = -1,
  activeKey = null,
  flat = false,
}: {
  rows: RunsTableRow[]
  activeIndex?: number
  activeKey?: string | null
  flat?: boolean
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const { topRows, childrenByParent } = useMemo(() => {
    if (flat) {
      return { topRows: rows, childrenByParent: new Map<string, RunsTableRow[]>() }
    }
    const byKey = new Map<string, RunsTableRow>()
    for (const r of rows) byKey.set(r.key, r)
    const map = new Map<string, RunsTableRow[]>()
    const top: RunsTableRow[] = []
    for (const r of rows) {
      if (r.parentRunId && r.parentRunId !== r.key && byKey.has(r.parentRunId)) {
        const list = map.get(r.parentRunId) ?? []
        list.push(r)
        map.set(r.parentRunId, list)
      } else {
        top.push(r)
      }
    }
    for (const list of map.values()) {
      list.sort((a, b) => {
        const sa = a.stepIndex ?? Number.MAX_SAFE_INTEGER
        const sb = b.stepIndex ?? Number.MAX_SAFE_INTEGER
        if (sa !== sb) return sa - sb
        return a.started - b.started
      })
    }
    return { topRows: top, childrenByParent: map }
  }, [rows, flat])

  function isExpanded(row: RunsTableRow): boolean {
    if (row.key in expanded) return Boolean(expanded[row.key])
    return row.state === "running" || row.state === "needs_input"
  }

  function toggleExpand(key: string, current: boolean) {
    setExpanded((prev) => ({ ...prev, [key]: !current }))
  }

  return (
    <>
      {/* Table ≥sm */}
      <div className="hidden rounded-xl border sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              <TableHead>Command</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>State</TableHead>
              <TableHead>Usage</TableHead>
              <TableHead className="text-right">Exit</TableHead>
              <TableHead>Started</TableHead>
              <TableHead className="w-12" aria-label="Cancel" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {topRows.map((row, idx) => {
              const children = childrenByParent.get(row.key) ?? []
              const hasChildren = children.length > 0
              const open = hasChildren && isExpanded(row)
              const isFocused = activeKey != null ? row.key === activeKey : idx === activeIndex
              return (
                <Fragment key={row.key}>
                  <TableRow
                    data-focused={isFocused ? "true" : undefined}
                    className={isFocused ? "bg-accent/50" : undefined}
                  >
                    <TableCell className="font-mono text-[13px]">
                      <div className="flex items-center gap-1">
                        {hasChildren ? (
                          <button
                            type="button"
                            aria-expanded={open}
                            aria-label={`Toggle child runs for ${row.label}`}
                            onClick={() => toggleExpand(row.key, open)}
                            className="inline-flex size-5 items-center justify-center rounded hover:bg-muted"
                          >
                            {open ? (
                              <ChevronDown aria-hidden className="size-3.5" />
                            ) : (
                              <ChevronRight aria-hidden className="size-3.5" />
                            )}
                          </button>
                        ) : null}
                        {row.href ? (
                          <Link
                            to={row.href}
                            className="underline-offset-2 hover:underline"
                          >
                            {row.label}
                          </Link>
                        ) : (
                          row.label
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-64">
                      {hasChildren ? (
                        <div className="flex flex-col gap-0.5">
                          <span className="truncate text-sm font-medium">
                            {parentSummaryLabel(row, children)}
                          </span>
                          <CopyCell text={`${row.command} ${argsText(row.args)}`.trim()} className="text-xs text-muted-foreground">
                            <span className="truncate font-mono">
                              {row.command} {argsText(row.args)}
                            </span>
                          </CopyCell>
                        </div>
                      ) : (
                        <CopyCell text={`${row.command} ${argsText(row.args)}`.trim()} className="text-sm">
                          <span className="truncate">
                            <span className="font-medium">{row.command}</span>{" "}
                            <span className="font-mono text-[13px] text-muted-foreground">
                              {argsText(row.args)}
                            </span>
                          </span>
                        </CopyCell>
                      )}
                    </TableCell>
                    <TableCell className="text-sm">{row.kind}</TableCell>
                    <TableCell className="max-w-48 font-mono text-[13px]">
                      <CopyCell text={row.target} />
                    </TableCell>
                    <TableCell>
                      {row.state ? <RunStateBadge state={row.state} /> : "—"}
                    </TableCell>
                    <TableCell className="max-w-56">
                      <TelemetryPills row={row} />
                    </TableCell>
                    <TableCell className="text-right font-mono text-[13px]">
                      {row.exitCode ?? "—"}
                    </TableCell>
                    <TableCell
                      className="text-muted-foreground"
                      title={row.startedTitle}
                    >
                      {relativeTime(row.started)}
                    </TableCell>
                    <TableCell>
                      {row.actions ? (
                        <div className="flex items-center">{row.actions}</div>
                      ) : null}
                    </TableCell>
                  </TableRow>
                  {open
                    ? children.map((child) => (
                        <TableRow
                          key={child.key}
                          data-child-of={row.key}
                          className="bg-muted/30"
                        >
                          <TableCell className="pl-8 font-mono text-[13px]">
                            <div className="flex items-center gap-1.5 border-l-2 border-muted-foreground/30 pl-2">
                              {child.stepIndex != null ? (
                                <span className="rounded bg-muted px-1 py-0.5 text-[11px] text-muted-foreground">
                                  #{child.stepIndex}
                                </span>
                              ) : null}
                              {child.href ? (
                                <Link
                                  to={child.href}
                                  className="underline-offset-2 hover:underline"
                                >
                                  {child.label}
                                </Link>
                              ) : (
                                child.label
                              )}
                            </div>
                          </TableCell>
                          <TableCell className="max-w-64">
                            <CopyCell text={`${child.command} ${argsText(child.args)}`.trim()} className="text-sm">
                              <span className="truncate">
                                <span className="font-medium">{child.command}</span>{" "}
                                <span className="font-mono text-[13px] text-muted-foreground">
                                  {argsText(child.args)}
                                </span>
                              </span>
                            </CopyCell>
                          </TableCell>
                          <TableCell className="text-sm">{child.kind}</TableCell>
                          <TableCell className="max-w-48 font-mono text-[13px]">
                            <CopyCell text={child.target} />
                          </TableCell>
                          <TableCell>
                            {child.state ? <RunStateBadge state={child.state} /> : "—"}
                          </TableCell>
                          <TableCell className="max-w-56">
                            <TelemetryPills row={child} />
                          </TableCell>
                          <TableCell className="text-right font-mono text-[13px]">
                            {child.exitCode ?? "—"}
                          </TableCell>
                          <TableCell
                            className="text-muted-foreground"
                            title={child.startedTitle}
                          >
                            {relativeTime(child.started)}
                          </TableCell>
                          <TableCell>
                            {child.actions ? (
                              <div className="flex items-center">{child.actions}</div>
                            ) : null}
                          </TableCell>
                        </TableRow>
                      ))
                    : null}
                </Fragment>
              )
            })}
          </TableBody>
        </Table>
      </div>
      {/* Cards <sm */}
      <div className="space-y-3 sm:hidden">
        {topRows.map((row, idx) => {
          const children = childrenByParent.get(row.key) ?? []
          const hasChildren = children.length > 0
          const open = hasChildren && isExpanded(row)
          const isFocused = activeKey != null ? row.key === activeKey : idx === activeIndex
          return (
            <div key={row.key} className="space-y-2">
              <Card
                data-focused={isFocused ? "true" : undefined}
                className={isFocused ? "py-4 ring-2 ring-ring" : "py-4"}
              >
                <CardContent className="space-y-2 px-4">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5">
                      {hasChildren ? (
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-label={`Toggle child runs for ${row.label}`}
                          onClick={() => toggleExpand(row.key, open)}
                          className="inline-flex size-6 items-center justify-center rounded hover:bg-muted"
                        >
                          {open ? (
                            <ChevronDown aria-hidden className="size-4" />
                          ) : (
                            <ChevronRight aria-hidden className="size-4" />
                          )}
                        </button>
                      ) : null}
                      {row.href ? (
                        <Link
                          to={row.href}
                          className="font-mono text-[13px] font-semibold underline-offset-2 hover:underline"
                        >
                          {row.label}
                        </Link>
                      ) : (
                        <span className="font-mono text-[13px] font-semibold">
                          {row.label}
                        </span>
                      )}
                    </div>
                    {row.state ? <RunStateBadge state={row.state} /> : null}
                  </div>
                  <p className="text-sm">
                    {hasChildren ? (
                      <span className="font-medium">{parentSummaryLabel(row, children)}</span>
                    ) : (
                      <>
                        <span className="font-medium">{row.command}</span>{" "}
                        <span className="font-mono text-[13px] text-muted-foreground">
                          {argsText(row.args)}
                        </span>
                      </>
                    )}
                  </p>
                  <p className="text-xs text-muted-foreground">{row.kind}</p>
                  <p
                    className="truncate font-mono text-[13px] text-muted-foreground"
                    title={row.target}
                  >
                    {row.target}
                  </p>
                  <div className="flex items-center justify-between border-t pt-2 text-xs text-muted-foreground">
                    <span>
                      exit {row.exitCode ?? "—"} · {relativeTime(row.started)}
                    </span>
                    {row.actions ? (
                      <div className="flex items-center">{row.actions}</div>
                    ) : null}
                  </div>
                </CardContent>
              </Card>
              {open ? (
                <div className={cn("space-y-2 border-l-2 border-muted-foreground/30 pl-3")}>
                  {children.map((child) => (
                    <Card key={child.key} className="bg-muted/30 py-3">
                      <CardContent className="space-y-1.5 px-3">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs font-semibold">
                            {child.stepIndex != null ? `#${child.stepIndex} ` : ""}
                            {child.href ? (
                              <Link to={child.href} className="underline-offset-2 hover:underline">
                                {child.label}
                              </Link>
                            ) : (
                              child.label
                            )}
                          </span>
                          {child.state ? <RunStateBadge state={child.state} /> : null}
                        </div>
                        <p className="text-xs">
                          <span className="font-medium">{child.command}</span>{" "}
                          <span className="font-mono text-muted-foreground">{argsText(child.args)}</span>
                        </p>
                        <div className="flex items-center justify-between border-t pt-1.5 text-xs text-muted-foreground">
                          <span title={child.startedTitle}>
                            exit {child.exitCode ?? "—"} · {relativeTime(child.started)}
                          </span>
                          {child.actions ? (
                            <div className="flex items-center">{child.actions}</div>
                          ) : null}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
    </>
  )
}
