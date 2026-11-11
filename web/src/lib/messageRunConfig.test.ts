import { describe, expect, it } from "vitest"
import {
  actionRunModeOf,
  buildActionUpsertArgs,
  buildFlowTemplateUpsertArgs,
  buildImmediateActionArgs,
  buildImmediateFlowArgs,
  buildImmediateMessageActionArgs,
  buildImmediateWorktreeFlowArgs,
  isRunImmediateAction,
  isRunImmediateTemplate,
  messageScoped,
  splitRefs,
} from "@/lib/messageRunConfig"
import { VALIDATE_CLAIMED_ISSUES_PROMPT, buildMessageArgs } from "@/lib/messageLaunchConfig"

describe("messageRunConfig category filtering", () => {
  it("keeps message rows and defaults missing category to worktree", () => {
    const rows = [
      { label: "a", category: "message" },
      { label: "b", category: "worktree" },
      { label: "c" },
    ]
    expect(messageScoped(rows, "message").map((r) => r.label)).toEqual(["a"])
    expect(messageScoped(rows, "worktree").map((r) => r.label)).toEqual(["b", "c"])
  })
})

describe("actionRunModeOf", () => {
  it("falls back to preview for unknown run modes", () => {
    expect(actionRunModeOf({ default_options: { run_mode: "headless" } })).toBe("headless")
    expect(actionRunModeOf({ default_options: { run_mode: "bogus" } })).toBe("preview")
    expect(actionRunModeOf({})).toBe("preview")
  })
})

describe("action upsert args", () => {
  it("passes --category and the --run-immediately flag for add", () => {
    expect(
      buildActionUpsertArgs(null, {
        name: "Triage",
        label: "triage",
        icon: "Zap",
        prompt: "do it",
        pre: "",
        post: "",
        runMode: "headless",
        runImmediately: true,
        category: "message",
      }),
    ).toEqual([
      "add",
      "--name", "Triage",
      "--label", "triage",
      "--icon", "Zap",
      "--prompt", "do it",
      "--default-options", JSON.stringify({ run_mode: "headless" }),
      "--category", "message",
      "--run-immediately",
    ])
  })

  it("emits --new-label on update and --no-run-immediately when false", () => {
    const args = buildActionUpsertArgs("old", {
      name: "Triage",
      label: "new",
      icon: "",
      prompt: "do it",
      pre: "",
      post: "",
      runMode: "preview",
      runImmediately: false,
      category: "worktree",
    })
    expect(args.slice(0, 3)).toEqual(["update", "old", "--name"])
    expect(args).toContain("--new-label")
    expect(args).toContain("new")
    expect(args).toContain("--no-run-immediately")
    const idx = args.indexOf("--default-options")
    expect(idx).toBeGreaterThan(-1)
    expect(JSON.parse(args[idx + 1] ?? "")).toEqual({ run_mode: "preview" })
  })
})

describe("flow template upsert args", () => {
  it("passes --category and the --run-immediately flag, steps untouched", () => {
    const steps = [{ kind: "start", config: {} }]
    const args = buildFlowTemplateUpsertArgs(null, {
      name: "Handle",
      label: "handle",
      steps,
      pre: "",
      post: "",
      runImmediately: true,
      category: "message",
    })
    expect(args[0]).toBe("template")
    expect(args[1]).toBe("add")
    expect(args).toContain("--category")
    expect(args).toContain("message")
    expect(args).toContain("--run-immediately")
    const idx = args.indexOf("--steps")
    expect(JSON.parse(args[idx + 1] ?? "")).toEqual([{ kind: "start", config: {} }])
  })

  it("preserves an explicit pre_execution script and passes --no-run-immediately", () => {
    const steps = [{ kind: "start", config: {} }]
    const args = buildFlowTemplateUpsertArgs("h", {
      name: "Handle",
      label: "h",
      steps,
      pre: "#!/bin/bash\necho hi",
      post: "",
      runImmediately: false,
      category: "worktree",
    })
    expect(args).toContain("--pre-execution")
    expect(args).toContain("--no-run-immediately")
    const idx = args.indexOf("--steps")
    expect(JSON.parse(args[idx + 1] ?? "")).toEqual([{ kind: "start", config: {} }])
  })
})

describe("run_immediately readers", () => {
  it("reads the flag from the row column", () => {
    expect(isRunImmediateAction({ run_immediately: true })).toBe(true)
    expect(isRunImmediateAction({ run_immediately: false })).toBe(false)
    expect(isRunImmediateAction({})).toBe(false)
  })

  it("reads the flag from the template row column", () => {
    expect(isRunImmediateTemplate({ run_immediately: true })).toBe(true)
    expect(isRunImmediateTemplate({ run_immediately: false })).toBe(false)
    expect(isRunImmediateTemplate({})).toBe(false)
  })
})
describe("immediate run args", () => {
  it("builds message action run argv with the standard confirm", () => {
    expect(buildImmediateMessageActionArgs("triage", "p1")).toEqual(
      ["action", "run", "triage", "p1", "--yes"],
    )
  })

  it("builds message flow run argv with a required worktree ref", () => {
    expect(buildImmediateFlowArgs("handle", "p1", "jira:X")).toEqual(
      ["flow", "run", "handle", "p1", "--ref", "jira:X", "--yes"],
    )
  })

  it("passes the seeded validate prompt through the validate tab", () => {
    expect(
      buildMessageArgs("validate", {
        ref: "p1",
        prompt: VALIDATE_CLAIMED_ISSUES_PROMPT,
        runMode: "preview",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["validate", "p1", "--prompt", VALIDATE_CLAIMED_ISSUES_PROMPT, "--yes"])
  })
})

describe("worktree-scope immediate run args", () => {
  it("builds action run argv per ref with the selected run mode", () => {
    expect(buildImmediateActionArgs("triage", "jira:X", "headless")).toEqual(
      ["run", "triage", "jira:X", "--launch", "--yes"],
    )
    expect(buildImmediateActionArgs("triage", "jira:X", "terminal")).toEqual(
      ["run", "triage", "jira:X", "--terminal", "--yes"],
    )
    expect(buildImmediateActionArgs("triage", "jira:X", "preview")).toEqual(
      ["run", "triage", "jira:X", "--yes"],
    )
  })

  it("builds worktree flow run argv", () => {
    expect(buildImmediateWorktreeFlowArgs("handle", "jira:X")).toEqual(
      ["run", "handle", "jira:X", "--yes"],
    )
  })

  it("splits a multi-ref input on whitespace and commas", () => {
    expect(splitRefs(" a, b\n c ")).toEqual(["a", "b", "c"])
    expect(splitRefs("   ")).toEqual([])
  })
})
