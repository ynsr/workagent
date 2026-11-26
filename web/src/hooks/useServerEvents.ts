import { useEffect, useSyncExternalStore } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { queryKeys } from "@/lib/queries"

export interface ServerChangeEvent {
  seq: number
  table: string
  row_key: string
  op: "insert" | "update" | "delete"
  created_at: number
}

let connected = false
const listeners = new Set<() => void>()

function setConnected(next: boolean): void {
  if (connected === next) return
  connected = next
  for (const listener of listeners) {
    listener()
  }
}

function subscribeConnected(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getConnectedSnapshot(): boolean {
  return connected
}

/** Reactive SSE connection status (true while GET /api/events is open). */
export function useServerEventsConnected(): boolean {
  return useSyncExternalStore(
    subscribeConnected,
    getConnectedSnapshot,
    () => false,
  )
}

/** Test helper to reset module-level connection state between unit tests. */
export function _resetServerEventsConnectedForTest(): void {
  setConnected(false)
}

const DEBOUNCE_MS = 150

/**
 * Subscribe to the global GET /api/events CDC SSE stream and invalidate
 * exact TanStack Query keys when underlying SQLite tables change.
 * Mounted once in AppLayout.
 */
export function useServerEvents(): void {
  const qc = useQueryClient()

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setConnected(false)
      return
    }

    const es = new EventSource("/api/events")
    let hadError = false
    const pendingTimers = new Map<string, ReturnType<typeof setTimeout>>()

    const scheduleInvalidate = (queryKey: readonly unknown[]) => {
      const k = JSON.stringify(queryKey)
      if (pendingTimers.has(k)) return
      const timer = setTimeout(() => {
        pendingTimers.delete(k)
        void qc.invalidateQueries({ queryKey })
      }, DEBOUNCE_MS)
      pendingTimers.set(k, timer)
    }

    const onOpen = () => {
      setConnected(true)
      if (hadError) {
        hadError = false
        void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
        void qc.invalidateQueries({ queryKey: queryKeys.links })
        void qc.invalidateQueries({ queryKey: queryKeys.runs })
        void qc.invalidateQueries({ queryKey: ["flows"] })
        void qc.invalidateQueries({ queryKey: queryKeys.schedules })
        void qc.invalidateQueries({ queryKey: ["messages"] })
        void qc.invalidateQueries({ queryKey: queryKeys.prodtests })
        void qc.invalidateQueries({ queryKey: queryKeys.repos })
        void qc.invalidateQueries({ queryKey: queryKeys.trackers })
        void qc.invalidateQueries({ queryKey: queryKeys.candidatesPrs })
        void qc.invalidateQueries({ queryKey: ["candidates", "issues"] })
        void qc.invalidateQueries({ queryKey: queryKeys.candidatesWorktrees })
        void qc.invalidateQueries({ queryKey: ["release"] })
      }
    }

    const onError = () => {
      hadError = true
      setConnected(false)
    }

    const onChange = (evt: MessageEvent) => {
      let payload: ServerChangeEvent
      try {
        payload = JSON.parse(String(evt.data)) as ServerChangeEvent
      } catch {
        return
      }
      switch (payload.table) {
        case "worktrees":
          scheduleInvalidate(queryKeys.statusAll)
          scheduleInvalidate(queryKeys.links)
          scheduleInvalidate(queryKeys.candidatesPrs)
          scheduleInvalidate(queryKeys.candidatesWorktrees)
          break
        case "pr_cache":
          scheduleInvalidate(queryKeys.statusAll)
          break
        case "pr_candidates_cache":
          scheduleInvalidate(queryKeys.candidatesPrs)
          break
        case "issue_cache":
          scheduleInvalidate(["candidates", "issues"])
          scheduleInvalidate(["issues"])
          break
        case "release_diff_cache":
          scheduleInvalidate(["release"])
          break
        case "repos":
          scheduleInvalidate(queryKeys.repos)
          scheduleInvalidate(["release"])
          scheduleInvalidate(queryKeys.candidatesPrs)
          break
        case "trackers":
          scheduleInvalidate(queryKeys.trackers)
          scheduleInvalidate(queryKeys.trackerDomains)
          scheduleInvalidate(["candidates", "issues"])
          scheduleInvalidate(["issues"])
          break
        case "pr_checks":
        case "pr_review_threads":
          if (payload.row_key) {
            scheduleInvalidate(queryKeys.worktreeTimeline(payload.row_key))
          } else {
            scheduleInvalidate(["worktree", "timeline"])
          }
          break
        case "runs":
          scheduleInvalidate(queryKeys.runs)
          scheduleInvalidate(queryKeys.statusAll)
          if (payload.row_key) {
            scheduleInvalidate(queryKeys.run(payload.row_key))
            scheduleInvalidate(queryKeys.run(`db-${payload.row_key}`))
          }
          break
        case "flows":
          scheduleInvalidate(["flows"])
          scheduleInvalidate(queryKeys.statusAll)
          break
        case "schedules":
          scheduleInvalidate(queryKeys.schedules)
          break
        case "messages":
          scheduleInvalidate(["messages"])
          break
        case "prodtest_items":
          scheduleInvalidate(queryKeys.prodtests)
          scheduleInvalidate(queryKeys.statusAll)
          if (payload.row_key) {
            scheduleInvalidate(queryKeys.prodtestDetail(payload.row_key))
          }
          break
        default:
          break
      }
    }

    es.addEventListener("open", onOpen)
    es.addEventListener("error", onError)
    es.addEventListener("change", onChange as EventListener)

    return () => {
      for (const timer of pendingTimers.values()) {
        clearTimeout(timer)
      }
      pendingTimers.clear()
      es.removeEventListener("open", onOpen)
      es.removeEventListener("error", onError)
      es.removeEventListener("change", onChange as EventListener)
      es.close()
      setConnected(false)
    }
  }, [qc])
}
