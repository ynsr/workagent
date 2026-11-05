import { describe, expect, it } from "vitest"
import { adhocArgs, splitAdhocRefs } from "@/lib/launchConfig"

describe("splitAdhocRefs", () => {
  it("splits one-per-line refs", () => {
    expect(splitAdhocRefs("jira:A\njira:B\n")).toEqual(["jira:A", "jira:B"])
  })
  it("splits comma-separated refs and drops blanks", () => {
    expect(splitAdhocRefs(" jira:A, ,jira:B ,, ")).toEqual(["jira:A", "jira:B"])
  })
})

describe("adhocArgs", () => {
  it("emits --prompt plus --extra-prompt", () => {
    expect(adhocArgs({ adhocPrompt: " summarize ", extraPrompt: " be brief " })).toEqual([
      "--prompt=summarize",
      "--extra-prompt=be brief",
    ])
  })
  it("omits --extra-prompt when empty", () => {
    expect(adhocArgs({ adhocPrompt: "hi", extraPrompt: "  " })).toEqual(["--prompt=hi"])
  })
})
