import { api, type FlowStep } from "@/lib/api"

export const EXECUTION_MODES = ["sequential", "parallel"] as const
export const SEQUENTIAL_ONLY_STEPS = ["start", "cleanup", "release"] as const

/** True when every parallelizable step may run concurrently across worktrees.
 * start/cleanup/release always run sequentially (backend forces it), so they
 * are excluded — otherwise any realistic chain would force sequential mode
 * and the concurrent fan-out would be dead code. Any remaining sequential
 * step forces worktree-by-worktree execution. */
export function chainRunsParallel(steps: FlowStep[]): boolean {
  const parallelizable = steps.filter(
    (s) => !(SEQUENTIAL_ONLY_STEPS as readonly string[]).includes(s.kind),
  )
  if (parallelizable.length === 0) return false
  return parallelizable.every((s) => (s.execution_mode ?? "sequential") === "parallel")
}

/** Poll a just-created run until it leaves `running` (settled or timeout).
 * Sequential fan-out waits for each worktree's run before starting the
 * next; ~60 s cap, then continues so one stuck run never blocks the queue. */
export async function awaitRunSettled(runId: string): Promise<void> {
  const deadline = Date.now() + 60_000
  for (;;) {
    const run = await api.run(runId).catch(() => null)
    if (run?.state !== "running") return
    if (Date.now() >= deadline) return
    await new Promise((resolve) => { globalThis.setTimeout(resolve, 2000) })
  }
}

export interface ChainRunHooks {
  onStarted?: (key: string, runId: string) => void
  onSettled?: (key: string, runId: string) => void
  onError?: (key: string, reason: unknown) => void
}

/** Fan out one flow run per worktree key. Sequential chains await each run
 * settling before starting the next; parallel chains start all at once.
 * Never rejects (per-key failures go to `onError`) so callers can safely
 * fire-and-forget it AFTER closing their dialog — awaiting the ~60 s
 * per-worktree settle poll with the modal open stalls the UI (#69). */
export async function runChainOnWorktrees(opts: {
  keys: string[]
  parallel: boolean
  createRun: (key: string) => Promise<{ run_id: string }>
  hooks?: ChainRunHooks
}): Promise<void> {
  const { keys, parallel, createRun, hooks } = opts
  if (parallel) {
    const started = await Promise.all(keys.map(async (key) => {
      try {
        const { run_id } = await createRun(key)
        return { status: "fulfilled" as const, key, run_id }
      } catch (reason) {
        return { status: "rejected" as const, key, reason }
      }
    }))
    started.forEach((r) => {
      if (r.status === "fulfilled") hooks?.onStarted?.(r.key, r.run_id)
      else hooks?.onError?.(r.key, r.reason)
    })
    return
  }
  for (const key of keys) {
    try {
      const { run_id } = await createRun(key)
      hooks?.onStarted?.(key, run_id)
      await awaitRunSettled(run_id)
      hooks?.onSettled?.(key, run_id)
    } catch (reason) {
      hooks?.onError?.(key, reason)
    }
  }
}
