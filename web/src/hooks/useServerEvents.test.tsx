import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { render, act } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  useServerEvents,
  useServerEventsConnected,
  _resetServerEventsConnectedForTest,
} from "./useServerEvents"
import { queryKeys } from "@/lib/queries"

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  closed = false
  listeners = new Map<string, Set<(ev: Event) => void>>()

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, cb: (ev: Event) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)!.add(cb)
  }

  removeEventListener(type: string, cb: (ev: Event) => void) {
    this.listeners.get(type)?.delete(cb)
  }

  emit(type: string, data?: unknown) {
    const ev =
      data !== undefined
        ? new MessageEvent(type, { data: JSON.stringify(data) })
        : new Event(type)
    for (const cb of this.listeners.get(type) ?? []) {
      cb(ev)
    }
  }

  close() {
    this.closed = true
  }
}

function Harness({ onConnected }: { onConnected?: (v: boolean) => void }) {
  useServerEvents()
  const connected = useServerEventsConnected()
  onConnected?.(connected)
  return <div data-testid="sse-state">{connected ? "connected" : "disconnected"}</div>
}

describe("useServerEvents", () => {
  const origEventSource = globalThis.EventSource

  beforeEach(() => {
    MockEventSource.instances = []
    _resetServerEventsConnectedForTest()
    ;(globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource =
      MockEventSource
  })

  afterEach(() => {
    ;(globalThis as unknown as { EventSource: typeof EventSource }).EventSource =
      origEventSource
    _resetServerEventsConnectedForTest()
  })

  it("tracks connection state and closes EventSource on unmount", () => {
    const qc = new QueryClient()
    const { getByTestId, unmount } = render(
      <QueryClientProvider client={qc}>
        <Harness />
      </QueryClientProvider>,
    )

    expect(MockEventSource.instances).toHaveLength(1)
    const es = MockEventSource.instances[0]!
    expect(es.url).toBe("/api/events")
    expect(getByTestId("sse-state").textContent).toBe("disconnected")

    act(() => {
      es.emit("open")
    })
    expect(getByTestId("sse-state").textContent).toBe("connected")

    act(() => {
      es.emit("error")
    })
    expect(getByTestId("sse-state").textContent).toBe("disconnected")

    unmount()
    expect(es.closed).toBe(true)
  })

  it("invalidates exact TanStack Query keys for each tracked table", () => {
    vi.useFakeTimers()
    try {
      const qc = new QueryClient()
      const spy = vi.spyOn(qc, "invalidateQueries")

      render(
        <QueryClientProvider client={qc}>
          <Harness />
        </QueryClientProvider>,
      )
      const es = MockEventSource.instances[0]!

      act(() => {
        es.emit("change", { seq: 1, table: "worktrees", row_key: "jira:IPG-1", op: "update", created_at: 1 })
        vi.advanceTimersByTime(200)
      })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.links })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.candidatesPrs })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.candidatesWorktrees })

      spy.mockClear()
      act(() => {
        es.emit("change", { seq: 2, table: "runs", row_key: "42", op: "insert", created_at: 2 })
        vi.advanceTimersByTime(200)
      })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.runs })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.run("42") })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.run("db-42") })

      spy.mockClear()
      act(() => {
        es.emit("change", { seq: 3, table: "flows", row_key: "7", op: "update", created_at: 3 })
        vi.advanceTimersByTime(200)
      })
      expect(spy).toHaveBeenCalledWith({ queryKey: ["flows"] })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })

      spy.mockClear()
      act(() => {
        es.emit("change", { seq: 4, table: "messages", row_key: "m-1", op: "insert", created_at: 4 })
        vi.advanceTimersByTime(200)
      })
      expect(spy).toHaveBeenCalledWith({ queryKey: ["messages"] })

      spy.mockClear()
      act(() => {
        es.emit("change", { seq: 5, table: "prodtest_items", row_key: "IPG-9", op: "update", created_at: 5 })
        vi.advanceTimersByTime(200)
      })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.prodtests })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.prodtestDetail("IPG-9") })

      spy.mockClear()
      act(() => {
        es.emit("change", { seq: 6, table: "pr_candidates_cache", row_key: "prs", op: "update", created_at: 6 })
        es.emit("change", { seq: 7, table: "issue_cache", row_key: "jira:mine90", op: "update", created_at: 7 })
        es.emit("change", { seq: 8, table: "release_diff_cache", row_key: "demo", op: "update", created_at: 8 })
        es.emit("change", { seq: 9, table: "repos", row_key: "demo", op: "update", created_at: 9 })
        es.emit("change", { seq: 10, table: "trackers", row_key: "jira:IPG", op: "update", created_at: 10 })
        es.emit("change", { seq: 11, table: "pr_checks", row_key: "jira:IPG-1", op: "insert", created_at: 11 })
        vi.advanceTimersByTime(200)
      })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.candidatesPrs })
      expect(spy).toHaveBeenCalledWith({ queryKey: ["candidates", "issues"] })
      expect(spy).toHaveBeenCalledWith({ queryKey: ["issues"] })
      expect(spy).toHaveBeenCalledWith({ queryKey: ["release"] })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.repos })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.trackers })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.trackerDomains })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.worktreeTimeline("jira:IPG-1") })
    } finally {
      vi.useRealTimers()
    }
  })

  it("invalidates core queries on reconnect after an error, but not on initial open", () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, "invalidateQueries")

    render(
      <QueryClientProvider client={qc}>
        <Harness />
      </QueryClientProvider>,
    )
    const es = MockEventSource.instances[0]!

    act(() => {
      es.emit("open")
    })
    expect(spy).not.toHaveBeenCalled()

    act(() => {
      es.emit("error")
    })
    expect(spy).not.toHaveBeenCalled()

    act(() => {
      es.emit("open")
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusInactive })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusDeleted })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.links })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.runs })
    expect(spy).toHaveBeenCalledWith({ queryKey: ["flows"] })
    expect(spy).toHaveBeenCalledWith({ queryKey: ["messages"] })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.prodtests })
  })

  it("coalesces burst pr_cache events into a single status invalidation", () => {
    vi.useFakeTimers()
    try {
      const qc = new QueryClient()
      const spy = vi.spyOn(qc, "invalidateQueries")

      render(
        <QueryClientProvider client={qc}>
          <Harness />
        </QueryClientProvider>,
      )
      const es = MockEventSource.instances[0]!

      act(() => {
        es.emit("change", { seq: 10, table: "pr_cache", row_key: "a", op: "update", created_at: 10 })
        es.emit("change", { seq: 11, table: "pr_cache", row_key: "b", op: "update", created_at: 10 })
        es.emit("change", { seq: 12, table: "pr_cache", row_key: "c", op: "update", created_at: 10 })
      })
      expect(spy).not.toHaveBeenCalled()

      act(() => {
        vi.advanceTimersByTime(500)
      })
      expect(spy).toHaveBeenCalledTimes(3)
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusInactive })
      expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusDeleted })
    } finally {
      vi.useRealTimers()
    }
  })
})
