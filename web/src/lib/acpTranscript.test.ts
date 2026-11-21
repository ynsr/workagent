import { describe, expect, it } from "vitest"
import {
  extractModelEffort,
  formatActivityRunSummary,
  formatTurnDuration,
  groupActivityRuns,
  parseAcpBlocks,
  type AcpBlock,
} from "@/lib/harness"

describe("parseAcpBlocks pending questions", () => {
  it("parses a question block with options", () => {
    const blocks = parseAcpBlocks([
      { seq: 0, text: 'ACP: {"t": "question", "qid": "q1", "text": "proceed?", "options": ["yes", "no"], "multi": false}' },
    ])
    expect(blocks).toHaveLength(1)
    expect(blocks[0]?.kind).toBe("question")
    expect(blocks[0]?.qid).toBe("q1")
    expect(blocks[0]?.options).toEqual(["yes", "no"])
  })

  it("keeps non-ACP lines as raw", () => {
    const blocks = parseAcpBlocks([{ seq: 0, text: "plain log line" }])
    expect(blocks[0]?.kind).toBe("raw")
  })
})

describe("parseAcpBlocks coalescing and normalization", () => {
  it("coalesces consecutive message and thought chunks and skips malformed line 0", () => {
    const blocks = parseAcpBlocks([
      { seq: 0, text: 'ACP:{"t":"message","text":"trun' },
      { seq: 1, text: 'ACP:{"t":"thought","text":"step 1 "}' },
      { seq: 2, text: 'ACP:{"t":"thought","text":"step 2"}' },
      { seq: 3, text: 'ACP:{"t":"message","text":"Hello "}' },
      { seq: 4, text: 'ACP:{"t":"message","text":"world"}' },
    ])
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toMatchObject({ kind: "thought", text: "step 1 step 2" })
    expect(blocks[1]).toMatchObject({ kind: "message", text: "Hello world" })
  })

  it("merges tool_call updates by id, formats usage, and normalizes {label} options", () => {
    const blocks = parseAcpBlocks([
      { seq: 1, text: 'ACP:{"t":"tool_call","id":"tc1","name":"read","kind":"read","status":"running","args":{"path":"a.py"},"output":""}' },
      { seq: 2, text: 'ACP:{"t":"tool_call","id":"tc1","name":"read","kind":"read","status":"completed","output":"file body"}' },
      { seq: 3, text: 'ACP:{"t":"usage","usage":{"inputTokens":120,"outputTokens":45}}' },
      { seq: 4, text: 'ACP:{"t":"question","qid":"q1","text":"Pick one","options":[{"label":"Yes"},{"label":"No"}]}' },
    ])
    expect(blocks).toHaveLength(3)
    expect(blocks[0]).toMatchObject({ kind: "tool_call", id: "tc1", status: "completed", output: "file body" })
    expect(blocks[1]!.kind).toBe("usage")
    expect(blocks[1]!.text).not.toContain('{"t":')
    expect(blocks[1]!.text).toContain("120")
    expect(blocks[2]).toMatchObject({ kind: "question", qid: "q1", options: ["Yes", "No"] })
  })
})

describe("parseAcpBlocks unknown event types (risk 2 RED)", () => {
  it("pushes unknown t as its own block instead of merging into a message", async () => {
    const { parseAcpBlocks } = await import("./harness")
    const blocks = parseAcpBlocks([
      { seq: 1, text: 'ACP:{"t":"message","text":"hello"}' },
      { seq: 2, text: 'ACP:{"t":"future_type","text":"from the future"}' },
    ])
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toMatchObject({ kind: "message", text: "hello" })
    expect(blocks[1]).toMatchObject({ kind: "message", text: "from the future" })
  })
})

describe("groupActivityRuns (T05.1)", () => {
  const tc = (overrides: Partial<AcpBlock>): AcpBlock => ({
    kind: "tool_call",
    text: "",
    status: "completed",
    ...overrides,
  })

  it("clusters 3+ consecutive exploratory calls into an activity_run with counts and failedCalls", () => {
    const blocks: AcpBlock[] = [
      tc({ id: "t1", name: "read", toolKind: "read" }),
      tc({ id: "t2", name: "grep", toolKind: "search" }),
      tc({ id: "t3", name: "bash", toolKind: "execute", args: { command: "git status" } }),
      tc({ id: "t4", name: "read", toolKind: "read", status: "failed", output: "ENOENT" }),
    ]
    const items = groupActivityRuns(blocks)
    expect(items).toHaveLength(1)
    const group = items[0]!
    expect(group.kind).toBe("activity_run")
    if (group.kind === "activity_run") {
      expect(group.id).toBe("activity-t1")
      expect(group.counts).toEqual({ reads: 2, searches: 1, commands: 1, edits: 0 })
      expect(group.calls).toHaveLength(4)
      expect(group.failedCalls).toHaveLength(1)
      expect(group.failedCalls[0]!.id).toBe("t4")
      expect(formatActivityRunSummary(group.counts)).toBe("Explored 2 files, 1 search, 1 command")
    }
  })

  it("leaves fewer than 3 consecutive exploratory calls ungrouped", () => {
    const blocks: AcpBlock[] = [
      tc({ id: "t1", name: "read", toolKind: "read" }),
      tc({ id: "t2", name: "grep", toolKind: "search" }),
      { kind: "message", text: "done" },
    ]
    const items = groupActivityRuns(blocks)
    expect(items).toHaveLength(3)
    expect(items.every((i) => i.kind !== "activity_run")).toBe(true)
  })

  it("breaks clusters on edit/write and mutating execute/bash calls", () => {
    const blocks: AcpBlock[] = [
      tc({ id: "t1", name: "read", toolKind: "read" }),
      tc({ id: "t2", name: "read", toolKind: "read" }),
      tc({ id: "t3", name: "bash", toolKind: "execute", args: { command: "rm -rf tmp/" } }),
      tc({ id: "t4", name: "read", toolKind: "read" }),
      tc({ id: "t5", name: "edit", toolKind: "edit" }),
    ]
    const items = groupActivityRuns(blocks)
    expect(items).toHaveLength(5)
    expect(items.every((i) => i.kind !== "activity_run")).toBe(true)
  })
})

describe("turn header helpers (T05.3)", () => {
  it("extracts --model and --effort from run args", () => {
    expect(extractModelEffort(["start", "--model", "claude-sonnet-4", "--effort=high"])).toEqual({
      model: "claude-sonnet-4",
      effort: "high",
    })
    expect(extractModelEffort(["--model=gpt-5"])).toEqual({ model: "gpt-5", effort: undefined })
    expect(extractModelEffort([])).toEqual({})
  })

  it("formats turn durations", () => {
    expect(formatTurnDuration(0)).toBe("<1s")
    expect(formatTurnDuration(42)).toBe("42s")
    expect(formatTurnDuration(75)).toBe("1m 15s")
    expect(formatTurnDuration(120)).toBe("2m")
    expect(formatTurnDuration(3660)).toBe("1h 1m")
  })
})
