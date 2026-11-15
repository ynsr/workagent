import { describe, expect, it } from "vitest"
import { commandAction, needsConfirm } from "./runs"

describe("needsConfirm (backend _needs_confirm mirror)", () => {
  it("gates flow run / template delete, not template add", () => {
    expect(needsConfirm("flow", ["run", "full", "jira:X"])).toBe(true)
    expect(needsConfirm("flow", ["template", "delete", "full"])).toBe(true)
    expect(needsConfirm("flow", ["template", "add", "--name", "X"])).toBe(false)
    expect(needsConfirm("flow", ["run", "full", "jira:X", "--dry-run"])).toBe(false)
  })

  it("gates whole destructive commands and action/message runs", () => {
    for (const cmd of ["start", "review", "sync", "release", "cleanup", "adhoc"]) {
      expect(needsConfirm(cmd, ["whatever"])).toBe(true)
    }
    expect(needsConfirm("action", ["run", "lbl", "jira:X"])).toBe(true)
    expect(needsConfirm("action", ["add", "--name", "X"])).toBe(false)
    expect(needsConfirm("message", ["flow", "run", "lbl", "p1"])).toBe(true)
    expect(needsConfirm("message", ["add", "--message-id", "p1"])).toBe(false)
    expect(needsConfirm("register", ["/tmp/wt"])).toBe(false)
    // Issue #99: the exact Re-run payload from the report.
    expect(needsConfirm("flow", ["run", "workagent_review_fix_cleanup", "github:ynsr/workagent#95"])).toBe(true)
  })

  it("maps flow runs to the flow opt-out bucket", () => {
    expect(commandAction("flow", ["run", "full", "jira:X"])).toBe("flow")
    expect(commandAction("flow", ["template", "delete", "full"])).toBe("flow template delete")
    expect(commandAction("flow", ["template", "add", "--name", "X"])).toBe("flow")
  })
})
