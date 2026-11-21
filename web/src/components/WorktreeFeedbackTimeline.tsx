import { useState } from "react"
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  ExternalLink,
  MessageSquare,
  Play,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react"
import { toast } from "sonner"
import type { PrCheckRow, PrReviewThreadRow, WorktreeEntry, WorktreeEventRow } from "@/lib/api"
import { prUrl } from "@/lib/api"
import { useCreateRun, useWorktreeTimeline } from "@/lib/queries"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { errorText } from "@/components/StatusFeedback"

const FAILING_CONCLUSIONS = new Set([
  "failure",
  "failed",
  "timed_out",
  "action_required",
  "startup_failure",
  "cancelled",
  "canceled",
])

function isFailingCheck(chk: PrCheckRow): boolean {
  return (
    FAILING_CONCLUSIONS.has((chk.conclusion || "").toLowerCase()) ||
    (chk.status || "").toLowerCase() === "failed"
  )
}

function eventIcon(eventType: string) {
  switch (eventType) {
    case "ci_failed":
      return <AlertCircle className="size-3.5 shrink-0 text-destructive" aria-hidden="true" />
    case "ci_passed":
      return <CheckCircle2 className="size-3.5 shrink-0 text-emerald-500" aria-hidden="true" />
    case "ci_running":
      return <Clock className="size-3.5 shrink-0 text-amber-500" aria-hidden="true" />
    case "review_comments_arrived":
      return <MessageSquare className="size-3.5 shrink-0 text-amber-500" aria-hidden="true" />
    case "review_comments_resolved":
      return <CheckCircle2 className="size-3.5 shrink-0 text-emerald-500" aria-hidden="true" />
    case "run_started":
    case "run_finished":
      return <Play className="size-3.5 shrink-0 text-sky-500" aria-hidden="true" />
    default:
      return <Sparkles className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
  }
}

function formatEventTime(iso: string): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

export interface WorktreeFeedbackTimelineProps {
  worktreeKey: string
  entry?: WorktreeEntry
  onFixCi?: (failingChecks: string[]) => void
  onFixComments?: () => void
  onCleanup?: () => void
}

/**
 * Closed-loop feedback timeline for a single worktree:
 * - Failing normalized PR checks with inline "Fix CI" action
 * - Unresolved review threads with inline "Fix comments" action
 * - Ready-to-merge / merged banner with inline "Cleanup" action
 * - Chronological worktree_events ledger
 */
export function WorktreeFeedbackTimeline({
  worktreeKey,
  entry,
  onFixCi,
  onFixComments,
  onCleanup,
}: WorktreeFeedbackTimelineProps) {
  const { data, isLoading, isError } = useWorktreeTimeline(worktreeKey)
  const createRun = useCreateRun()
  const [expandedLogId, setExpandedLogId] = useState<number | null>(null)

  const checks: PrCheckRow[] = data?.checks ?? []
  const threads: PrReviewThreadRow[] = data?.threads ?? []
  const events: WorktreeEventRow[] = data?.events ?? []

  const failingChecks = checks.filter(isFailingCheck)
  const unresolvedThreads = threads.filter((t) => !t.resolved)
  const prState = (entry?.pr_detail?.state ?? "").toLowerCase()
  const isMerged = prState === "merged"
  const isReadyToMerge = entry?.kanban_column === "ready_to_merge"

  const handleFixCi = () => {
    const names = failingChecks.map((c) => c.name)
    if (onFixCi) {
      onFixCi(names)
      return
    }
    const prompt = names.length
      ? `Investigate and fix the failing CI checks: ${names.join(", ")}.`
      : "Investigate and fix the failing CI checks on this branch."
    createRun.mutate(
      { command: "adhoc", args: [worktreeKey, "--prompt", prompt], confirm: true },
      {
        onSuccess: () => toast.success("Started Fix CI run"),
        onError: (err) => toast.error(errorText(err)),
      },
    )
  }

  const handleFixComments = () => {
    if (onFixComments) {
      onFixComments()
      return
    }
    const target = entry ? prUrl(entry) || worktreeKey : worktreeKey
    createRun.mutate(
      { command: "review", args: [target, "--fix-comments"], confirm: true },
      {
        onSuccess: () => toast.success("Started Fix comments run"),
        onError: (err) => toast.error(errorText(err)),
      },
    )
  }

  const handleCleanup = () => {
    if (onCleanup) {
      onCleanup()
      return
    }
    createRun.mutate(
      { command: "cleanup", args: [worktreeKey, "--yes"], confirm: true },
      {
        onSuccess: () => toast.success("Started Cleanup run"),
        onError: (err) => toast.error(errorText(err)),
      },
    )
  }

  if (isLoading) {
    return (
      <div className="rounded-md border border-border/60 p-3 text-xs text-muted-foreground">
        Loading feedback timeline…
      </div>
    )
  }

  if (isError) {
    return (
      <div className="rounded-md border border-border/60 p-3 text-xs text-muted-foreground">
        Unable to load feedback timeline.
      </div>
    )
  }

  const hasContent =
    failingChecks.length > 0 ||
    unresolvedThreads.length > 0 ||
    events.length > 0 ||
    isReadyToMerge

  if (!hasContent) {
    return (
      <div className="rounded-md border border-border/60 p-3 text-xs text-muted-foreground">
        No CI checks, review threads, or timeline events recorded yet.
      </div>
    )
  }

  return (
    <section
      aria-label="Worktree feedback timeline"
      className="space-y-3 rounded-md border border-border/60 p-3"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Feedback Timeline
        </h3>
        <div className="flex flex-wrap items-center gap-1.5">
          {failingChecks.length > 0 ? (
            <Button
              type="button"
              size="sm"
              variant="destructive"
              className="h-6 px-2 text-xs"
              disabled={createRun.isPending}
              onClick={handleFixCi}
            >
              <Wrench className="mr-1 size-3" aria-hidden="true" />
              Fix CI
            </Button>
          ) : null}
          {unresolvedThreads.length > 0 ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-6 px-2 text-xs"
              disabled={createRun.isPending}
              onClick={handleFixComments}
            >
              <MessageSquare className="mr-1 size-3" aria-hidden="true" />
              Fix comments
            </Button>
          ) : null}
          {isMerged ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-6 px-2 text-xs"
              disabled={createRun.isPending}
              onClick={handleCleanup}
            >
              <Trash2 className="mr-1 size-3" aria-hidden="true" />
              Cleanup
            </Button>
          ) : null}
        </div>
      </div>

      {isReadyToMerge ? (
        <div className="flex items-center gap-2 rounded bg-emerald-500/10 px-2.5 py-1.5 text-xs text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="size-3.5 shrink-0" aria-hidden="true" />
          <span>{entry?.display_status || "Ready to merge"}</span>
        </div>
      ) : null}

      {failingChecks.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-xs font-medium text-destructive">
            Failing checks ({failingChecks.length})
          </div>
          <ul className="space-y-1">
            {failingChecks.map((chk) => (
              <li
                key={chk.id}
                className="rounded border border-destructive/30 bg-destructive/5 px-2 py-1.5 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono font-medium">{chk.name}</span>
                  <div className="flex items-center gap-1.5">
                    <Badge variant="destructive" className="h-4 px-1.5 text-[10px]">
                      {chk.conclusion || chk.status || "failed"}
                    </Badge>
                    {chk.url ? (
                      <a
                        href={chk.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-muted-foreground hover:text-foreground"
                        title={`Open ${chk.name}`}
                      >
                        <ExternalLink className="size-3" aria-hidden="true" />
                      </a>
                    ) : null}
                    {chk.log_tail ? (
                      <button
                        type="button"
                        className="text-[11px] underline text-muted-foreground hover:text-foreground"
                        onClick={() =>
                          setExpandedLogId((cur) => (cur === chk.id ? null : chk.id))
                        }
                      >
                        {expandedLogId === chk.id ? "Hide log" : "Log"}
                      </button>
                    ) : null}
                  </div>
                </div>
                {chk.log_tail && expandedLogId === chk.id ? (
                  <pre className="mt-1.5 max-h-32 overflow-auto rounded bg-muted p-1.5 font-mono text-[11px] whitespace-pre-wrap">
                    {chk.log_tail}
                  </pre>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {unresolvedThreads.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-xs font-medium text-amber-600 dark:text-amber-400">
            Unresolved review threads ({unresolvedThreads.length})
          </div>
          <ul className="space-y-1">
            {unresolvedThreads.map((th) => (
              <li
                key={th.id}
                className="rounded border border-border/60 bg-muted/30 px-2 py-1.5 text-xs"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">
                    {th.author || "reviewer"}
                    {th.file ? (
                      <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">
                        {th.file}
                        {th.line != null ? `:${th.line}` : ""}
                      </span>
                    ) : null}
                  </span>
                  {th.is_bot ? (
                    <Badge variant="secondary" className="h-4 px-1.5 text-[10px]">
                      bot
                    </Badge>
                  ) : null}
                </div>
                {th.body ? (
                  <p className="mt-1 line-clamp-2 text-muted-foreground">{th.body}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {events.length > 0 ? (
        <div className="space-y-1.5">
          <div className="text-xs font-medium text-muted-foreground">History</div>
          <ol className="space-y-1">
            {events.map((ev) => (
              <li key={ev.id} className="flex items-start gap-2 text-xs">
                <span className="mt-0.5">{eventIcon(ev.event_type)}</span>
                <span className="min-w-0 flex-1">{ev.summary || ev.event_type}</span>
                <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                  {formatEventTime(ev.created_at)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </section>
  )
}
