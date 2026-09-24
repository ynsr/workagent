import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { QueryClient } from "@tanstack/react-query"
import { api, type CreateRunInput } from "./api"
import { getVerbose } from "./settings"

export const queryKeys = {
  info: ["info"] as const,
  statusAll: ["status", "all"] as const,
  statusDetail: (ref: string) => ["status-detail", ref] as const,
  path: (ref: string) => ["path", ref] as const,
  repos: ["repos"] as const,
  trackers: ["trackers"] as const,
  links: ["links"] as const,
  doctor: ["doctor"] as const,
  runs: ["runs"] as const,
  run: (id: string) => ["runs", "detail", id] as const,
  issues: ["issues"] as const,
  candidates: ["candidates"] as const,
  sessions: ["sessions"] as const,
  session: (id: string) => ["sessions", "detail", id] as const,
}

/** Poll /api/status every 15 s while the tab is visible (contract). */
export function useStatusAll(refresh?: boolean) {
  return useQuery({
    queryKey: [...queryKeys.statusAll, refresh ?? false],
    queryFn: () => api.statusAll(refresh ? { refresh: true } : undefined),
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
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

export function useLinks() {
  return useQuery({ queryKey: queryKeys.links, queryFn: api.links })
}

export function useDoctor() {
  return useQuery({ queryKey: queryKeys.doctor, queryFn: api.doctor })
}

export function useIssues() {
  return useQuery({ queryKey: queryKeys.issues, queryFn: api.issues, staleTime: 3600_000 })
}

export function useCandidates() {
  return useQuery({ queryKey: queryKeys.candidates, queryFn: api.candidates, staleTime: 3600_000 })
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

/** Resume a run's runtime session in the OS terminal (fire-and-forget). */
export function useResumeRun() {
  return useMutation({
    mutationFn: (id: string) => api.resumeRun(id),
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
}
