import { api, type FlowStep } from "@/lib/api"

export const EXECUTION_MODES = ["sequential", "parallel"] as const
export const SEQUENTIAL_ONLY_STEPS = ["start", "cleanup", "release"] as const

/** True when every step may run concurrently across worktrees (fire-all).
 * Any sequential step forces worktree-by-worktree execution. */
export function chainRunsParallel(steps: FlowStep[]): boolean {
  return steps.length > 0 && steps.every((s) => (s.execution_mode ?? "sequential") === "parallel")
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
