import { describe, expect, it } from "vitest"
import { buildReviewArgs } from "@/components/StartForm"
import { INITIAL, adhocArgs, splitAdhocRefs } from "@/lib/launchConfig"
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


describe("buildReviewArgs", () => {
  it("headless review sends --no-tty so the CLI returns and chains --then-fix-comments", () => {
    const args = buildReviewArgs({ ...INITIAL, ref: "https://github.com/o/r/pull/1", repo: "r", runMode: "headless", thenFixComments: true })
    expect(args).toContain("--no-tty")
    expect(args).toContain("--launch")
    expect(args).toContain("--then-fix-comments")
  })
  it("sends --use-review-session only with fix action or then-fix chain", () => {
    const plain = buildReviewArgs({ ...INITIAL, ref: "u", repo: "r", useReviewSession: true })
    expect(plain).not.toContain("--use-review-session")
    const chained = buildReviewArgs({ ...INITIAL, ref: "u", repo: "r", thenFixComments: true, useReviewSession: true })
    expect(chained).toContain("--then-fix-comments")
    expect(chained).toContain("--use-review-session")
  })
  it("fix action sends --fix-comments without --then-fix-comments", () => {
    const args = buildReviewArgs({ ...INITIAL, ref: "u", repo: "r", fixComments: true, thenFixComments: true })
    expect(args).toContain("--fix-comments")
    expect(args).not.toContain("--then-fix-comments")
  })
})
