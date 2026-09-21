/**
 * Typed client for the `harness serve` API.
 * Contract: web/API_CONTRACT.md (authoritative).
 */

export interface Info {
  version: string
  host: string
  port: number
  network_exposed: boolean
}

export interface PrDetail {
  number: number
  state: string
  title: string
  author: string
  url: string
  tool: string
}

export interface CommitsDetail {
  behind: number
  ahead: number
}

/** One worktree entry, as in `harness status --json`. */
export interface WorktreeEntry {
  issue?: string
  worktree?: string
  branch?: string
  /** Live AI harness on the worktree, e.g. "omp 4242"; absent when none. */
  harness?: string
  repo?: string
  issue_url?: string
  commits?: string
  pr?: string
  /** CI pipeline status: success | failure | running | not_started; absent when unknown. */
  ci?: string
  /** First-seen stamp (ISO-8601); older entries may lack it. */
  added_at?: string
  /** False when the recorded path is missing or not a live git worktree. */
  wt_valid?: boolean
  commits_detail?: CommitsDetail | null
  pr_detail?: PrDetail | null
}

/** GET /api/status — map of worktree key → entry. */
export type WorktreeMap = Record<string, WorktreeEntry>

/** GET /api/status?ref=… — single-worktree detail (entry fields plus key/base_branch/create_hint). */
export interface WorktreeDetail extends WorktreeEntry {
  key: string
  commits: string
  pr: string
  commits_detail?: CommitsDetail | null
  pr_detail?: PrDetail | null
  base_branch?: string
  issue_url?: string
  create_hint?: string
}

export interface PathInfo {
  key: string
  worktree: string
}

export interface Repo {
  name: string
  path: string
  tracker?: string
  [key: string]: unknown
}

export interface Links {
  trackers: Record<string, { repos: string[] }>
  worktrees: WorktreeMap
}

/** One row of GET /api/issues. */
export interface IssueRow {
  key: string
  title: string
  url: string
  status: string
  created: string
}

export interface IssuesResponse {
  issues: IssueRow[]
  warning?: string | null
}

/** One unlinked open PR/MR of GET /api/candidates. */
export interface CandidatePr {
  key: string
  number: number
  title: string
  url: string
  branch: string
  updated: string
  state: string
  repo: string
}

export interface CandidateWorktree {
  path: string
  repo: string
  branch: string
  key_guess: string
}

export interface CandidatesResponse {
  prs: CandidatePr[]
  issues: IssueRow[]
  worktrees: CandidateWorktree[]
  warnings: string[]
}

export interface DoctorInfo {
  status: string
  live_hash?: string
  recorded_hash?: string
  tools: Record<string, boolean>
  receipt?: string
}

export type RunState =
  | "running"
  | "succeeded"
  | "failed"
  | "needs_input"
  | "cancelled"

export interface Run {
  id: string
  command: string
  args: string[]
  state: RunState
  exit_code: number | null
  truncated: boolean
  created: number
  target: string
  last_seq: number
}

export interface RunLine {
  seq: number
  text: string
}

export interface RunDetail extends Run {
  lines: RunLine[]
}

export type RunCommand =
  | "start"
  | "review"
  | "cleanup"
  | "sync"
  | "open"
  | "register"
  | "repo"
  | "link"

/** Display label for a PR/MR URL: "PR #33" (GitHub) or "MR #42" (GitLab);
 * falls back to the raw URL when the kind cannot be determined. */
export function prLabel(prUrl: string | undefined): string {
  if (!prUrl) return ""
  const m = /\/(pull|merge_requests)\/(\d+)/.exec(prUrl)
  return m ? `${m[1] === "pull" ? "PR" : "MR"} #${m[2]}` : prUrl
}

export interface CreateRunInput {
  command: RunCommand
  args: string[]
  confirm?: boolean
  force?: boolean
}

/** Error shape: `{"error": {"code": string, "message": string}}`. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.code = code
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch (cause) {
    throw new ApiError(
      0,
      "network_error",
      `Cannot reach the harness server (${String(cause)})`,
    )
  }
  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  if (!res.ok) {
    const err = (body as ErrorBody | null)?.error
    throw new ApiError(
      res.status,
      err?.code ?? "http_error",
      err?.message ?? `Request failed with HTTP ${res.status}`,
    )
  }
  return body as T
}

function qs(params: Record<string, string | undefined>): string {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value) sp.set(key, value)
  }
  const s = sp.toString()
  return s ? `?${s}` : ""
}

export const api = {
  info: () => request<Info>("/api/info"),

  /** GET /api/status — all worktrees. `refresh` re-queries PR status (slow). */
  statusAll: (opts?: { refresh?: boolean }) =>
    request<WorktreeMap>(
      `/api/status${qs({ refresh: opts?.refresh ? "true" : undefined })}`,
    ),

  /** GET /api/status?ref=… — single worktree detail. */
  statusDetail: (ref: string) =>
    request<WorktreeDetail>(`/api/status${qs({ ref })}`),

  /** GET /api/path?ref=… — resolved worktree path. */
  path: (ref: string) => request<PathInfo>(`/api/path${qs({ ref })}`),

  repos: () => request<Repo[]>("/api/repos"),
  links: () => request<Links>("/api/links"),
  doctor: () => request<DoctorInfo>("/api/doctor"),

  /** GET /api/issues — my open issues; always 200 with an optional warning. */
  issues: () => request<IssuesResponse>("/api/issues"),
  /** GET /api/candidates — unlinked PR/MRs + recent issues. */
  candidates: () => request<CandidatesResponse>("/api/candidates"),

  runs: () => request<Run[]>("/api/runs"),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),

  /** POST /api/runs — 202 `{run_id}`; 409 `{code:"conflict"}` on target collision. */
  createRun: (input: CreateRunInput) =>
    request<{ run_id: string }>("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),

  /** POST /api/runs/{id}/cancel — no body (no Content-Type). */
  cancelRun: (id: string) =>
    request<{ id: string; state: string }>(
      `/api/runs/${encodeURIComponent(id)}/cancel`,
      { method: "POST" },
    ),
}
