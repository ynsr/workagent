import { Fragment } from "react"
import { Button } from "@/components/ui/button"
import type { FlowRow } from "@/lib/api"
import { cn } from "@/lib/utils"

export interface FlowStepperProps {
  flow: FlowRow
  compact?: boolean
  onResume?: (flow: FlowRow, skipStep: boolean) => void
  resuming?: boolean
  className?: string
}

type StepStatus = "ok" | "running" | "paused" | "failed" | "skipped" | "pending"

interface StepItem {
  label: string
  status: StepStatus
}

const KIND_LABELS: Record<string, string> = {
  start: "Start",
  review: "Review",
  fix_comments: "Fix",
  sync: "Sync",
  cleanup: "Cleanup",
  release: "Release",
  prodtest_plan: "Prodtest",
  action: "Action",
}

function stepLabel(kind: string, actionLabel?: string): string {
  if (kind === "action" && actionLabel) return actionLabel
  return KIND_LABELS[kind] ?? kind
}

function deriveSteps(flow: FlowRow): StepItem[] {
  const effective = flow.effective_steps ?? []
  const results = flow.step_results ?? []
  const count = Math.max(effective.length, results.length)
  if (count === 0) {
    return [{ label: `Step ${flow.current_step}`, status: flow.status === "done" ? "ok" : flow.status === "failed" ? "failed" : flow.status === "paused" ? "paused" : "pending" }]
  }
  const items: StepItem[] = []
  for (let i = 0; i < count; i++) {
    const eff = effective[i]
    const res = results[i]
    const kind = eff?.kind ?? res?.kind ?? `step-${i}`
    const label = stepLabel(kind, eff?.action_label)
    let status: StepStatus = "pending"
    if (res?.status === "ok") status = "ok"
    else if (res?.status === "failed") status = "failed"
    else if (res?.status === "paused") status = "paused"
    else if (res?.status === "skipped") status = "skipped"
    else if (res?.status === "running") status = "running"
    else if (i < flow.current_step) status = "ok"
    else if (i === flow.current_step && flow.status === "running") status = "running"
    else if (i === flow.current_step && flow.status === "paused") status = "paused"
    else if (i === flow.current_step && flow.status === "failed") status = "failed"
    items.push({ label, status })
  }
  return items
}

function statusGlyph(status: StepStatus): { char: string; cls: string; sr: string } {
  switch (status) {
    case "ok":
      return { char: "✓", cls: "text-emerald-600 dark:text-emerald-400", sr: "completed" }
    case "running":
      return { char: "●", cls: "text-primary motion-safe:animate-pulse", sr: "running" }
    case "paused":
      return { char: "●", cls: "text-amber-600 dark:text-amber-400", sr: "paused" }
    case "failed":
      return { char: "✗", cls: "text-destructive", sr: "failed" }
    case "skipped":
      return { char: "–", cls: "text-muted-foreground", sr: "skipped" }
    default:
      return { char: "○", cls: "text-muted-foreground", sr: "pending" }
  }
}

export function FlowStepper({
  flow,
  compact = false,
  onResume,
  resuming = false,
  className,
}: FlowStepperProps) {
  const steps = deriveSteps(flow)
  const isPaused = flow.status === "paused"

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1.5",
        compact ? "text-xs" : "text-[13px]",
        className,
      )}
      aria-label={`Flow ${flow.template_label} progress`}
    >
      <ol className="flex flex-wrap items-center gap-1">
        {steps.map((step, idx) => {
          const g = statusGlyph(step.status)
          return (
            <Fragment key={`${step.label}-${idx}`}>
              <li
                className={cn(
                  "inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono",
                  step.status === "running" && "bg-primary/10 font-medium text-foreground",
                  step.status === "paused" && "bg-amber-500/15 font-medium text-amber-800 dark:text-amber-300",
                  step.status === "failed" && "bg-destructive/10 text-destructive",
                  step.status === "ok" && "text-foreground",
                  (step.status === "pending" || step.status === "skipped") && "text-muted-foreground",
                )}
              >
                <span>{step.label}</span>
                <span aria-hidden className={g.cls}>
                  {g.char}
                </span>
                <span className="sr-only">({g.sr})</span>
              </li>
              {idx < steps.length - 1 ? (
                <li aria-hidden className="text-muted-foreground/60 select-none">
                  →
                </li>
              ) : null}
            </Fragment>
          )
        })}
      </ol>
      {isPaused && !compact && onResume ? (
        <span className="ml-1 inline-flex items-center gap-1">
          <Button
            size="sm"
            variant="secondary"
            className="h-6 px-2 text-xs"
            disabled={resuming}
            onClick={(e) => {
              e.stopPropagation()
              onResume(flow, false)
            }}
          >
            {resuming ? "Resuming…" : "Resume"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-xs"
            disabled={resuming}
            onClick={(e) => {
              e.stopPropagation()
              onResume(flow, true)
            }}
          >
            Skip step
          </Button>
        </span>
      ) : null}
    </div>
  )
}
