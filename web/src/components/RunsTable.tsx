import type { ReactNode } from "react"
import { Link } from "react-router-dom"
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
import { argsText, relativeTime } from "@/lib/format"

/** One table row: live runs link to /runs/:id, persisted session runs
 * (no registry id) render a plain label and skip state/actions they lack. */
export interface RunsTableRow {
  key: string
  /** Link target for the run cell; plain text when absent. */
  href?: string
  label: string
  command: string
  args: string[]
  target: string
  state?: RunState
  exitCode: number | null
  /** Epoch seconds for the Started column. */
  started: number
  startedTitle?: string
  actions?: ReactNode
}

export function RunsTable({ rows }: { rows: RunsTableRow[] }) {
  return (
    <>
      {/* Table ≥sm */}
      <div className="hidden rounded-xl border sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              <TableHead>Command</TableHead>
              <TableHead>Target</TableHead>
              <TableHead>State</TableHead>
              <TableHead className="text-right">Exit</TableHead>
              <TableHead>Started</TableHead>
              <TableHead className="w-12" aria-label="Cancel" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.key}>
                <TableCell className="font-mono text-[13px]">
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
                </TableCell>
                <TableCell>
                  <span className="font-medium">{row.command}</span>{" "}
                  <span className="font-mono text-[13px] text-muted-foreground">
                    {argsText(row.args)}
                  </span>
                </TableCell>
                <TableCell className="font-mono text-[13px]">{row.target}</TableCell>
                <TableCell>
                  {row.state ? <RunStateBadge state={row.state} /> : "—"}
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
            ))}
          </TableBody>
        </Table>
      </div>
      {/* Cards <sm */}
      <div className="space-y-3 sm:hidden">
        {rows.map((row) => (
          <Card key={row.key} className="py-4">
            <CardContent className="space-y-2 px-4">
              <div className="flex items-center justify-between gap-2">
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
                {row.state ? <RunStateBadge state={row.state} /> : null}
              </div>
              <p className="text-sm">
                <span className="font-medium">{row.command}</span>{" "}
                <span className="font-mono text-[13px] text-muted-foreground">
                  {argsText(row.args)}
                </span>
              </p>
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
        ))}
      </div>
    </>
  )
}
