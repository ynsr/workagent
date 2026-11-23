import type { PrDetail, RunState } from "@/lib/api"
import { StatusGlyph, StatusLabel, type StatusTone } from "@/components/StatusGlyph"

const RUN_STATES: Record<RunState, { label: string; tone: StatusTone }> = {
  running: { label: "Running", tone: "working" },
  succeeded: { label: "Succeeded", tone: "done" },
  failed: { label: "Failed", tone: "failed" },
  needs_input: { label: "Needs input", tone: "needs-you" },
  cancelled: { label: "Cancelled", tone: "idle" },
}

export function RunStateBadge({ state, className }: { state: RunState; className?: string }) {
  const run = RUN_STATES[state]
  return (
    <StatusLabel tone={run.tone} className={className}>
      {run.label}
    </StatusLabel>
  )
}

const PR_TONES: Record<string, StatusTone> = { open: "in-review", merged: "merged", closed: "idle" }

/** PR/MR state: glyph + `#N state`; falls back to a muted glyph. */
export function PrBadge({ pr }: { pr: PrDetail | null | undefined }) {
  if (!pr) return null
  return (
    <StatusLabel tone={PR_TONES[pr.state] ?? "idle"} title={`${pr.title} — ${pr.author} (${pr.tool})`}>
      #{pr.number} {pr.state}
    </StatusLabel>
  )
}

const ISSUE_DONE_STATES: Record<string, true> = { done: true, released: true, resolved: true }
const ISSUE_PROGRESS_TONES: Record<string, StatusTone> = {
  "in progress": "working",
  review: "in-review",
  "code review": "in-review",
  "product test": "in-review",
}

/** Linked-issue status; renders nothing when the worktree has no linked issue or the status is unknown. */
export function IssueStatusBadge({ status }: { status?: string | null }) {
  const s = (status ?? "").trim().toLowerCase()
  if (!s) return null
  const tone: StatusTone = ISSUE_DONE_STATES[s] ? "done" : (ISSUE_PROGRESS_TONES[s] ?? "idle")
  return (
    <StatusLabel tone={tone} title={`Issue status: ${s}`}>
      {s}
    </StatusLabel>
  )
}


type CiState = "success" | "failure" | "running" | "not_started"

const CI_TONES: Record<CiState, StatusTone> = { success: "done", failure: "failed", running: "working", not_started: "idle" }

const CI_TOOLTIPS: Record<CiState, string> = {
  success: "CI: success",
  failure: "CI: failure",
  running: "CI: running",
  not_started: "No CI status",
}

/**
 * CI pipeline badge. Always a link: `ciUrl` (the latest CI job page from
 * the backend, e.g. GitLab `.../-/jobs/194493`) when known, else the
 * PR/MR checks page derived from `prUrl` as a fallback. Clicks stop
 * table-row toggling. Tooltip shows the status plus the link target, or
 * "No CI status" when the badge has no CI status at all.
 */
export function CiBadge({ ci, ciUrl, prUrl }: { ci?: string | null; ciUrl?: string | null; prUrl?: string | null }) {
  const state: CiState =
    ci === "success" || ci === "failure" || ci === "running" ? ci : "not_started"
  const href = ciUrl || checksUrl(prUrl) || undefined
  const tip = href ? `${CI_TOOLTIPS[state]} — ${href}` : CI_TOOLTIPS[state]
  if (!href) {
    return (
      <span title={tip} className="inline-flex">
        <span className="sr-only">{CI_TOOLTIPS[state]}</span>
        <StatusGlyph tone={CI_TONES[state]} />
      </span>
    )
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={tip}
      title={tip}
      onClick={(e) => e.stopPropagation()}
      className="inline-block"
    >
      <span className="sr-only">{CI_TOOLTIPS[state]}</span>
      <StatusGlyph tone={CI_TONES[state]} className="hover:opacity-70" />
    </a>
  )
}

/** CI checks page for a PR/MR URL (fallback when the backend job URL is
 * unknown): GitHub `…/pull/N/checks`, GitLab `…/-/pipelines` (handles both
 * `/-/merge_requests/N` and legacy `/merge_requests/N`). */
export function checksUrl(pr: string | null | undefined): string {
  if (!pr) return ""
  const gh = /^https:\/\/github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/.exec(pr)
  if (gh) return `https://github.com/${gh[1]}/pull/${gh[2]}/checks`
  const gl = /^(https:\/\/[^/]+\/.*?)(?:\/-)?\/merge_requests\/\d+/.exec(pr)
  if (gl) return `${gl[1]}/-/pipelines`
  return ""
}
