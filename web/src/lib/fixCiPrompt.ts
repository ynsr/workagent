import type { QueryClient } from "@tanstack/react-query"
import { api, type PrCheckRow, type WorktreeTimeline } from "@/lib/api"
import { queryKeys } from "@/lib/queries"

export const CI_LOG_TAIL_LINES = 50

const FAILING_CONCLUSIONS = new Set([
  "failure",
  "failed",
  "timed_out",
  "action_required",
  "startup_failure",
  "cancelled",
  "canceled",
])

/** Return true when a normalized PR check row represents a failure. */
export function isFailingCheck(chk: PrCheckRow): boolean {
  return (
    FAILING_CONCLUSIONS.has((chk.conclusion || "").toLowerCase()) ||
    (chk.status || "").toLowerCase() === "failed"
  )
}

/** Return the last `maxLines` lines of a CI log tail (trimming trailing empty lines). */
export function tailLogLines(log: string, maxLines = CI_LOG_TAIL_LINES): string {
  const trimmed = (log || "").replace(/\r\n/g, "\n").trimEnd()
  if (!trimmed) return ""
  const lines = trimmed.split("\n")
  return lines.slice(-maxLines).join("\n").trim()
}

/** Return a backtick fence strictly longer than any run of backticks in `text` (min 3). */
export function markdownFence(text: string): string {
  let longest = 0
  for (const m of text.matchAll(/`+/g)) {
    if (m[0].length > longest) longest = m[0].length
  }
  return "`".repeat(Math.max(3, longest + 1))
}

/**
 * Build the Fix CI prompt including failing check names, failing CI job URL(s),
 * and the last 50 lines of failing job terminal logs.
 */
export function buildFixCiPrompt(
  failingChecks: readonly PrCheckRow[],
  fallbackCiUrl?: string | null,
): string {
  const fallbackUrl = (fallbackCiUrl || "").trim()
  if (failingChecks.length === 0) {
    const base = "Investigate and fix the failing CI checks on this branch."
    return fallbackUrl ? `${base}\n\nFailing CI job URL: ${fallbackUrl}` : base
  }
  const names = failingChecks.map((c) => c.name).filter(Boolean)
  const header = names.length
    ? `Investigate and fix the failing CI checks: ${names.join(", ")}.`
    : "Investigate and fix the failing CI checks on this branch."
  const sections: string[] = [header, "", "Failing CI checks:"]
  for (const chk of failingChecks) {
    const status = chk.conclusion || chk.status || "failed"
    const url = (chk.url || fallbackUrl).trim()
    const tail = tailLogLines(chk.log_tail || "")
    sections.push(`- ${chk.name} (${status})${url ? ` — URL: ${url}` : ""}`)
    if (tail) {
      const fence = markdownFence(tail)
      sections.push(`Last ${CI_LOG_TAIL_LINES} lines of terminal log:`)
      sections.push(fence)
      sections.push(tail)
      sections.push(fence)
    }
  }
  return sections.join("\n")
}

/**
 * Resolve the Fix CI prompt for *worktreeKey*, fetching missing failing job logs
 * via `GET /api/worktrees/{ref}/timeline?fetch_logs=true` when needed (soft-fail).
 */
export async function resolveFixCiPrompt(
  worktreeKey: string,
  checks: readonly PrCheckRow[],
  options: {
    fallbackCiUrl?: string | null
    fetchIfEmpty?: boolean
    queryClient?: QueryClient
  } = {},
): Promise<string> {
  let failing = checks.filter(isFailingCheck)
  const fallbackCiUrl = options.fallbackCiUrl
  const needsLogs =
    (Boolean(options.fetchIfEmpty) && failing.length === 0) ||
    failing.some((c) => !c.log_tail?.trim() && Boolean(c.url || fallbackCiUrl))
  if (needsLogs) {
    try {
      const fresh: WorktreeTimeline = await api.worktreeTimeline(worktreeKey, 100, {
        fetchLogs: true,
      })
      if (options.queryClient && queryKeys?.worktreeTimeline) {
        options.queryClient.setQueryData(queryKeys.worktreeTimeline(worktreeKey), fresh)
      }
      const freshFailing = (fresh.checks ?? []).filter(isFailingCheck)
      if (freshFailing.length > 0) {
        failing = freshFailing
      }
    } catch {
      // Soft-fail: build the prompt from whatever checks/URLs are already available.
    }
  }
  return buildFixCiPrompt(failing, fallbackCiUrl)
}
