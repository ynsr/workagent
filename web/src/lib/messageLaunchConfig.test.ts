import { describe, expect, it } from "vitest"
import { MESSAGE_LAUNCH_MODES, MESSAGE_LAUNCH_INITIAL, buildMessageArgs, messageLaunchValid } from "@/lib/messageLaunchConfig"

describe("messageLaunchConfig modes", () => {
  it("exposes the five message tabs", () => {
    expect(MESSAGE_LAUNCH_MODES).toEqual(["adhoc", "validate", "draft-reply", "action", "flow"])
  })
})

describe("buildMessageArgs", () => {
  it("builds adhoc argv with prompt and headless launch", () => {
    expect(
      buildMessageArgs("adhoc", {
        ref: "p1",
        prompt: "summarize",
        runMode: "headless",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["adhoc", "p1", "--prompt", "summarize", "--launch", "--yes"])
  })

  it("builds validate argv without prompt when empty", () => {
    expect(
      buildMessageArgs("validate", {
        ref: "p1",
        prompt: "  ",
        runMode: "preview",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["validate", "p1", "--yes"])
  })

  it("builds draft-reply argv with text", () => {
    expect(
      buildMessageArgs("draft-reply", {
        ref: "p1",
        text: "ack",
        dryRun: true,
      }),
    ).toEqual(["draft-reply", "p1", "--text", "ack", "--dry-run"])
  })

  it("builds action run argv with terminal mode and ref override", () => {
    expect(
      buildMessageArgs("action", {
        ref: "p1",
        label: "triage",
        runMode: "terminal",
        refOverride: "jira:X",
        repo: "proj",
      }),
    ).toEqual(["action", "run", "triage", "p1", "--ref", "jira:X", "--repo", "proj", "--terminal", "--yes"])
  })

  it("builds flow run argv with required worktree ref", () => {
    expect(
      buildMessageArgs("flow", {
        ref: "p1",
        label: "handle",
        refOverride: "jira:X",
        repo: "",
      }),
    ).toEqual(["flow", "run", "handle", "p1", "--ref", "jira:X", "--yes"])
  })

  it("builds adhoc argv with terminal mode and no preview flag", () => {
    expect(
      buildMessageArgs("adhoc", {
        ref: "p1",
        prompt: "hi",
        runMode: "terminal",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["adhoc", "p1", "--prompt", "hi", "--terminal", "--yes"])
  })

  it("builds action argv with preview mode and no run flag", () => {
    expect(
      buildMessageArgs("action", {
        ref: "p1",
        label: "triage",
        runMode: "preview",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["action", "run", "triage", "p1", "--yes"])
  })

  it("builds draft-reply argv without dry-run by default", () => {
    expect(
      buildMessageArgs("draft-reply", { ref: "p1", text: "ack" }),
    ).toEqual(["draft-reply", "p1", "--text", "ack"])
  })
})

describe("messageLaunchValid", () => {
  it("requires a message ref on every tab", () => {
    expect(messageLaunchValid("validate", { ...MESSAGE_LAUNCH_INITIAL, ref: "  " })).toBe(false)
  })

  it("requires a prompt on adhoc and text on draft-reply", () => {
    expect(messageLaunchValid("adhoc", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1" })).toBe(false)
    expect(messageLaunchValid("draft-reply", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1" })).toBe(false)
    expect(messageLaunchValid("adhoc", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", prompt: "hi" })).toBe(true)
    expect(messageLaunchValid("draft-reply", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", text: "ack" })).toBe(true)
  })

  it("requires a label on action/flow and a worktree on flow", () => {
    expect(messageLaunchValid("action", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1" })).toBe(false)
    expect(messageLaunchValid("flow", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", label: "h" })).toBe(false)
    expect(messageLaunchValid("action", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", label: "t" })).toBe(true)
    expect(messageLaunchValid("flow", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", label: "h", refOverride: "jira:X" })).toBe(true)
  })
  })
