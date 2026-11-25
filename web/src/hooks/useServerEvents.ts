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
    let prCacheTimer: ReturnType<typeof setTimeout> | null = null

    const schedulePrCacheInvalidate = () => {
      if (prCacheTimer !== null) return
      prCacheTimer = setTimeout(() => {
        prCacheTimer = null
        void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
        void qc.invalidateQueries({ queryKey: queryKeys.statusInactive })
        void qc.invalidateQueries({ queryKey: queryKeys.statusDeleted })
      }, 500)
    }

    const onOpen = () => {
      setConnected(true)
      if (hadError) {
        hadError = false
        void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
        void qc.invalidateQueries({ queryKey: queryKeys.statusInactive })
        void qc.invalidateQueries({ queryKey: queryKeys.statusDeleted })
        void qc.invalidateQueries({ queryKey: queryKeys.links })
        void qc.invalidateQueries({ queryKey: queryKeys.runs })
        void qc.invalidateQueries({ queryKey: ["flows"] })
        void qc.invalidateQueries({ queryKey: queryKeys.schedules })
        void qc.invalidateQueries({ queryKey: ["messages"] })
        void qc.invalidateQueries({ queryKey: queryKeys.prodtests })
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
          void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
          void qc.invalidateQueries({ queryKey: queryKeys.statusInactive })
          void qc.invalidateQueries({ queryKey: queryKeys.statusDeleted })
          void qc.invalidateQueries({ queryKey: queryKeys.links })
          break
        case "pr_cache":
          schedulePrCacheInvalidate()
          break
        case "runs":
          void qc.invalidateQueries({ queryKey: queryKeys.runs })
          void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
          if (payload.row_key) {
            void qc.invalidateQueries({ queryKey: queryKeys.run(payload.row_key) })
            void qc.invalidateQueries({ queryKey: queryKeys.run(`db-${payload.row_key}`) })
          }
          break
        case "flows":
          void qc.invalidateQueries({ queryKey: ["flows"] })
          void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
          break
        case "schedules":
          void qc.invalidateQueries({ queryKey: queryKeys.schedules })
          break
        case "messages":
          void qc.invalidateQueries({ queryKey: ["messages"] })
          break
        case "prodtest_items":
          void qc.invalidateQueries({ queryKey: queryKeys.prodtests })
          void qc.invalidateQueries({ queryKey: queryKeys.statusAll })
          if (payload.row_key) {
            void qc.invalidateQueries({ queryKey: queryKeys.prodtestDetail(payload.row_key) })
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
      if (prCacheTimer !== null) {
        clearTimeout(prCacheTimer)
        prCacheTimer = null
      }
      es.removeEventListener("open", onOpen)
      es.removeEventListener("error", onError)
      es.removeEventListener("change", onChange as EventListener)
      es.close()
      setConnected(false)
    }
  }, [qc])
}
