import { describe, expect, it } from "vitest"
import { resumeCommand } from "@/lib/format"

describe("resumeCommand", () => {
  it("formats omp resume command when harnessName is omp", () => {
    expect(resumeCommand("/home/x/wt", "/home/x/runs/omp/s.jsonl", "omp")).toBe(
      "cd '/home/x/wt' && omp --resume '/home/x/runs/omp/s.jsonl'",
    )
  })

  it("formats agy-guard conversation command when harnessName is agy", () => {
    expect(resumeCommand("/home/x/wt", "conv-1", "agy")).toBe(
      "cd '/home/x/wt' && agy-guard --conversation 'conv-1'",
    )
  })

  it("defaults to agy-guard when harnessName is empty or omitted", () => {
    expect(resumeCommand("/home/x/wt", "conv-2")).toBe(
      "cd '/home/x/wt' && agy-guard --conversation 'conv-2'",
    )
    expect(resumeCommand("/home/x/wt", "conv-2", "")).toBe(
      "cd '/home/x/wt' && agy-guard --conversation 'conv-2'",
    )
    expect(resumeCommand("/home/x/wt", "")).toBe(
      "cd '/home/x/wt' && agy-guard",
    )
  })

  it("escapes single quotes for POSIX shells", () => {
    expect(resumeCommand("/home/o'malley/wt", "conv'1", "agy")).toBe(
      "cd '/home/o'\\''malley/wt' && agy-guard --conversation 'conv'\\''1'",
    )
    expect(resumeCommand("/home/o'malley/wt", "/tmp/a'b.jsonl", "omp")).toBe(
      "cd '/home/o'\\''malley/wt' && omp --resume '/tmp/a'\\''b.jsonl'",
    )
  })
})
