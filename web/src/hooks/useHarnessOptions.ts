import { useQuery } from "@tanstack/react-query"
import { api, type HarnessOptions, type PendingQuestion } from "@/lib/api"
import { queryKeys } from "@/lib/queries"

/** ACP-discovered model/effort options for one harness (issue #112).
 * Stale-while-fresh for 10 min (mirrors the server TTL cache). */
export function useHarnessOptions(harness: string, enabled = true) {
  const name = harness.trim() || "agy"
  return useQuery({
    queryKey: queryKeys.harnessOptions(name),
    queryFn: () => api.harnessOptions(name),
    enabled,
    staleTime: 10 * 60 * 1000,
    gcTime: 10 * 60 * 1000,
    retry: false,
  })
}

export type { HarnessOptions, PendingQuestion }
