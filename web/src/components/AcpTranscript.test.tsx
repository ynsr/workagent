import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { AcpTranscript } from "./AcpTranscript"

describe("AcpTranscript", () => {
  it("renders collapsed Thinking <details>, status-driven tool <details>, and Markdown messages", () => {
    const lines = [
      { seq: 1, text: 'ACP:{"t":"thought","text":"pondering"}' },
      { seq: 2, text: 'ACP:{"t":"tool_call","id":"t1","name":"read","kind":"read","status":"completed","output":"ok"}' },
      { seq: 3, text: 'ACP:{"t":"tool_call","id":"t2","name":"bash","kind":"execute","status":"failed","output":"boom"}' },
      { seq: 4, text: 'ACP:{"t":"message","text":"# Heading\\n\\n**bold** text"}' },
    ]
    const { container } = render(<AcpTranscript lines={lines} />)
    const details = Array.from(container.querySelectorAll("details"))
    expect(details).toHaveLength(3)
    // Thinking: collapsed
    expect(details[0]!.open).toBe(false)
    expect(details[0]!.textContent).toContain("Thinking")
    // Completed tool: collapsed
    expect(details[1]!.open).toBe(false)
    // Failed tool: expanded
    expect(details[2]!.open).toBe(true)
    // Markdown heading & bold rendered
    expect(screen.getByRole("heading", { name: "Heading" })).toBeInTheDocument()
    expect(container.querySelector("strong")?.textContent).toBe("bold")
  })
})
