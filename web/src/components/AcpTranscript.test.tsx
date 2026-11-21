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

describe("AcpTranscript ActivityRun folding & FailedCalls (T05.2)", () => {
  it("folds 3+ exploratory calls into an ActivityRun bar, extracts FailedCalls unfolded, and preserves sticky overrides", async () => {
    const { default: userEvent } = await import("@testing-library/user-event")
    const user = userEvent.setup()
    const lines = [
      { seq: 1, text: 'ACP:{"t":"tool_call","id":"c1","name":"read","kind":"read","status":"completed","output":"a"}' },
      { seq: 2, text: 'ACP:{"t":"tool_call","id":"c2","name":"grep","kind":"search","status":"completed","output":"b"}' },
      { seq: 3, text: 'ACP:{"t":"tool_call","id":"c3","name":"bash","kind":"execute","status":"failed","args":{"command":"ls missing"},"output":"not found"}' },
      { seq: 4, text: 'ACP:{"t":"tool_call","id":"c4","name":"read","kind":"read","status":"completed","output":"d"}' },
    ]
    const { rerender } = render(<AcpTranscript lines={lines} />)

    const activityRun = screen.getByTestId("activity-run") as HTMLDetailsElement
    expect(activityRun.open).toBe(false)
    expect(activityRun.textContent).toContain("Explored 2 files, 1 search, 1 command")
    expect(activityRun.textContent).toContain("1 failed")

    // Failed call is extracted unfolded immediately below the ActivityRun bar
    const failedCalls = screen.getByTestId("failed-calls")
    const failedDetails = failedCalls.querySelector("details")!
    expect(failedDetails.open).toBe(true)
    expect(failedDetails.textContent).toContain("not found")

    // Sticky user overrides survive live updates appending another exploratory call
    await user.click(activityRun.querySelector("summary")!)
    expect(activityRun.open).toBe(true)
    await user.click(failedDetails.querySelector("summary")!)
    expect(failedDetails.open).toBe(false)

    const updatedLines = [
      ...lines,
      { seq: 5, text: 'ACP:{"t":"tool_call","id":"c5","name":"read","kind":"read","status":"completed","output":"e"}' },
    ]
    rerender(<AcpTranscript lines={updatedLines} />)
    expect((screen.getByTestId("activity-run") as HTMLDetailsElement).open).toBe(true)
    expect(screen.getByTestId("activity-run").textContent).toContain("Explored 3 files, 1 search, 1 command")
    expect(screen.getByTestId("failed-calls").querySelector("details")!.open).toBe(false)
  })
})

describe("AcpTranscript settled turn summary header (T05.3)", () => {
  it("renders duration, model, and effort above completed turns and omits it while running", () => {
    const runningLines = [{ seq: 1, text: 'ACP:{"t":"message","text":"working"}' }]
    const { rerender } = render(
      <AcpTranscript
        lines={runningLines}
        runMeta={{
          created: 1_700_000_000,
          finished: null,
          state: "running",
          args: ["start", "--model", "claude-sonnet-4", "--effort", "high"],
        }}
      />,
    )
    expect(screen.queryByTestId("turn-summary-header")).not.toBeInTheDocument()

    const settledLines = [
      ...runningLines,
      { seq: 2, text: 'ACP:{"t":"turn_end"}' },
    ]
    rerender(
      <AcpTranscript
        lines={settledLines}
        runMeta={{
          created: 1_700_000_000,
          finished: 1_700_000_075,
          state: "succeeded",
          args: ["start", "--model", "claude-sonnet-4", "--effort", "high"],
        }}
      />,
    )
    const header = screen.getByTestId("turn-summary-header")
    expect(header.textContent).toBe("Worked for 1m 15s · claude-sonnet-4 (high)")
  })
})
