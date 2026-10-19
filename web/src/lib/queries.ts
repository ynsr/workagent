import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryClient } from "@tanstack/react-query"
import { api, type CreateRunInput } from "./api"
import { getVerbose } from "./settings"

export const queryKeys = {
  info: ["info"] as const,
  statusAll: ["status", "all"] as const,
  statusInactive: ["status", "inactive"] as const,
  statusDeleted: ["status", "deleted"] as const,
  path: (ref: string) => ["path", ref] as const,
  repos: ["repos"] as const,
  trackers: ["trackers"] as const,
  trackerDomains: ["trackers", "domains"] as const,
  actions: ["actions"] as const,
  action: (label: string) => ["actions", "detail", label] as const,
  flowTemplates: ["flow-templates"] as const,
  flowTemplate: (label: string) => ["flow-templates", "detail", label] as const,
  flows: (worktree?: string) => ["flows", worktree ?? ""] as const,
  links: ["links"] as const,
  doctor: ["doctor"] as const,
  runs: ["runs"] as const,
  run: (id: string) => ["runs", "detail", id] as const,
  candidates: (days?: number, mine?: boolean) => ["candidates", days ?? 90, mine ?? true] as const,
  candidatesPrs: ["candidates", "prs"] as const,
  issues: (days?: number, mine?: boolean) => ["issues", days ?? 90, mine ?? true] as const,
  candidatesIssues: (force?: boolean, days?: number, mine?: boolean) =>
    ["candidates", "issues", force ? "force" : "cached", days ?? 90, mine ?? true] as const,
  candidatesWorktrees: ["candidates", "worktrees"] as const,
  sessions: ["sessions"] as const,
  session: (id: string) => ["sessions", "detail", id] as const,
  release: (name: string) => ["release", name] as const,
}

/** Stale-while-revalidate: cached rows render instantly; a background
 * refetch refreshes them (overlay badge) — max(cache max-age * 10, 1 day). */
const SWR_STATUS_MS = 30 * 24 * 3600 * 1000   // PR cache: 3d * 10
const SWR_DAY_MS = 24 * 3600 * 1000           // issue/CI cache: 1h/10min * 10 → floor 1d
const RELEASE_SWR_MS = 3600 * 1000            // release diff: 1h fresh
/** Poll /api/status every 15 s while the tab is visible (contract). */
export function useStatusAll(refresh?: boolean) {
  return useQuery({
    queryKey: [...queryKeys.statusAll, refresh ?? false],
    queryFn: () => api.statusAll(refresh ? { refresh: true } : undefined),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: SWR_STATUS_MS,
    // Cache retention must match the SWR window: the default gcTime (5min)
    // garbage-collects rows after an idle tab, so an expired-but-usable
    // backend cache still blocks on skeleton instead of rendering stale
    // rows + overlay. Keep them for the full 30d SWR window.
    gcTime: SWR_STATUS_MS,
  })
}

/** Deactivated worktrees (active-only /api/status hides them). Same 15 s poll. */
export function useStatusInactive() {
  return useQuery({
    queryKey: queryKeys.statusInactive,
    queryFn: () => api.statusAll({ includeInactive: true }),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: SWR_STATUS_MS,
    gcTime: SWR_STATUS_MS,
  })
}

/** Soft-deleted worktrees (default /api/status hides them). Same 15 s poll. */
export function useStatusDeleted() {
  return useQuery({
    queryKey: queryKeys.statusDeleted,
    queryFn: () => api.statusAll({ includeInactive: true, includeDeleted: true }),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    staleTime: SWR_STATUS_MS,
    gcTime: SWR_STATUS_MS,
  })
}

export function useInfo() {
  return useQuery({ queryKey: queryKeys.info, queryFn: api.info, staleTime: 60_000 })
}
export function useRepos() {
  return useQuery({ queryKey: queryKeys.repos, queryFn: api.repos })
}

export function useTrackers() {
  return useQuery({ queryKey: queryKeys.trackers, queryFn: api.trackers })
}
export function useActions() {
  return useQuery({ queryKey: queryKeys.actions, queryFn: api.actions })
}

export function useAction(label: string | null) {
  return useQuery({
    queryKey: queryKeys.action(label ?? ""),
    queryFn: () => api.actionDetail(label ?? ""),
    enabled: !!label,
  })
}

export function useFlowTemplates() {
  return useQuery({ queryKey: queryKeys.flowTemplates, queryFn: api.flowTemplates })
}

export function useFlowTemplate(label: string | null) {
  return useQuery({
    queryKey: queryKeys.flowTemplate(label ?? ""),
    queryFn: () => api.flowTemplateDetail(label ?? ""),
    enabled: !!label,
  })
}

/** Flow runs, optionally scoped to one worktree (SWR: cached rows render
 * instantly, background refetch only swaps in fresh data — never blank). */
export function useFlows(worktree?: string) {
  return useQuery({
    queryKey: queryKeys.flows(worktree),
    queryFn: () => api.flows(worktree || undefined),
    staleTime: SWR_DAY_MS,
    gcTime: SWR_DAY_MS,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })
}

export function useTrackerDomains() {
  return useQuery({ queryKey: queryKeys.trackerDomains, queryFn: api.trackerDomains })
}

export function useLinks() {
  return useQuery({ queryKey: queryKeys.links, queryFn: api.links })
}

export function useDoctor() {
  return useQuery({ queryKey: queryKeys.doctor, queryFn: api.doctor })
}

export function useIssues(days?: number, mine?: boolean) {
  return useQuery({ queryKey: queryKeys.issues(days, mine), queryFn: () => api.issues({ days, mine }), staleTime: SWR_DAY_MS, gcTime: SWR_DAY_MS })
}

export function useCandidates(days?: number, mine?: boolean) {
  return useQuery({ queryKey: queryKeys.candidates(days, mine), queryFn: () => api.candidates({ days, mine }), staleTime: SWR_DAY_MS, gcTime: SWR_DAY_MS })
}

/** Per-tab candidates queries: each tab loads independently so the slow
 * PR fetch never blocks issues/worktrees (incremental tab rendering).
 * gcTime matches the SWR window: without it the default 5min gc drops
 * rows after an idle tab, and the Unlinked PR/MRs tab blocks on skeleton
 * instead of showing stale rows + overlay. */
export function useCandidatesPrs() {
  return useQuery({ queryKey: queryKeys.candidatesPrs, queryFn: () => api.candidatesPrs(), staleTime: SWR_DAY_MS, gcTime: SWR_DAY_MS })
}

export function useCandidatesIssues(
  force?: boolean,
  days: number = 90,
  mine: boolean = true,
  enabled: boolean = true,
) {
  return useQuery({
    queryKey: queryKeys.candidatesIssues(force, days, mine),
    queryFn: () => api.candidatesIssues({ force, days, mine }),
    staleTime: SWR_DAY_MS,
    // Same retention as useCandidatesPrs: stale issue rows render with the
    // overlay instead of blocking on skeleton after an idle tab.
    gcTime: SWR_DAY_MS,
    enabled,
  })
}

export function useCandidatesWorktrees() {
  return useQuery({ queryKey: queryKeys.candidatesWorktrees, queryFn: () => api.candidatesWorktrees(), staleTime: SWR_DAY_MS })
}

/** Release releasability for one repo (1h SWR; retention matches).
 * Polls every 30 s like StatusTable: cached rows render instantly, the
 * background refetch only swaps in fresh data (no skeleton/block). */
const RELEASE_POLL_MS = 30_000
export function useReleaseStatus(name: string) {
  return useQuery({
    queryKey: queryKeys.release(name),
    queryFn: () => api.releaseStatus(name),
    staleTime: RELEASE_SWR_MS,
    gcTime: RELEASE_SWR_MS,
    enabled: !!name,
    refetchInterval: RELEASE_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  })
}

/** Release releasability for several repos (one query each; same 30 s poll). */
export function useReleaseStatuses(names: string[]) {
  return useQueries({
    queries: names.map((name) => ({
      queryKey: queryKeys.release(name),
      queryFn: () => api.releaseStatus(name),
      staleTime: RELEASE_SWR_MS,
      gcTime: RELEASE_SWR_MS,
      refetchInterval: RELEASE_POLL_MS,
      refetchIntervalInBackground: false,
      refetchOnWindowFocus: true,
    })),
  })
}

export function useSessions() {
  return useQuery({ queryKey: queryKeys.sessions, queryFn: api.sessions })
}
export function useSession(id: string) {
  return useQuery({ queryKey: queryKeys.session(id), queryFn: () => api.session(id) })
}

export function useRuns() {
  return useQuery({
    queryKey: queryKeys.runs,
    queryFn: api.runs,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  })
}

export function useRun(id: string) {
  return useQuery({ queryKey: queryKeys.run(id), queryFn: () => api.run(id) })
}

export function usePath(ref: string) {
  return useQuery({
    queryKey: queryKeys.path(ref),
    queryFn: () => api.path(ref),
    enabled: ref.length > 0,
    staleTime: 30_000,
    retry: false,
  })
}

/**
 * Create a run. `-v` is passed as args[0] when the Settings toggle is on
 * (before the subcommand args, which start at index 0 here).
 */
export function useCreateRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateRunInput) => {
      const args = getVerbose() ? ["-v", ...input.args] : [...input.args]
      return api.createRun({ ...input, args })
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.runs })
    },
  })
}

/** Cancel a run and refresh run lists. */
export function useCancelRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.cancelRun(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: queryKeys.runs })
      void qc.invalidateQueries({ queryKey: queryKeys.run(id) })
    },
  })
}

/** Resume a run's harness session in the OS terminal (fire-and-forget). */
export function useResumeRun() {
  return useMutation({
    mutationFn: (id: string) => api.resumeRun(id),
  })
}

/** Stop a terminal-origin agent on a worktree (SIGTERM→SIGKILL). */
export function useStopTerminal() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (worktreeRef: string) => api.stopTerminal(worktreeRef),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
    },
  })
}

/** Resume a persisted session in the OS terminal (fire-and-forget). */
export function useResumeSession() {
  return useMutation({
    mutationFn: (id: string) => api.resumeSession(id),
  })
}

/** Invalidate shared queries after any run finishes (config-changing commands). */
export function invalidateAfterRun(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: queryKeys.runs })
  void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
  void qc.invalidateQueries({ queryKey: queryKeys.repos })
  void qc.invalidateQueries({ queryKey: queryKeys.trackers })
  void qc.invalidateQueries({ queryKey: queryKeys.links })
  void qc.invalidateQueries({ queryKey: queryKeys.doctor })
  void qc.invalidateQueries({ queryKey: queryKeys.flowTemplates })
  void qc.invalidateQueries({ queryKey: ["flows"] })
}
