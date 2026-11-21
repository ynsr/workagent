import { describe, expect, it } from "vitest"
import { modelEffortArgs, modelEffortFlags, parseAcpBlocks } from "@/lib/harness"
import { RUN_MODE_OPTIONS, runModeArgs } from "@/lib/launchConfig"
import { actionRunModeOf, buildImmediateActionArgs } from "@/lib/messageRunConfig"
import { messageRunModeArgs } from "@/lib/messageLaunchConfig"

describe("ACP harness data layer (issue #112)", () => {
  it("RunMode includes acp with --acp flag", () => {
    expect(RUN_MODE_OPTIONS.map((o) => o.value)).toEqual(["preview", "headless", "acp", "terminal"])
    expect(runModeArgs("acp")).toEqual(["--acp"])
    expect(runModeArgs("headless")).toEqual(["--launch"])
    expect(runModeArgs("terminal")).toEqual(["--terminal"])
    expect(runModeArgs("preview")).toEqual([])
  })

  it("message run modes include acp", () => {
    expect(messageRunModeArgs("acp")).toEqual(["--acp"])
    expect(messageRunModeArgs("preview")).toEqual([])
  })

  it("actionRunModeOf accepts acp", () => {
    expect(actionRunModeOf({ default_options: { run_mode: "acp" } })).toBe("acp")
    expect(actionRunModeOf({ default_options: { run_mode: "bogus" } })).toBe("preview")
  })

  it("buildImmediateActionArgs maps acp to --acp", () => {
    expect(buildImmediateActionArgs("audit", "jira:IPG-1", "acp", "")).toEqual(
      ["run", "audit", "jira:IPG-1", "--acp", "--yes"],
    )
  })

  it("modelEffortArgs emits flags only when non-empty", () => {
    expect(modelEffortArgs({ model: "", effort: "" })).toEqual([])
    expect(modelEffortArgs({ model: "m1", effort: "" })).toEqual(["--model", "m1"])
    expect(modelEffortArgs({ model: "", effort: "high" })).toEqual(["--effort", "high"])
    expect(modelEffortArgs({ model: "m1", effort: "high" })).toEqual(["--model", "m1", "--effort", "high"])
    expect(modelEffortFlags("", "x")).toEqual(["--effort", "x"])
  })

  it("parseAcpBlocks skips a malformed head-truncated first line", () => {
    const blocks = parseAcpBlocks([
      { seq: 0, text: 'ACP:{"t":"message","text":"trunca' },
      { seq: 1, text: 'ACP:{"t":"message","text":"hello"}' },
      { seq: 2, text: "plain raw line" },
    ])
    expect(blocks.map((b) => b.kind)).toEqual(["message", "raw"])
  })
})
