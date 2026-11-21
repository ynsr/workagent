import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen, fireEvent } from "@testing-library/react"
import { FlowStepper } from "./FlowStepper"
import type { FlowRow } from "@/lib/api"

afterEach(cleanup)

function makeFlow(overrides: Partial<FlowRow> = {}): FlowRow {
  return {
    id: 1,
    template_label: "ship",
    worktree_ref: "jira:IPG-1",
    repo_snapshot: {},
    status: "running",
    current_step: 1,
    effective_steps: [
      { kind: "start" },
      { kind: "review" },
      { kind: "fix_comments" },
      { kind: "cleanup" },
    ],
    step_results: [{ kind: "start", status: "ok", detail: "" }],
    created_at: "2026-01-01T00:00:00Z",
    modified_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

describe("FlowStepper", () => {
  it("renders step labels and status glyphs for a running flow", () => {
    render(<FlowStepper flow={makeFlow()} />)
    expect(screen.getByText("Start")).toBeTruthy()
    expect(screen.getByText("Review")).toBeTruthy()
    expect(screen.getByText("Fix")).toBeTruthy()
    expect(screen.getByText("Cleanup")).toBeTruthy()
    expect(screen.getByText("(completed)")).toBeTruthy()
    expect(screen.getByText("(running)")).toBeTruthy()
    expect(screen.getAllByText("(pending)").length).toBe(2)
  })

  it("shows inline Resume and Skip step buttons when paused and calls onResume", () => {
    const onResume = vi.fn()
    const flow = makeFlow({
      status: "paused",
      current_step: 1,
      step_results: [
        { kind: "start", status: "ok", detail: "" },
        { kind: "review", status: "paused", detail: "needs human" },
      ],
    })
    render(<FlowStepper flow={flow} onResume={onResume} />)
    fireEvent.click(screen.getByRole("button", { name: "Resume" }))
    expect(onResume).toHaveBeenCalledWith(flow, false)
    fireEvent.click(screen.getByRole("button", { name: "Skip step" }))
    expect(onResume).toHaveBeenCalledWith(flow, true)
  })

  it("hides Resume/Skip buttons in compact mode", () => {
    const onResume = vi.fn()
    render(<FlowStepper flow={makeFlow({ status: "paused" })} compact onResume={onResume} />)
    expect(screen.queryByRole("button", { name: "Resume" })).toBeNull()
  })
})
