import { describe, expect, it } from "vitest"
import { queryKeys } from "./queries"
import { commandAction, needsConfirm, runKind } from "./runs"

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

describe("runKind (runs own transcripts)", () => {
  it("mirrors the backend derive_session_type labels", () => {
    expect(runKind("start", ["jira:X"])).toBe("Start (task)")
    expect(runKind("review", ["jira:X"])).toBe("Review")
    expect(runKind("review", ["--fix-comments", "jira:X"])).toBe("Fix comments")
    expect(runKind("sync", ["jira:X"])).toBe("Sync")
    expect(runKind("flow", ["run", "ship", "jira:X"])).toBe("Flow")
    expect(runKind("action", ["run", "lint", "jira:X"])).toBe("Custom action")
    expect(runKind("prodtest plan", ["jira:X"])).toBe("Prodtest plan")
    expect(runKind("prodtest run", ["jira:X"])).toBe("Prodtest run")
  })

  it("falls back to the raw command for runs without a transcript kind", () => {
    expect(runKind("cleanup", ["jira:X"])).toBe("cleanup")
    expect(runKind("harness", [])).toBe("harness")
  })
})

describe("query keys", () => {
  it("expose no sessions surface", () => {
    expect(Object.keys(queryKeys)).not.toContain("session")
    expect(JSON.stringify(queryKeys)).not.toContain("sessions")
  })
})
