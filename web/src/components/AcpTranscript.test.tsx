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

describe("AcpTranscript user override (risk 4 RED)", () => {
  it("keeps a manually expanded completed tool open across re-renders", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    const lines = [
      { seq: 1, text: 'ACP:{"t":"tool_call","id":"t1","name":"read","kind":"read","status":"completed","output":"ok"}' },
    ]
    const { container, rerender } = render(<AcpTranscript lines={lines} />)
    const details = container.querySelector("details")!
    expect(details.open).toBe(false)
    await user.click(details.querySelector("summary")!)
    expect(details.open).toBe(true)
    // Status unchanged re-render must not snap it back shut.
    rerender(<AcpTranscript lines={lines} />)
    expect(container.querySelector("details")!.open).toBe(true)
  })
})

describe("AcpTranscript user override snap-back (risk 4 RED)", () => {
  it("does not snap a manually collapsed failed tool back open on re-render", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    const lines = [
      { seq: 1, text: 'ACP:{"t":"tool_call","id":"t9","name":"bash","kind":"execute","status":"failed","output":"boom"}' },
    ]
    const { container, rerender } = render(<AcpTranscript lines={lines} />)
    const details = () => container.querySelector("details")!
    expect(details().open).toBe(true)
    await user.click(details().querySelector("summary")!)
    expect(details().open).toBe(false)
    rerender(<AcpTranscript lines={lines} />)
    expect(details().open).toBe(false)
  })

  it("toggles two id-less tool_call blocks with the same name independently", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    const lines = [
      { seq: 1, text: 'ACP:{"t":"tool_call","name":"read","kind":"read","status":"completed","output":"first"}' },
      { seq: 2, text: 'ACP:{"t":"tool_call","name":"read","kind":"read","status":"completed","output":"second"}' },
    ]
    const { container } = render(<AcpTranscript lines={lines} />)
    const details = Array.from(container.querySelectorAll("details"))
    expect(details).toHaveLength(2)
    expect(details[0]!.open).toBe(false)
    expect(details[1]!.open).toBe(false)
    await user.click(details[0]!.querySelector("summary")!)
    expect(details[0]!.open).toBe(true)
    expect(details[1]!.open).toBe(false)
  })
})
