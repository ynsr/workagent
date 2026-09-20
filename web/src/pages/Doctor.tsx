import { CheckCircle2, CircleAlert, Stethoscope } from "lucide-react"
import { PageHeader } from "@/components/PageHeader"
import {
  ErrorState,
  TableSkeleton,
} from "@/components/StatusFeedback"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useDoctor } from "@/lib/queries"

export function Doctor() {
  const { data, isPending, isError, error, refetch } = useDoctor()

  if (isPending) {
    return (
      <div>
        <PageHeader
          title="Doctor"
          description="Environment status, config drift, and external tool availability."
        />
        <TableSkeleton rows={5} />
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div>
        <PageHeader
          title="Doctor"
          description="Environment status, config drift, and external tool availability."
        />
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    )
  }

  const ok = data.status === "ok"
  const stale = data.status === "stale"
  const tools = Object.entries(data.tools).sort(([a], [b]) => a.localeCompare(b))

  return (
    <>
      <PageHeader
        title="Doctor"
        description="Environment status, config drift, and external tool availability."
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {ok ? (
                <CheckCircle2 aria-hidden className="size-5 text-emerald-500" />
              ) : (
                <CircleAlert aria-hidden className="size-5 text-amber-500" />
              )}
              Environment:{" "}
              <Badge variant={ok ? "default" : "secondary"} className="font-mono">
                {data.status}
              </Badge>
            </CardTitle>
            <CardDescription>
              {ok
                ? "Recorded environment matches the live one."
                : stale
                  ? "The harness config has changed since the last doctor pass — re-run the doctor command in a terminal to refresh the receipt."
                  : "Environment state unknown."}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm">
            <div className="flex items-center justify-between rounded-md border px-3 py-2">
              <span className="text-muted-foreground">live hash</span>
              <span className="max-w-56 truncate font-mono text-[13px]" title={data.live_hash}>
                {data.live_hash ?? "—"}
              </span>
            </div>
            <div className="flex items-center justify-between rounded-md border px-3 py-2">
              <span className="text-muted-foreground">recorded hash</span>
              <span className="max-w-56 truncate font-mono text-[13px]" title={data.recorded_hash}>
                {data.recorded_hash ?? "—"}
              </span>
            </div>
            {data.receipt ? (
              <div className="flex items-center justify-between rounded-md border px-3 py-2">
                <span className="text-muted-foreground">receipt</span>
                <span className="max-w-56 truncate font-mono text-[13px]" title={data.receipt}>
                  {data.receipt}
                </span>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>External tools</CardTitle>
            <CardDescription>
              {ok || stale
                ? "Availability checked by the harness at the last doctor pass."
                : "Availability per tool (values from the harness registry)."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {tools.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No tools recorded.
              </p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead className="text-right">Available</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tools.map(([name, available]) => (
                    <TableRow key={name}>
                      <TableCell className="font-mono text-[13px]">{name}</TableCell>
                      <TableCell className="text-right">
                        {available ? (
                          <Badge variant="default">yes</Badge>
                        ) : (
                          <Badge variant="secondary">missing</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      {stale ? (
        <Card className="mt-6 border-amber-500/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Stethoscope aria-hidden className="size-4 text-amber-500" />
              Stale environment
            </CardTitle>
            <CardDescription>
              Re-run <span className="font-mono text-[13px]">harness doctor</span> in a
              terminal to regenerate the receipt — the web UI only reads it.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : null}
    </>
  )
}
