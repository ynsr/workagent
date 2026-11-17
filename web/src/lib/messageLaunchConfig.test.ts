import { describe, expect, it } from "vitest"
import { MESSAGE_LAUNCH_INITIAL, MESSAGE_LAUNCH_MODES, VALIDATE_CLAIMED_ISSUES_PROMPT, buildMessageArgs, extractMessageId, isRegisteredMessageRef, messageLaunchValid } from "@/lib/messageLaunchConfig"

describe("messageLaunchConfig modes", () => {
  it("exposes the five message tabs including draft-reply", () => {
    expect(MESSAGE_LAUNCH_MODES).toEqual(["adhoc", "validate", "draft-reply", "action", "flow"])
  })

  it("seeds the validate prompt with claimed-issues copy", () => {
    expect(VALIDATE_CLAIMED_ISSUES_PROMPT).toMatch(/claimed issues/i)
    expect(VALIDATE_CLAIMED_ISSUES_PROMPT).toMatch(/jira/i)
  })
})

describe("message ref helpers", () => {
  const MID = "swqrz9mfitdqmq7uaxtet7gb1h"
  it("extracts a bare id and permalink tails", () => {
    expect(extractMessageId(MID)).toBe(MID)
    expect(extractMessageId(`https://chat.jibit.cloud/jibit/pl/${MID}`)).toBe(MID)
    expect(extractMessageId(`https://chat.jibit.cloud/jibit/pl/${MID}?x=1#y`)).toBe(MID)
  })
  it("rejects non-id refs", () => {
    expect(extractMessageId("p1")).toBe("")
    expect(extractMessageId("https://chat.jibit.cloud/jibit/pl/nope")).toBe("")
  })
  it("matches permalinks to registered ids and threads to raw rows", () => {
    expect(isRegisteredMessageRef(`https://x/pl/${MID}`, [MID])).toBe(true)
    expect(isRegisteredMessageRef("thread-1", [MID, "thread-1"])).toBe(true)
    expect(isRegisteredMessageRef(`https://x/pl/${MID}`, ["other"])).toBe(false)
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

  it("forwards extra prompt on the draft-reply tab", () => {
    expect(
      buildMessageArgs("draft-reply", {
        ref: "p1",
        extraPrompt: "audience: backend team",
        runMode: "headless",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["draft-reply", "p1", "--extra-prompt", "audience: backend team", "--launch", "--yes"])
  })

  it("omits extra prompt when blank on the draft-reply tab", () => {
    expect(
      buildMessageArgs("draft-reply", {
        ref: "p1",
        extraPrompt: "   ",
        runMode: "headless",
        refOverride: "",
        repo: "",
      }),
    ).toEqual(["draft-reply", "p1", "--launch", "--yes"])
  })
})

describe("messageLaunchValid", () => {
  it("requires a message ref on every tab", () => {
    expect(messageLaunchValid("validate", { ...MESSAGE_LAUNCH_INITIAL, ref: "  " })).toBe(false)
  })

  it("requires a prompt on adhoc and validate", () => {
    expect(messageLaunchValid("adhoc", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1" })).toBe(false)
    expect(messageLaunchValid("validate", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1" })).toBe(false)
    expect(messageLaunchValid("adhoc", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", prompt: "hi" })).toBe(true)
    expect(messageLaunchValid("validate", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", prompt: "hi" })).toBe(true)
  })

  it("requires a label on action/flow and a worktree on flow", () => {
    expect(messageLaunchValid("action", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1" })).toBe(false)
    expect(messageLaunchValid("flow", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", label: "h" })).toBe(false)
    expect(messageLaunchValid("action", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", label: "t" })).toBe(true)
    expect(messageLaunchValid("flow", { ...MESSAGE_LAUNCH_INITIAL, ref: "p1", label: "h", refOverride: "jira:X" })).toBe(true)
  })
})

describe("buildMessageArgs harness", () => {
  it("emits --harness across all five message tabs when selected", () => {
    expect(buildMessageArgs("adhoc", { ref: "m1", prompt: "hi", harness: "agy" })).toEqual(
      expect.arrayContaining(["--harness", "agy"]),
    )
    expect(buildMessageArgs("validate", { ref: "m1", prompt: "hi", harness: "omp" })).toEqual(
      expect.arrayContaining(["--harness", "omp"]),
    )
    expect(buildMessageArgs("draft-reply", { ref: "m1", harness: "agy" })).toEqual(
      expect.arrayContaining(["--harness", "agy"]),
    )
    expect(buildMessageArgs("action", { ref: "m1", label: "a1", harness: "omp" })).toEqual(
      expect.arrayContaining(["--harness", "omp"]),
    )
    expect(buildMessageArgs("flow", { ref: "m1", label: "f1", refOverride: "wt1", harness: "omp" })).toEqual(
      expect.arrayContaining(["--harness", "omp"]),
    )
  })

  it("omits --harness when blank", () => {
    expect(buildMessageArgs("adhoc", { ref: "m1", prompt: "hi", harness: "" })).not.toContain("--harness")
    expect(buildMessageArgs("flow", { ref: "m1", label: "f1", refOverride: "wt1", harness: "" })).not.toContain("--harness")
  })
})
