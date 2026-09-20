import { useEffect, useRef, useState } from "react"
import type { RunLine, RunState } from "@/lib/api"

export interface RunEventOptions {
  /** Seed lines (from GET /api/runs/{id}) used when the stream (re)connects. */
  initialLines?: RunLine[]
  /** Called once when the server emits the terminal state event. */
  onState?: (state: RunState, exitCode: number | null) => void
}

export interface RunEventState {
  lines: RunLine[]
  state: RunState | null
  exitCode: number | null
  /** SSE connection is open (a terminal state closes it). */
  connected: boolean
}

/** Dedupe by seq and keep ascending order (resume replays can repeat lines). */
export function mergeLines(
  prev: RunLine[],
  incoming: RunLine[] | undefined,
): RunLine[] {
  if (!incoming || incoming.length === 0) return prev
  const bySeq = new Map<number, string>()
  for (const line of prev) bySeq.set(line.seq, line.text)
  for (const line of incoming) bySeq.set(line.seq, line.text)
  return [...bySeq.entries()]
    .sort(([a], [b]) => a - b)
    .map(([seq, text]) => ({ seq, text }))
}

/**
 * Subscribe to GET /api/runs/{id}/events (SSE).
 *
 * EventSource reconnects natively and sends Last-Event-ID, so resume after a
 * dropped connection only replays lines with seq > last — mergeLines absorbs
 * the replay. The terminal `state` event closes the stream (the server closes
 * it too).
 */
export function useRunEvents(
  runId: string | null,
  options?: RunEventOptions,
): RunEventState {
  const [lines, setLines] = useState<RunLine[]>([])
  const [state, setState] = useState<RunState | null>(null)
  const [exitCode, setExitCode] = useState<number | null>(null)
  const [connected, setConnected] = useState(false)

  const initialRef = useRef<RunLine[] | undefined>(options?.initialLines)
  initialRef.current = options?.initialLines
  const onStateRef = useRef<RunEventOptions["onState"]>(options?.onState)
  onStateRef.current = options?.onState

  useEffect(() => {
    if (!runId) {
      setLines([])
      setState(null)
      setExitCode(null)
      setConnected(false)
      return
    }
    setLines(mergeLines([], initialRef.current))
    setState(null)
    setExitCode(null)
    setConnected(false)

    const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`)
    let terminal = false

    es.onopen = () => {
      if (!terminal) setConnected(true)
    }
    es.onerror = () => {
      // Native reconnect with Last-Event-ID; only a terminal state closes us.
      if (!terminal) setConnected(false)
    }
    es.addEventListener("log", (ev) => {
      try {
        const line = JSON.parse(
          (ev as MessageEvent<string>).data,
        ) as RunLine
        setLines((prev) => mergeLines(prev, [line]))
      } catch {
        /* ignore malformed frames */
      }
    })
    es.addEventListener("state", (ev) => {
      try {
        const payload = JSON.parse(
          (ev as MessageEvent<string>).data,
        ) as { state: RunState; exit_code: number | null }
        setState(payload.state)
        setExitCode(payload.exit_code ?? null)
        terminal = true
        es.close()
        setConnected(false)
        onStateRef.current?.(payload.state, payload.exit_code ?? null)
      } catch {
        /* ignore malformed frames */
      }
    })

    return () => {
      terminal = true
      es.close()
    }
  }, [runId])

  return { lines, state, exitCode, connected }
}
