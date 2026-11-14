import { describe, expect, it } from "vitest"
import { chainRunsParallel, summarizeExecution } from "@/lib/flowExecution"
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
describe("summarizeExecution", () => {
  it("counts modes and flags all-parallel chains", () => {
    const steps: FlowStep[] = [
      { kind: "start", config: {} },
      { kind: "review", config: {}, execution_mode: "parallel" },
      { kind: "fix_comments", config: {} },
    ]
    expect(summarizeExecution(steps)).toEqual({ parallel: 1, sequential: 1, forced: 1, runsParallel: false })
  })

  it("flags fan-out when every parallelizable step is parallel", () => {
    const steps: FlowStep[] = [
      { kind: "review", config: {}, execution_mode: "parallel" },
      { kind: "fix_comments", config: {}, execution_mode: "parallel" },
    ]
    expect(summarizeExecution(steps).runsParallel).toBe(true)
  })
})
