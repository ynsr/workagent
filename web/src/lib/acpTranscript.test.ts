import { describe, expect, it } from "vitest"
import { parseAcpBlocks } from "@/lib/harness"

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
