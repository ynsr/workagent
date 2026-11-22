/**
 * Typed client for the `workagent serve` API.
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

export type KanbanColumn = "working" | "needs_you" | "in_review" | "ready_to_merge"

/** One worktree entry, as in `workagent status --json`. */
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
  /** Latest CI job page (e.g. GitLab `.../-/jobs/194493`, GitHub check details URL); absent when unknown. */
  ci_url?: string
  /** Review stats display "R|U|R" (done|unresolved|resolved); "-" when no PR/lookup failed. */
  reviews?: string
  /** First-seen stamp (ISO-8601); older entries may lack it. */
  added_at?: string
  /** False when the recorded path is missing or not a live git worktree. */
  wt_valid?: boolean
  /** 0 = deactivated (`link deactivate`); absent/1 = active. */
  active?: number
  /** Soft-delete stamp (`link remove`); absent/"" = live. */
  deleted_at?: string
  /** Related-repo keys for a parent worktree (start --related-repo); absent/[] = none. */
  related_repos?: string[]
  /** Parent link key for a child worktree; absent/"" = top-level. */
  parent_key_ref?: string
  commits_detail?: CommitsDetail | null
  pr_detail?: PrDetail | null
  reviews_detail?: ReviewsDetail | null
  /** Lowercased tracker status of the linked issue (jira/gh); absent when no linked issue or unknown. */
  issue_status?: string | null
  /** Prodtest display status ("pending" | "pending_prodtest" | "running" | "passed" | "failed" | "blocked"); "-" when no plan. */
  prodtest?: string
  /** Stored prodtest item (status/risk/revision/last_run); null when no plan exists. */
  prodtest_detail?: ProdtestDetail | null
  /** Derived delivery stage lane: working | needs_you | in_review | ready_to_merge. */
  kanban_column?: KanbanColumn
  /** Human-readable derived delivery status phrase. */
  display_status?: string
  /** Machine-readable attention reason when kanban_column === "needs_you", else null. */
  attention_reason?: string | null
  /** Names of currently failing normalized PR checks. */
  failing_checks?: string[]
  /** Count of unresolved review threads. */
  unresolved_threads?: number
}

/** One normalized PR check row (GET /api/worktrees/{ref}/timeline). */
export interface PrCheckRow {
  id: number
  worktree_ref: string
  name: string
  commit_sha: string
  status: string
  conclusion: string
  url: string
  log_tail: string
  updated_at: string
}

/** One normalized PR review thread row (GET /api/worktrees/{ref}/timeline). */
export interface PrReviewThreadRow {
  id: number
  worktree_ref: string
  thread_id: string
  author: string
  file: string
  line: number | null
  body: string
  resolved: boolean
  is_bot: boolean
  updated_at: string
}

/** One chronological feedback event (GET /api/worktrees/{ref}/timeline). */
export interface WorktreeEventRow {
  id: number
  worktree_ref: string
  event_type: string
  summary: string
  details: Record<string, unknown>
  created_at: string
}

/** GET /api/worktrees/{ref}/timeline response payload. */
export interface WorktreeTimeline {
  ref: string
  checks: PrCheckRow[]
  threads: PrReviewThreadRow[]
  events: WorktreeEventRow[]
  summary: {
    failing_checks: string[]
    unresolved_threads: number
  }
}

/** Prodtest row cell: stored plan/run state for one issue. */
export interface ProdtestDetail {
  issue_key: string
  plan_path?: string
  plan_revision?: number
  plan_risk?: string
  plan_diff_hash?: string
  release_sha?: string
  released_at?: string
  status?: string
  last_run?: string
  last_run_id?: string
  updated_at?: string
}
/** One prodtest item row (GET /api/prodtests). Worktree decoupled from dev flow. */
export interface ProdtestRow {
  issue_key: string
  status: string
  plan_path?: string
  plan_revision?: number
  plan_risk?: string
  plan_diff_hash?: string
  release_sha?: string
  released_at?: string
  last_run_id?: string
  worktree_ref?: string
  run_results?: string
  updated_at?: string
}

export type DiffMode = "base" | "head"

export interface WorktreeDiffFile {
  path: string
  status: string
  additions: number
  deletions: number
  binary: boolean
  old_path?: string | null
}

export interface WorktreeFilesResponse {
  key: string
  mode: DiffMode
  base_ref: string
  files: WorktreeDiffFile[]
  total_additions: number
  total_deletions: number
}

export interface DiffLine {
  type: "context" | "add" | "del" | "meta"
  old_line: number | null
  new_line: number | null
  content: string
}

export interface DiffHunk {
  header: string
  old_start: number
  old_count: number
  new_start: number
  new_count: number
  lines: DiffLine[]
}

export interface WorktreeDiffResponse {
  key: string
  path: string
  mode: DiffMode
  base_ref: string
  binary: boolean
  truncated: boolean
  additions: number
  deletions: number
  hunks: DiffHunk[]
}

export interface ProdtestPlanCase {
  id?: string
  title?: string
  kind?: string
  priority?: string
  safety?: string
  severity?: string
  tool?: string
  description?: string
  steps?: unknown[]
  expect?: unknown[]
  [key: string]: unknown
}

export interface ProdtestPlanPrecondition {
  id?: string
  title?: string
  description?: string
  [key: string]: unknown
}

export interface ProdtestPlanResponse {
  issue_key: string
  status: string
  plan_path: string
  plan_revision?: number | null
  plan_risk: string
  plan_diff_hash: string
  release_sha: string
  last_run_id: string
  updated_at: string
  truncated: boolean
  yaml: string
  parse_error: string | null
  summary: string
  preconditions: ProdtestPlanPrecondition[]
  cases: ProdtestPlanCase[]
  metadata: Record<string, unknown>
}

/** Review-comment stats: done reviews + thread resolution. */
export interface ReviewsDetail {
  reviews: number
  unresolved: number
  resolved: number
}

/** GET /api/status — map of worktree key → entry. */
export type WorktreeMap = Record<string, WorktreeEntry>

/** GET /api/status?ref=… — single-worktree detail (entry fields plus key/base_branch/create_hint). */
export interface WorktreeDetail extends WorktreeEntry {
  key: string
  commits: string
  pr: string
  base_branch?: string
  create_hint?: string
  prodtest?: string
  prodtest_detail?: ProdtestDetail | null
}

export interface PathInfo {
  key: string
  worktree: string
}

export interface Repo {
  name: string
  path: string
  tracker?: string
  trackers?: string[]
  default_branch?: string
  release_branch?: string
  commands?: { "pre-release"?: string | null }
  /** Per-repo StatusTable overlay visibility: {button_id: bool} (missing = all visible). */
  overlay_buttons?: Record<string, boolean>
  /** Opt-in background watcher: auto-fix failing PR CI checks. */
  auto_fix_ci?: boolean
  /** Opt-in background watcher: auto-fix unresolved PR review comments. */
  auto_fix_reviews?: boolean
}

/** GET/POST /api/worktrees/{ref}/watcher response payload. */
export interface WorktreeWatcherSettings {
  key: string
  auto_fix_ci: boolean | null
  auto_fix_reviews: boolean | null
  effective_auto_fix_ci: boolean
  effective_auto_fix_reviews: boolean
  last_nudge_signature: string
}

export interface TrackerRow {
  key: string
  vendor: string
  remote_url: string
  repos: number
  /** 1/0 from the DB; absent on old payloads (treat as enabled). */
  fetch_enabled?: number | boolean
  is_enabled?: number | boolean
}

export interface ActionRow {
  id: number
  label: string
  name: string
  icon: string
  prompt: string
  pre_execution: string
  post_execution: string
  default_options: Record<string, unknown>
  protected?: boolean
  sort_order?: number
  dedicated_top_bar?: boolean
  dedicated_overlay?: boolean
  category?: string
  run_immediately?: boolean
  created_at: string
  modified_at: string
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
  /** Launch default repo for this ref (candidates endpoint only). */
  repo_hint?: string
  /** Reporter login (jira) / author login (github); absent on old caches. */
  reporter?: string
  author?: string
  /** Assignee login (jira) / assignee logins (github); absent on old caches. */
  assignee?: string
  assignees?: string[]
  /** Query origin: True when the row matched the assignee-me / reporter-me
   * search. Absent on old caches (treated as me-scoped either way). */
  assigned_to_me?: boolean
  created_by_me?: boolean
  /** Issue body/description for client-side search; absent on old caches. */
  description?: string
}
export interface IssuesResponse {
  issues: IssueRow[]
  warning?: string | null
}

/** Releasability of GET /api/release/status. */
export interface ReleaseStatus {
  repo: string
  path: string
  default_branch: string
  release_branch: string
  diff_count: number
  behind?: number
  releasable: boolean
  reason: string
  stale?: boolean
  releasing?: boolean
  ci?: string
  ci_url?: string
  fetched_at?: string | null
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
  /** Registered repo display name (backend WS2 Task 5); absent until then. */
  repo_name?: string
  /** PR/MR author login; "" or absent when unknown (old glab). */
  author?: string
}

export interface CandidateWorktree {
  path: string
  repo: string
  branch: string
  key_guess: string
  /** Newest mtime under the worktree dir (ISO-8601); "" when unknown. */
  mtime?: string
}

export interface CandidatesPrsResponse {
  prs: CandidatePr[]
  warnings: string[]
  /** True when rows came from the expired cache while a background
   * refresh runs — the tab keeps showing them with the overlay badge,
   * then re-fetches once for the fresh rows. */
  stale?: boolean
}

export interface CandidatesIssuesResponse {
  issues: IssueRow[]
  warnings: string[]
}

export interface CandidatesWorktreesResponse {
  worktrees: CandidateWorktree[]
  warnings: string[]
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

export interface HarnessInfo {
  name: string
  label: string
  binary: string
  available: boolean
}

export interface ConfigInfo {
  default_harness: string
  supported_harnesses: string[]
  harnesses: HarnessInfo[]
}
/** One model/effort option from GET /api/harnesses/{name}/options. */
export interface HarnessOption {
  id: string
  name: string
}

/** GET /api/harnesses/{name}/options (issue #112): ACP-discovered model/effort
 * options. `supported` false (or empty lists) ⇒ the UI disables that input. */
export interface HarnessOptions {
  harness: string
  supported: boolean
  models: HarnessOption[]
  efforts: HarnessOption[]
  current_model: string
  current_effort: string
  warning?: string
  cached?: boolean
}

/** Pending ACP question on a run (from ACP:{"t":"question",...} in the log). */
export interface PendingQuestion {
  qid: string
  text: string
  options: string[]
  multi: boolean
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
  /** Stored transcript path (`--session-file`); may be a scope hint only —
   * see `transcript_ready`. */
  session_file: string
  /** Backend readiness: `session_file` names a non-empty .jsonl on disk, so
   * `omp --resume` on it works. Copy is gated on this (preview rows store a
   * `--session-dir` hint that never existed). */
  transcript_ready: boolean
  /** Stored transcript kind (start/review/sync/fix_comments/custom_action/
   * flow/prodtest_plan/prodtest_run); null for rows recorded before the type
   * existed. The Runs label prefers it over the command. */
  session_type: string | null
  worktree: string
  /** AI harness name that produced this run (e.g. "agy" | "omp"). */
  harness_name?: string
  /** Shell resume command computed by the server when transcript is ready. */
  resume_command?: string
  last_seq: number
  /** Parent run ID (`db-<id>` or live run id) when spawned by a parent run (flow / review --all / --then-fix-comments). */
  parent_run_id?: string | null
  /** 0-based step index within the parent run (null when unindexed). */
  step_index?: number | null
  /** Newest unanswered ACP question (null when none) — drives QuestionCard. */
  pending_question?: PendingQuestion | null
  /** Epoch seconds when the run finished (null while running). */
  finished?: number | null
  /** Elapsed run duration in milliseconds (null while running). */
  duration_ms?: number | null
  /** Model identifier used for the run ("" when unset). */
  model_id?: string
  /** Reasoning effort level used for the run ("" when unset). */
  effort?: string
  /** Aggregated input tokens across turns (null when unreported). */
  input_tokens?: number | null
  /** Aggregated output tokens across turns (null when unreported). */
  output_tokens?: number | null
  /** Aggregated cached/read tokens across turns (null when unreported). */
  cached_tokens?: number | null
  /** Aggregated USD cost across turns (null when unreported). */
  cost_usd?: number | null
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
  | "release"
  | "sync"
  | "open"
  | "register"
  | "repo"
  | "link"
  | "tracker"
  | "action"
  | "message"
  | "flow"
  | "prodtest"
  | "backup"
  | "adhoc"
  | "config"
export interface MessageRow { id: number; message_id: string; thread_id: string; app_name: string; sender_username: string; content: string; ts: string; repos: string[]; related_repos?: string[]; worktrees?: string[]; added_at: string }
export type FlowExecutionMode = "sequential" | "parallel"
export interface FlowStep { kind: string; action_label?: string; execution_mode?: FlowExecutionMode; config?: Record<string, unknown> }
export interface FlowTemplateRow { id: number; label: string; name: string; icon?: string; steps: FlowStep[]; pre_execution: string; post_execution: string; parent_labels?: string[]; protected?: boolean; sort_order?: number; dedicated_top_bar?: boolean; dedicated_overlay?: boolean; category?: string; run_immediately?: boolean; created_at: string; modified_at: string }
export interface FlowStepResult { kind: string; status: string; detail?: string }
export interface FlowRow { id: number; template_label: string; worktree_ref: string; repo_snapshot: { repo?: string; launch_ref?: string; default_branch?: string; release_branch?: string }; status: string; current_step: number; step_results: FlowStepResult[]; effective_steps?: FlowStep[] | null; created_at: string; modified_at: string }

/** Display label for a PR/MR URL: "PR #33" (GitHub) or "MR #42" (GitLab);
 * falls back to the raw URL when the kind cannot be determined. */
export function prLabel(prUrl: string | undefined): string {
  if (!prUrl) return ""
  const m = /\/(pull|merge_requests)\/(\d+)/.exec(prUrl)
  return m ? `${m[1] === "pull" ? "PR" : "MR"} #${m[2]}` : prUrl
}

/** Absolute PR/MR URL for an entry: pr_detail.url first (backend `pr` is a
 * display label like "MR #1695 (open)", not a URL). Recorded-URL entries
 * carry the raw URL in pr_detail.url too. */
export function prUrl(entry: Pick<WorktreeEntry, "pr" | "pr_detail">): string {
  const u = entry.pr_detail?.url ?? ""
  if (u.startsWith("http")) return u
  const raw = entry.pr ?? ""
  return raw.startsWith("http") ? raw : ""
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
      `Cannot reach the workagent server (${String(cause)})`,
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
  config: () => request<ConfigInfo>("/api/config"),

  /** GET /api/status — all worktrees. `refresh` re-queries PR status (slow). */
  statusAll: (opts?: { refresh?: boolean; includeInactive?: boolean; includeDeleted?: boolean }) =>
    request<WorktreeMap>(
      `/api/status${qs({ refresh: opts?.refresh ? "true" : undefined, include_inactive: opts?.includeInactive ? "true" : undefined, include_deleted: opts?.includeDeleted ? "true" : undefined })}`,
    ),

  /** GET /api/status?ref=… — single worktree detail. */
  statusDetail: (ref: string) =>
    request<WorktreeDetail>(`/api/status${qs({ ref })}`),

  /** GET /api/worktrees/{ref}/timeline — normalized PR checks, review threads, and events. */
  worktreeTimeline: (ref: string, limit?: number) =>
    request<WorktreeTimeline>(
      `/api/worktrees/${encodeURIComponent(ref)}/timeline${qs({ limit: limit != null ? String(limit) : undefined })}`,
    ),

  /** GET /api/worktrees/{ref}/watcher — per-worktree watcher overrides & effective state. */
  worktreeWatcher: (ref: string) =>
    request<WorktreeWatcherSettings>(
      `/api/worktrees/${encodeURIComponent(ref)}/watcher`,
    ),

  /** POST /api/worktrees/{ref}/watcher — set/clear per-worktree watcher overrides. */
  setWorktreeWatcher: (
    ref: string,
    body: { auto_fix_ci?: boolean | null; auto_fix_reviews?: boolean | null },
  ) =>
    request<WorktreeWatcherSettings>(
      `/api/worktrees/${encodeURIComponent(ref)}/watcher`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  /** GET /api/path?ref=… — resolved worktree path. */
  path: (ref: string) => request<PathInfo>(`/api/path${qs({ ref })}`),

  repos: () => request<Repo[]>("/api/repos"),
  trackers: () => request<{ trackers: TrackerRow[] }>("/api/trackers"),
  /** GET /api/actions — custom actions (Actions page source). */
  actions: () => request<{ actions: ActionRow[] }>("/api/actions"),
  /** GET /api/actions/{label} — one custom action; rejects on 404. */
  actionDetail: (label: string) =>
    request<ActionRow>(`/api/actions/${encodeURIComponent(label)}`),
  /** GET /api/flow-templates — flow template definitions (Flow Templates page source). */
  flowTemplates: () => request<{ templates: FlowTemplateRow[] }>("/api/flow-templates"),
  /** GET /api/flow-templates/{label} — one flow template; rejects on 404. */
  flowTemplateDetail: (label: string) =>
    request<FlowTemplateRow>(`/api/flow-templates/${encodeURIComponent(label)}`),
  /** GET /api/flows?worktree= — flow runs, optionally scoped to one worktree. */
  flows: (worktree?: string) =>
    request<{ flows: FlowRow[] }>(`/api/flows${qs({ worktree })}`),
  /** GET /api/messages — registered chat messages (Messages page source). */
  messages: (thread?: string) =>
    request<{ messages: MessageRow[] }>(`/api/messages${qs({ thread })}`),
  /** GET /api/prodtests — all prodtest items, newest first (Prodtests page source). */
  prodtests: () =>
    request<{ prodtests: ProdtestRow[] }>("/api/prodtests"),
  /** GET /api/prodtests/{ref} — one prodtest item; rejects on 404. */
  prodtestDetail: (ref: string) =>
    request<ProdtestRow>(`/api/prodtests/${encodeURIComponent(ref)}`),
   /** GET /api/messages/{ref} — one registered message; rejects on 404. */
   messageDetail: (ref: string) =>
     request<MessageRow>(`/api/messages/${encodeURIComponent(ref)}`),
  trackerDomains: () => request<{ disabled_domains: string[] }>("/api/tracker-domains"),
  setTrackerDomains: (domains: string[]) =>
    request<{ disabled_domains: string[] }>("/api/tracker-domains", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domains }),
    }),
  links: () => request<Links>("/api/links"),
  doctor: () => request<DoctorInfo>("/api/doctor"),

  /** GET /api/issues — issues for the user's (window, scope) selection;
   * always 200 with an optional warning. `force` bypasses the variant
   * server cache and re-queries the tracker CLIs live. */
  issues: (opts?: { force?: boolean; days?: number; mine?: boolean }) =>
    request<IssuesResponse>(
      `/api/issues${qs({ force: opts?.force ? "true" : undefined, days: opts?.days === undefined ? undefined : String(opts.days), mine: opts?.mine === false ? "false" : undefined })}`,
    ),
  /** GET /api/candidates — unlinked PR/MRs + issues for the selection.
   * `force` re-queries the tracker CLIs live instead of the variant cache. */
  candidates: (opts?: { force?: boolean; days?: number; mine?: boolean }) =>
    request<CandidatesResponse>(
      `/api/candidates${qs({ force: opts?.force ? "true" : undefined, days: opts?.days === undefined ? undefined : String(opts.days), mine: opts?.mine === false ? "false" : undefined })}`,
    ),
  /** GET /api/candidates/prs — unlinked PR/MRs only (slowest source; loads independently). */
  candidatesPrs: () => request<CandidatesPrsResponse>("/api/candidates/prs"),
  /** GET /api/release/status?name= — releasability for one repo (SWR). */
  releaseStatus: (name: string) =>
    request<ReleaseStatus>(`/api/release/status${qs({ name })}`),
  /** GET /api/candidates/issues — issues only (`force` bypasses the variant cache). */
  candidatesIssues: (opts?: { force?: boolean; days?: number; mine?: boolean }) =>
    request<CandidatesIssuesResponse>(
      `/api/candidates/issues${qs({ force: opts?.force ? "true" : undefined, days: opts?.days === undefined ? undefined : String(opts.days), mine: opts?.mine === false ? "false" : undefined })}`,
    ),
  /** GET /api/candidates/worktrees — unregistered on-disk worktrees only. */
  candidatesWorktrees: () => request<CandidatesWorktreesResponse>("/api/candidates/worktrees"),
  /** GET /api/default-repo?ref=… — linked-worktree repo else single-linked (issue #26). `repos` lists every linked repo of the ref's tracker for the picker. */
  defaultRepo: (ref: string) =>
    request<{ ref: string; repo: string; repos: string[] }>(`/api/default-repo${qs({ ref })}`),

  /** GET /api/review-session?ref=... - latest non-running review run (fix-continue target). */
  reviewSession: (ref: string) =>
    request<{ ref: string; run_id: number | null; file_path: string | null }>(`/api/review-session${qs({ ref })}`),

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
  /** GET /api/harnesses/{name}/options — ACP-discovered model/effort options. */
  harnessOptions: (name: string) =>
    request<HarnessOptions>(`/api/harnesses/${encodeURIComponent(name)}/options`),

  /** POST /api/runs/{id}/input — answer a pending ACP question. */
  submitRunInput: (id: string, body: { text: string; qid?: string }) =>
    request<{ id: string; ok: boolean }>(
      `/api/runs/${encodeURIComponent(id)}/input`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    ),

  /** POST /api/runs/{id}/follow-up — spawn a linked continuation run on the same worktree/session. */
  followUpRun: (
    id: string,
    body: { prompt: string; harness?: string; run_mode?: string; model?: string; effort?: string },
  ) =>
    request<{ run_id: string; continued_from_run_id: number | null; worktree_ref: string }>(
      `/api/runs/${encodeURIComponent(id)}/follow-up`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    ),

  /** POST /api/runs/{id}/resume — open OS terminal resumed on the run's transcript. */
  resumeRun: (id: string) =>
    request<{ id: string; session_file: string; worktree: string }>(
      `/api/runs/${encodeURIComponent(id)}/resume`,
      { method: "POST" },
    ),

  /** POST /api/runs/{id}/start-terminal — open OS terminal running the run's harness command. */
  startTerminal: (id: string) =>
    request<{ id: string }>(
      `/api/runs/${encodeURIComponent(id)}/start-terminal`,
      { method: "POST" },
    ),

  /** POST /api/terminal/stop — SIGTERM→SIGKILL the terminal agent on a worktree. */
  stopTerminal: (worktreeRef: string) =>
    request<{ worktree_ref: string; stopped: boolean }>(
      "/api/terminal/stop",
      { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ worktree_ref: worktreeRef }) },
    ),

  /** GET /api/worktrees/{ref}/files?mode=base|head — changed files in a worktree. */
  worktreeFiles: (ref: string, mode: DiffMode = "base") =>
    request<WorktreeFilesResponse>(
      `/api/worktrees/${encodeURIComponent(ref)}/files${qs({ mode })}`,
    ),

  /** GET /api/worktrees/{ref}/diff?path=<file>&mode=base|head — unified diff hunks. */
  worktreeDiff: (ref: string, path: string, mode: DiffMode = "base") =>
    request<WorktreeDiffResponse>(
      `/api/worktrees/${encodeURIComponent(ref)}/diff${qs({ path, mode })}`,
    ),

  /** GET /api/prodtests/{issue_key}/plan — validated YAML plan content + metadata. */
  prodtestPlan: (issueKey: string) =>
    request<ProdtestPlanResponse>(
      `/api/prodtests/${encodeURIComponent(issueKey)}/plan`,
    ),

  /** GET /api/schedules — all schedules plus built-in starter templates. */
  schedules: () => request<SchedulesResponse>("/api/schedules"),

  /** POST /api/schedules/preview — preview next N run timestamps. */
  previewSchedule: (cron_expr: string, timezone = "UTC", count = 5) =>
    request<SchedulePreviewResponse>("/api/schedules/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cron_expr, timezone, count }),
    }),

  /** POST /api/schedules — create a new schedule. */
  createSchedule: (body: CreateScheduleInput) =>
    request<ScheduleRecord>("/api/schedules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** PATCH /api/schedules/{id} — update an existing schedule. */
  updateSchedule: (id: number | string, body: UpdateScheduleInput) =>
    request<ScheduleRecord>(`/api/schedules/${encodeURIComponent(String(id))}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  /** DELETE /api/schedules/{id} — delete a schedule. */
  deleteSchedule: (id: number | string) =>
    request<{ removed: boolean; id: string }>(`/api/schedules/${encodeURIComponent(String(id))}`, {
      method: "DELETE",
    }),

  /** POST /api/schedules/{id}/run — trigger a schedule immediately. */
  runScheduleNow: (id: number | string, body?: { confirm?: boolean }) =>
    request<{ schedule: ScheduleRecord; argv: string[]; run_id: number | null }>(
      `/api/schedules/${encodeURIComponent(String(id))}/run`,
      body === undefined
        ? { method: "POST" }
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
    ),
}

export type ScheduleTargetKind = "flow" | "action" | "command"

export interface ScheduleRecord {
  id: number
  name: string
  cron_expr: string
  timezone: string
  target_kind: ScheduleTargetKind
  target_label: string
  args_json: string
  args: Record<string, unknown> | unknown[]
  enabled: boolean
  last_run_at: string
  next_run_at: string
  last_run_id: number | null
  lease_until: string
  created_at: string
  modified_at: string
}

export interface ScheduleStarterTemplate {
  id: string
  name: string
  description: string
  cron_expr: string
  timezone: string
  target_kind: ScheduleTargetKind
  target_label: string
  args_json: string
}

export interface SchedulesResponse {
  schedules: ScheduleRecord[]
  starters: ScheduleStarterTemplate[]
}

export interface SchedulePreviewResponse {
  cron_expr: string
  timezone: string
  next_runs: string[]
}

export interface CreateScheduleInput {
  name: string
  cron_expr: string
  timezone: string
  target_kind: ScheduleTargetKind
  target_label: string
  args_json?: string
  enabled?: boolean
}

export type UpdateScheduleInput = Partial<CreateScheduleInput>
