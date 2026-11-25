import { api, type FlowExecutionMode, type FlowStep } from "@/lib/api"

export const EXECUTION_MODES = ["sequential", "parallel"] as const
export const SEQUENTIAL_ONLY_STEPS = ["cleanup", "release"] as const

export function defaultExecutionMode(kind: string): FlowExecutionMode {
  return kind === "start" ? "parallel" : "sequential"
}

export function effectiveExecutionMode(
  step: Pick<FlowStep, "kind" | "execution_mode">,
): FlowExecutionMode {
  return step.execution_mode ?? defaultExecutionMode(step.kind)
}

/** Counts parallelizable steps by mode for summary lines/badges. `forced`
 * counts cleanup/release steps (always sequential across worktrees). */
export function summarizeExecution(steps: FlowStep[]): {
  parallel: number
  sequential: number
  forced: number
  runsParallel: boolean
} {
  let parallel = 0
  let sequential = 0
  let forced = 0
  for (const s of steps) {
    if ((SEQUENTIAL_ONLY_STEPS as readonly string[]).includes(s.kind)) {
      forced += 1
      continue
    }
    if (effectiveExecutionMode(s) === "parallel") parallel += 1
    else sequential += 1
  }
  return { parallel, sequential, forced, runsParallel: chainRunsParallel(steps) }
}

/** True when every parallelizable step may run concurrently across worktrees.
 * cleanup/release always run sequentially (backend forces it), so they
 * are excluded — otherwise any realistic chain would force sequential mode
 * and the concurrent fan-out would be dead code. Any remaining sequential
 * step forces worktree-by-worktree execution. */
export function chainRunsParallel(steps: FlowStep[]): boolean {
  const parallelizable = steps.filter(
    (s) => !(SEQUENTIAL_ONLY_STEPS as readonly string[]).includes(s.kind),
  )
  if (parallelizable.length === 0) return false
  return parallelizable.every((s) => effectiveExecutionMode(s) === "parallel")
}

/** Poll a just-created run until it leaves `running`. Returns true when the
 * run settled, false when the ~60 s cap expired and the run may still be
 * going. Sequential fan-out never blocks forever on one stuck run. */
export async function awaitRunSettled(runId: string): Promise<boolean> {
  const deadline = Date.now() + 60_000
  for (;;) {
    const run = await api.run(runId).catch(() => null)
    if (run?.state !== "running") return true
    if (Date.now() >= deadline) return false
    await new Promise((resolve) => { globalThis.setTimeout(resolve, 2000) })
  }
}

export interface ChainRunHooks {
  onStarted?: (key: string, runId: string) => void
  onSettled?: (key: string, runId: string) => void
  /** Fired instead of `onSettled` when the settle poll times out and the run
   * may still be going. Falls back to `onSettled` when unset. */
  onTimeout?: (key: string, runId: string) => void
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
    const outcomes = await Promise.all(keys.map(async (key) => {
      try {
        const { run_id } = await createRun(key)
        return { status: "fulfilled" as const, key, run_id }
      } catch (reason) {
        return { status: "rejected" as const, key, reason }
      }
    }))
    outcomes.forEach((r) => {
      if (r.status === "fulfilled") hooks?.onStarted?.(r.key, r.run_id)
      else hooks?.onError?.(r.key, r.reason)
    })
    return
  }
  for (const key of keys) {
    try {
      const { run_id } = await createRun(key)
      hooks?.onStarted?.(key, run_id)
      if (await awaitRunSettled(run_id)) hooks?.onSettled?.(key, run_id)
      else if (hooks?.onTimeout) hooks.onTimeout(key, run_id)
      else hooks?.onSettled?.(key, run_id)
    } catch (reason) {
      hooks?.onError?.(key, reason)
    }
  }
}
