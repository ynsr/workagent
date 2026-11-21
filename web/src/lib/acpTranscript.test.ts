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
