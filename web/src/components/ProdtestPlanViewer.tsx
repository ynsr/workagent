import { useState } from "react"
import { toast } from "sonner"
import { AlertTriangle, CheckCircle2, Copy, FileCode2, ListChecks } from "lucide-react"
import { useProdtestPlan } from "@/lib/queries"
import { copyToClipboard } from "@/lib/format"
import { HighlightedLine } from "@/lib/syntaxHighlight"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { EmptyState, ErrorState, errorText } from "@/components/StatusFeedback"

const RISK_TONE: Record<string, string> = {
  low: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  medium: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  high: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300",
  critical: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300",
}

export function ProdtestPlanViewer({
  issueKey,
  className = "",
}: {
  issueKey: string
  className?: string
}) {
  const [tab, setTab] = useState<"cases" | "yaml">("cases")
  const { data, isPending, isError, error, refetch } = useProdtestPlan(issueKey)

  if (isPending) {
    return <div className="py-6 text-center text-sm text-muted-foreground">Loading prodtest plan…</div>
  }
  if (isError) {
    return <ErrorState error={error} onRetry={() => void refetch()} />
  }
  if (!data) {
    return (
      <EmptyState
        icon={<FileCode2 className="size-8" aria-hidden />}
        title="No prodtest plan"
        description={`No plan file found for ${issueKey}.`}
      />
    )
  }

  const yamlLines = data.yaml.split("\n")

  return (
    <div className={`flex flex-col gap-3 ${className}`} data-testid="prodtest-plan-viewer">
      {/* Metadata header */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-sm font-semibold">{data.issue_key}</span>
          {data.status ? <Badge variant="outline">{data.status}</Badge> : null}
          {data.plan_revision != null ? (
            <Badge variant="secondary">rev{data.plan_revision}</Badge>
          ) : null}
          {data.plan_risk ? (
            <Badge variant="outline" className={RISK_TONE[data.plan_risk.toLowerCase()] ?? ""}>
              {data.plan_risk}
            </Badge>
          ) : null}
        </div>
        <div className="flex items-center gap-1.5">
          <div className="flex items-center gap-1" role="group" aria-label="Plan view mode">
            <Button
              type="button"
              size="sm"
              variant={tab === "cases" ? "default" : "outline"}
              onClick={() => setTab("cases")}
            >
              <ListChecks className="mr-1 size-3.5" aria-hidden />
              Cases ({data.cases.length})
            </Button>
            <Button
              type="button"
              size="sm"
              variant={tab === "yaml" ? "default" : "outline"}
              onClick={() => setTab("yaml")}
            >
              <FileCode2 className="mr-1 size-3.5" aria-hidden />
              YAML
            </Button>
          </div>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            title="Copy YAML"
            aria-label="Copy plan YAML"
            onClick={() =>
              copyToClipboard(data.yaml)
                .then(() => toast.success("Plan YAML copied"))
                .catch((err) => toast.error(errorText(err)))
            }
          >
            <Copy className="size-3.5" aria-hidden />
          </Button>
        </div>
      </div>

      {data.summary ? <p className="text-sm text-muted-foreground">{data.summary}</p> : null}

      {data.parse_error ? (
        <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          <AlertTriangle className="size-4 shrink-0" aria-hidden />
          <span>YAML parse warning: {data.parse_error}</span>
        </div>
      ) : null}

      {tab === "cases" ? (
        <div className="space-y-3">
          {data.preconditions.length > 0 ? (
            <div className="rounded-md border bg-muted/20 p-3">
              <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Preconditions ({data.preconditions.length})
              </div>
              <ul className="space-y-1 text-xs">
                {data.preconditions.map((p, idx) => (
                  <li key={p.id ?? idx} className="flex items-start gap-1.5">
                    <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
                    <span>
                      {p.id ? <span className="mr-1.5 font-mono font-medium">{p.id}</span> : null}
                      {p.title ?? p.description ?? JSON.stringify(p)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {data.cases.length === 0 ? (
            <EmptyState
              icon={<ListChecks className="size-8" aria-hidden />}
              title="No structured cases"
              description="Switch to the YAML tab to inspect the raw plan file."
            />
          ) : (
            <div className="space-y-2">
              {data.cases.map((c, idx) => (
                <div
                  key={c.id ?? idx}
                  className="rounded-md border p-3 text-xs"
                  data-testid="prodtest-plan-case"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 font-medium">
                      {c.id ? <span className="font-mono text-primary">{c.id}</span> : null}
                      <span>{c.title ?? "Untitled case"}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1">
                      {c.kind ? <Badge variant="secondary">{c.kind}</Badge> : null}
                      {c.priority ? <Badge variant="outline">{c.priority}</Badge> : null}
                      {c.safety ? <Badge variant="outline">{c.safety}</Badge> : null}
                      {c.tool ? <Badge variant="outline" className="font-mono">{c.tool}</Badge> : null}
                    </div>
                  </div>
                  {c.description ? (
                    <p className="mt-1.5 text-muted-foreground">{c.description}</p>
                  ) : null}
                  {Array.isArray(c.expect) && c.expect.length > 0 ? (
                    <div className="mt-1.5 text-[11px] text-muted-foreground">
                      {c.expect.length} expectation{c.expect.length === 1 ? "" : "s"}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="max-h-[480px] overflow-auto rounded-md border bg-muted/20 font-mono text-xs">
          {data.truncated ? (
            <div className="border-b bg-amber-500/10 px-3 py-1.5 text-amber-800 dark:text-amber-300">
              Plan YAML truncated due to size cap.
            </div>
          ) : null}
          <table className="w-full border-collapse">
            <tbody>
              {yamlLines.map((line, i) => (
                <tr key={i} className="hover:bg-muted/40">
                  <td className="w-10 select-none border-r px-2 py-0.5 text-right text-muted-foreground">
                    {i + 1}
                  </td>
                  <td className="whitespace-pre-wrap break-all px-2.5 py-0.5">
                    <HighlightedLine text={line} path={data.plan_path || "plan.yaml"} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
