import { describe, expect, it } from "vitest"
import { chainRunsParallel, runChainOnWorktrees } from "@/lib/flowExecution"
import { validateChain } from "@/components/ChainEditor"
import type { FlowStep } from "@/lib/api"

describe("chainRunsParallel", () => {
  it("ignores forced-sequential start/cleanup steps", () => {
    const steps: FlowStep[] = [
      { kind: "start", config: {} },
      { kind: "review", config: {}, execution_mode: "parallel" },
      { kind: "fix_comments", config: {}, execution_mode: "parallel" },
      { kind: "cleanup", config: {} },
    ]
    expect(chainRunsParallel(steps)).toBe(true)
  })

  it("returns false when any parallelizable step is sequential", () => {
    const steps: FlowStep[] = [
      { kind: "start", config: {} },
      { kind: "review", config: {}, execution_mode: "parallel" },
      { kind: "fix_comments", config: {} },
      { kind: "cleanup", config: {} },
    ]
    expect(chainRunsParallel(steps)).toBe(false)
  })

  it("returns false for empty or sequential-only chains", () => {
    expect(chainRunsParallel([])).toBe(false)
    expect(chainRunsParallel([{ kind: "start", config: {} }])).toBe(false)
  })
})

describe("validateChain prodtest_plan rule", () => {
  it("rejects prodtest_plan after cleanup", () => {
    const steps: FlowStep[] = [
      { kind: "cleanup", config: {} },
      { kind: "prodtest_plan", config: {} },
    ]
    expect(validateChain(steps).some((e) => e.includes("'prodtest_plan'"))).toBe(true)
  })
})

describe("runChainOnWorktrees", () => {
  it("fans out parallel chains and never rejects", async () => {
    const order: string[] = []
    const hooks: { started: string[]; errors: string[] } = { started: [], errors: [] }
    await runChainOnWorktrees({
      keys: ["b", "a"],
      parallel: true,
      createRun: async (key) => {
        order.push(key)
        if (key === "b") throw new Error("boom")
        return { run_id: `run-${key}` }
      },
      hooks: {
        onStarted: (key) => { hooks.started.push(key) },
        onError: (key) => { hooks.errors.push(key) },
      },
    })
    expect(order.sort()).toEqual(["a", "b"])
    expect(hooks.started).toEqual(["a"])
    expect(hooks.errors).toEqual(["b"])
  })

  it("runs sequential chains one worktree at a time", async () => {
    const started: string[] = []
    const settled: string[] = []
    let concurrent = 0
    let maxConcurrent = 0
    const gates = new Map<string, () => void>()
    const gate = (key: string) => new Promise<void>((resolve) => {
      gates.set(key, resolve)
    })
    const pending = runChainOnWorktrees({
      keys: ["a", "b"],
      parallel: false,
      createRun: (key) => {
        started.push(key)
        concurrent += 1
        maxConcurrent = Math.max(maxConcurrent, concurrent)
        return gate(key).then(() => {
          concurrent -= 1
          return { run_id: `run-${key}` }
        })
      },
      hooks: { onSettled: (key) => { settled.push(key) } },
    })
    // Sequential: b must not start until a's gate resolves and settles.
    await Promise.resolve()
    await Promise.resolve()
    expect(started).toEqual(["a"])
    gates.get("a")?.()
    for (let i = 0; i < 20 && !gates.has("b"); i += 1) await Promise.resolve()
    expect(started).toEqual(["a", "b"])
    gates.get("b")?.()
    await pending
    expect(settled).toEqual(["a", "b"])
    expect(maxConcurrent).toBe(1)
  })
})
