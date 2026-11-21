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
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.links })

    spy.mockClear()
    act(() => {
      es.emit("change", { seq: 2, table: "runs", row_key: "42", op: "insert", created_at: 2 })
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.runs })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.run("42") })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.run("db-42") })

    spy.mockClear()
    act(() => {
      es.emit("change", { seq: 3, table: "flows", row_key: "7", op: "update", created_at: 3 })
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: ["flows"] })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })

    spy.mockClear()
    act(() => {
      es.emit("change", { seq: 4, table: "messages", row_key: "m-1", op: "insert", created_at: 4 })
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: ["messages"] })

    spy.mockClear()
    act(() => {
      es.emit("change", { seq: 5, table: "prodtest_items", row_key: "IPG-9", op: "update", created_at: 5 })
    })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.prodtests })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.statusAll })
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.prodtestDetail("IPG-9") })
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
})
