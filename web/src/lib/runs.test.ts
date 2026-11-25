import { describe, expect, it } from "vitest"
import { queryKeys } from "./queries"
import { buildRerunArgs, commandAction, hasChainChangeError, needsConfirm, runKind } from "./runs"

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
  it("labels from the stored session_type", () => {
    expect(runKind("review", ["jira:X"], "fix_comments")).toBe("Fix comments")
    expect(runKind("review", ["jira:X"], "review")).toBe("Review")
    expect(runKind("prodtest", ["run", "jira:X"], "prodtest_run")).toBe("Prodtest run")
    expect(runKind("action", ["run", "lint", "jira:X"], "custom_action")).toBe("Custom action")
    expect(runKind("flow", ["run", "ship", "jira:X"], "flow")).toBe("Flow")
  })

  it("falls back to the command for pre-migration rows (null session_type)", () => {
    expect(runKind("start", ["jira:X"], null)).toBe("Start (task)")
    expect(runKind("review", ["jira:X"], null)).toBe("Review")
    expect(runKind("review", ["--fix-comments", "jira:X"], null)).toBe("Fix comments")
    expect(runKind("sync", ["jira:X"], null)).toBe("Sync")
    expect(runKind("flow", ["run", "ship", "jira:X"], null)).toBe("Flow")
    expect(runKind("action", ["run", "lint", "jira:X"], null)).toBe("Custom action")
    // The web launches prodtest split (command="prodtest", args[0]=plan|run)
    // and two-word legacy rows store the joined command.
    expect(runKind("prodtest", ["plan", "jira:X"], null)).toBe("Prodtest plan")
    expect(runKind("prodtest", ["run", "jira:X"], null)).toBe("Prodtest run")
    expect(runKind("prodtest plan", ["jira:X"], null)).toBe("Prodtest plan")
    expect(runKind("prodtest run", ["jira:X"], null)).toBe("Prodtest run")
  })

  it("falls back to the raw command for runs without a transcript kind", () => {
    expect(runKind("cleanup", ["jira:X"], null)).toBe("cleanup")
    expect(runKind("harness", [], null)).toBe("harness")
  })

  it("ignores prototype-named stored types", () => {
    // A plain-object index resolves 'constructor'/'toString' to
    // Object.prototype functions; only own keys may label a run.
    expect(runKind("cleanup", ["jira:X"], "constructor")).toBe("cleanup")
    expect(runKind("cleanup", ["jira:X"], "toString")).toBe("cleanup")
    expect(runKind("cleanup", ["jira:X"], "__proto__")).toBe("cleanup")
  })
})

describe("query keys", () => {
  it("expose no sessions surface", () => {
    expect(Object.keys(queryKeys)).not.toContain("session")
    expect(JSON.stringify(queryKeys)).not.toContain("sessions")
  })
})

describe("buildRerunArgs / hasChainChangeError (issue #183)", () => {
  const STEP_DIFF_LINE =
    "error: --steps differs from the resumable run 155 (current step 0) — re-run with --fresh for a new run on the new chain, or without --steps to resume"
  const CONFIG_DIFF_LINE =
    "error: --steps config differs from the resumable run 155 — re-run with --fresh for a new run on the new chain, without --steps to resume, or pass config edits via --step-config"

  it("detects both --steps diff and --steps config diff errors", () => {
    expect(hasChainChangeError([STEP_DIFF_LINE])).toBe(true)
    expect(hasChainChangeError([{ seq: 1, text: CONFIG_DIFF_LINE }])).toBe(true)
    expect(hasChainChangeError(["error: unparseable ref"])).toBe(false)
  })

  it("appends --fresh for flow run when chain-change error occurred", () => {
    expect(
      buildRerunArgs(
        "flow",
        ["-v", "run", "review_fix", "ao/wave2-iban/root", "--steps", "[]", "--yes"],
        [{ seq: 1, text: STEP_DIFF_LINE }],
      ),
    ).toEqual(["run", "review_fix", "ao/wave2-iban/root", "--steps", "[]", "--fresh"])
  })

  it("does not duplicate --fresh if already present", () => {
    expect(
      buildRerunArgs(
        "flow",
        ["run", "review_fix", "ao/wave2-iban/root", "--fresh", "--yes"],
        [STEP_DIFF_LINE],
      ),
    ).toEqual(["run", "review_fix", "ao/wave2-iban/root", "--fresh"])
  })

  it("does not add --fresh for other errors or non-flow-run commands", () => {
    expect(
      buildRerunArgs("flow", ["run", "review_fix", "jira:X", "--yes"], ["error: no linked state"]),
    ).toEqual(["run", "review_fix", "jira:X"])
    expect(
      buildRerunArgs("review", ["jira:X", "--yes"], [STEP_DIFF_LINE]),
    ).toEqual(["jira:X"])
  })
})
