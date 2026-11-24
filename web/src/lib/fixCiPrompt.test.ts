import { describe, expect, it } from "vitest"
import { buildFixCiPrompt, tailLogLines } from "@/lib/fixCiPrompt"
import type { PrCheckRow } from "@/lib/api"

function makeCheck(overrides: Partial<PrCheckRow> = {}): PrCheckRow {
  return {
    id: 1,
    worktree_ref: "jira:IPG-186",
    name: "test",
    commit_sha: "abc",
    status: "failed",
    conclusion: "failure",
    url: "https://git.jibit.cloud/server/projectx/-/jobs/203729",
    log_tail: "",
    updated_at: "2026-11-24T00:00:00Z",
    ...overrides,
  }
}

describe("tailLogLines", () => {
  it("keeps only the last 50 lines and trims trailing blanks", () => {
    const lines = Array.from({ length: 65 }, (_, i) => `line-${i + 1}`)
    const out = tailLogLines(`${lines.join("\n")}\n\n`)
    const split = out.split("\n")
    expect(split).toHaveLength(50)
    expect(split[0]).toBe("line-16")
    expect(split[49]).toBe("line-65")
  })
})

describe("buildFixCiPrompt", () => {
  it("includes failing job URL and last 50 lines of terminal log", () => {
    const log = Array.from({ length: 60 }, (_, i) => `err-${i + 1}`).join("\n")
    const prompt = buildFixCiPrompt([makeCheck({ log_tail: log })])
    expect(prompt).toContain("Investigate and fix the failing CI checks: test.")
    expect(prompt).toContain("URL: https://git.jibit.cloud/server/projectx/-/jobs/203729")
    expect(prompt).toContain("Last 50 lines of terminal log:")
    expect(prompt).not.toContain("err-10\n")
    expect(prompt).toContain("err-11")
    expect(prompt).toContain("err-60")
  })

  it("handles multiple failing checks and fallback CI URL", () => {
    const prompt = buildFixCiPrompt(
      [
        makeCheck({ id: 1, name: "pytest", url: "https://github.com/o/r/actions/runs/1/job/2", log_tail: "AssertionError" }),
        makeCheck({ id: 2, name: "lint", url: "", log_tail: "" }),
      ],
      "https://github.com/o/r/actions/runs/1",
    )
    expect(prompt).toContain("Investigate and fix the failing CI checks: pytest, lint.")
    expect(prompt).toContain("- pytest (failure) — URL: https://github.com/o/r/actions/runs/1/job/2")
    expect(prompt).toContain("AssertionError")
    expect(prompt).toContain("- lint (failure) — URL: https://github.com/o/r/actions/runs/1")
  })

  it("includes fallback CI URL when checks array is empty", () => {
    const prompt = buildFixCiPrompt([], "https://git.jibit.cloud/server/projectx/-/jobs/999")
    expect(prompt).toContain("Investigate and fix the failing CI checks on this branch.")
    expect(prompt).toContain("Failing CI job URL: https://git.jibit.cloud/server/projectx/-/jobs/999")
  })
})
